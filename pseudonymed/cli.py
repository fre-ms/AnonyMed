"""Command line: pseudonymise, check, re-identify, list pseudonyms, show configuration."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Annotated

import typer

from pseudonymed import __version__, umgebung
from pseudonymed.ablauf import Ablaufoptionen, pseudonymisiere
from pseudonymed.dokument.laden import papierformat
from pseudonymed.dokument.ocr import OCRFehler, verfuegbare_backends
from pseudonymed.dokument.textdatei import ist_textdatei
from pseudonymed.erkennung.codes import verfuegbar as codes_verfuegbar
from pseudonymed.kern.modell import Patient
from pseudonymed.konfig import (
    UMGEBUNG_PASSWORT,
    CardDAVKonfig,
    konfigpfad,
    lade_einstellungen,
    lade_konfig,
    passwort_laden,
    passwort_speichern,
    speichere_konfig,
)
from pseudonymed.pruefung.bericht import als_text
from pseudonymed.pseudonyme.speicher import (
    SCHEMATA,
    Pseudonymspeicher,
    UnsichererAbgleich,
    einmalspeicher,
    schluesselpfad_zu,
    standardpfad,
)
from pseudonymed.quellen.carddav import CardDAVClient, CardDAVFehler, passt
from pseudonymed.schwaerzen.pdf import Schwaerzoptionen

app = typer.Typer(
    help="PseudonyMed – Patientendokumente lokal pseudonymisieren, Pseudonyme später zurücksetzen.",
    no_args_is_help=True,
    add_completion=False,
)

SpeicherOption = Annotated[
    Path | None,
    typer.Option("--speicher", help="Pfad der Pseudonym-Datenbank (Standard: Nutzerdatenordner)."),
]
KonfigOption = Annotated[
    Path | None,
    typer.Option("--konfig", help="Konfigurationsdatei (Standard: Nutzer-Konfigordner)."),
]
PatientOption = Annotated[
    Path | None,
    typer.Option("--patient", "-p", exists=True,
                 help="Patientendatei (JSON/TOML, siehe 'vorlage')."),
]
VcfOption = Annotated[
    Path | None, typer.Option("--vcf", exists=True, help="Lokale vCard-Datei (.vcf).")
]
KontaktOption = Annotated[
    str | None,
    typer.Option("--kontakt", "-k", help="Suchbegriff im CardDAV-Adressbuch (Name, Geburtsjahr)."),
]
NrOption = Annotated[int | None, typer.Option("--nr", help="Nummer des Treffers bei mehreren.")]
SchemaOption = Annotated[
    str, typer.Option("--schema", help="Pseudonymschema: 'hash' (Standard) oder 'buchstabe'.")
]
NeuOption = Annotated[
    bool, typer.Option("--neu", help="Bei unsicherem Abgleich als neue Person anlegen.")
]
GleichOption = Annotated[
    str | None,
    typer.Option("--gleich", help="Bei unsicherem Abgleich dieser Kennung zuordnen."),
]


def _speicher(pfad: Path | None, schema: str = "hash") -> Pseudonymspeicher:
    if schema not in SCHEMATA:
        typer.echo(f"--schema muss eines von {', '.join(SCHEMATA)} sein.", err=True)
        raise typer.Exit(code=1)
    return Pseudonymspeicher(pfad, schema=schema)


def _abgleich_entscheidung(neu: bool, gleich: str | None) -> str | None:
    if neu and gleich:
        typer.echo("Bitte nur --neu oder --gleich angeben.", err=True)
        raise typer.Exit(code=1)
    if neu:
        return "neu"
    return f"gleich:{gleich.strip().upper()}" if gleich else None


def _unsicher_melden(p: Patient, fehler: UnsichererAbgleich) -> None:
    typer.echo(
        f"Unsicherer Abgleich für {p.nachname}, {p.vorname}:"
        " ähnliche Einträge im Pseudonymspeicher:",
        err=True,
    )
    for k in fehler.kandidaten:
        felder = ", ".join(f"{n} {v:.2f}" for n, v in k.felder.items())
        geb = k.geburtsdatum or "–"
        typer.echo(f"  {k.kennung}  {k.nachname}, {k.vorname}  geb. {geb}  Score {k.score:.2f}"
                   f" ({felder})", err=True)
    typer.echo("Entscheiden mit --gleich KENNUNG (dieselbe Person) oder --neu (neue Person).",
               err=True)


def _patient_laden(pfad: Path) -> Patient:
    from pseudonymed.quellen.manuell import lade_patient

    try:
        return lade_patient(pfad)
    except (OSError, ValueError) as e:
        typer.echo(f"Patientendatei nicht lesbar: {e}", err=True)
        raise typer.Exit(code=1) from e


def _verbindung(konfig: Path | None) -> tuple[CardDAVClient, CardDAVKonfig]:
    k = lade_konfig(konfig)
    if not k.vollstaendig:
        typer.echo("CardDAV ist nicht eingerichtet: zuerst 'pseudonymed kontakte einrichten'.",
                   err=True)
        raise typer.Exit(code=1)
    passwort = passwort_laden(k)
    if not passwort:
        typer.echo(
            f"Kein App-Passwort für {k.kennung} gefunden (Schlüsselbund oder {UMGEBUNG_PASSWORT}).",
            err=True,
        )
        raise typer.Exit(code=1)
    try:
        return CardDAVClient(k.url, k.benutzer, passwort), k
    except CardDAVFehler as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from e


def _kontakte_suchen(begriff: str, konfig: Path | None) -> list[Patient]:
    """Local vCard book and CardDAV together; a source that fails is reported, and only
    when nothing at all is set up or every source failed the command stops."""
    from pseudonymed.quellen import adressbuch

    if not adressbuch.eingerichtet(konfig):
        typer.echo("Kein Adressbuch eingerichtet: 'pseudonymed kontakte einrichten' (Nextcloud)"
                   " oder 'pseudonymed kontakte lokal DATEI' (vCard-Datei oder Ordner).", err=True)
        raise typer.Exit(code=1)
    ergebnis = adressbuch.suche(begriff, konfig)
    for fehler in ergebnis.fehler:
        typer.echo(fehler, err=True)
    if ergebnis.fehler and len(ergebnis.fehler) == len(ergebnis.quellen):
        raise typer.Exit(code=1)
    return ergebnis.patienten


def _zeile(nr: int, p: Patient, kennung: str | None = None) -> str:
    geb = p.geburtsdatum.strftime("%d.%m.%Y") if p.geburtsdatum else "–"
    ort = " ".join(t for t in (p.plz, p.ort) if t)
    pseudonym = f"  → {'Patientin' if p.weiblich else 'Patient'} {kennung}" if kennung else ""
    return f"{nr:>3}  {p.nachname}, {p.vorname}  geb. {geb}  {ort}{pseudonym}"


def _auswahl(patienten: list[Patient], begriff: str, nr: int | None) -> Patient:
    if not patienten:
        typer.echo(f"Kein Kontakt passt zu {begriff!r}.", err=True)
        raise typer.Exit(code=1)
    if nr is not None:
        if 1 <= nr <= len(patienten):
            return patienten[nr - 1]
        typer.echo(f"--nr muss zwischen 1 und {len(patienten)} liegen.", err=True)
        raise typer.Exit(code=1)
    if len(patienten) == 1:
        return patienten[0]
    typer.echo("Mehrere Treffer, bitte mit --nr wählen:", err=True)
    for i, p in enumerate(patienten, 1):
        typer.echo(_zeile(i, p), err=True)
    raise typer.Exit(code=1)


def _patient_bestimmen(
    patient: Path | None, vcf: Path | None, kontakt: str | None, nr: int | None,
    konfig: Path | None,
) -> Patient:
    """Exactly one source: patient file, local vCard file (optionally filtered) or CardDAV."""
    from pseudonymed.quellen.vcf import patienten_aus_vcf

    if patient is not None and (vcf is not None or kontakt):
        typer.echo("Bitte nur eine Quelle angeben: --patient, --vcf oder --kontakt.", err=True)
        raise typer.Exit(code=1)
    if patient is not None:
        return _patient_laden(patient)
    if vcf is not None:
        kandidaten = [p for p in patienten_aus_vcf(vcf) if p.nachname or p.vorname]
        if kontakt:
            kandidaten = [p for p in kandidaten if passt(p, kontakt)]
        return _auswahl(kandidaten, kontakt or vcf.name, nr)
    if kontakt:
        return _auswahl(_kontakte_suchen(kontakt, konfig), kontakt, nr)
    typer.echo("Bitte --patient, --vcf oder --kontakt angeben.", err=True)
    raise typer.Exit(code=1)


@app.callback()
def _hauptbefehl() -> None:
    """PseudonyMed: lokale Pseudonymisierung mit stabilen Pseudonymen."""


@app.command()
def pseudonymisieren(
    dateien: Annotated[list[Path], typer.Argument(
        help="PDF-, Bild- oder Textdateien (.txt, .md, .srt, .vtt, Whisper-.json, .eml).",
        exists=True)],
    patient: PatientOption = None,
    vcf: VcfOption = None,
    kontakt: KontaktOption = None,
    nr: NrOption = None,
    konfig: KonfigOption = None,
    ziel: Annotated[Path | None, typer.Option("--ziel", "-z", help="Zielordner.")] = None,
    datum: Annotated[str, typer.Option("--datum", help="Geburtsdatum als 'jahr' oder 'alter'.")]
    = "jahr",
    rastern: Annotated[bool, typer.Option("--rastern", help="Ausgabe vollständig rastern.")]
    = False,
    dpi: Annotated[int, typer.Option("--dpi", help="Auflösung beim Rastern.")] = 200,
    textdatei: Annotated[bool, typer.Option("--text/--kein-text",
                                            help="Textdatei in Lesereihenfolge schreiben.")] = True,
    markdown: Annotated[bool, typer.Option("--markdown/--kein-markdown",
                                           help="Zusätzlich eine Markdown-Datei schreiben.")]
    = False,
    textebene: Annotated[bool, typer.Option(
        "--textebene/--keine-textebene",
        help="OCR-Text unsichtbar in Scans einbetten (durchsuchbares PDF).")] = True,
    bericht: Annotated[bool, typer.Option("--bericht/--kein-bericht",
                                          help="Prüfbericht als JSON schreiben.")] = True,
    vermerk: Annotated[bool, typer.Option("--vermerk/--ohne-vermerk",
                                          help="Vermerk in den PDF-Metadaten.")] = True,
    speicher: SpeicherOption = None,
    schema: SchemaOption = "hash",
    neu: NeuOption = False,
    gleich: GleichOption = None,
    ocr: Annotated[str, typer.Option("--ocr", help="OCR für Scans/Fotos: auto, rapidocr,"
                                                  " tesseract oder aus.")] = "auto",
    codes: Annotated[bool, typer.Option("--codes/--keine-codes",
                                        help="Barcodes und QR-Codes schwärzen.")] = True,
    vorschau: Annotated[str, typer.Option("--vorschau", help="Vorschaubilder: auto (nur bei OCR),"
                                                          " immer oder nie.")] = "auto",
    unlesbar: Annotated[str, typer.Option(
        "--unlesbar", help="Von der OCR nicht lesbare Zeilen: schwaerzen oder lassen.")]
    = "schwaerzen",
    ner: Annotated[bool, typer.Option("--ner/--kein-ner", help="Unbekannte Personen und Orte"
                                                              " per NER suchen (Extra 'ner').")]
    = False,
    fachbegriffe: Annotated[bool, typer.Option(
        "--fachbegriffe/--keine-fachbegriffe",
        help="Medizinische Fachbegriffe vor NER und unscharfer OCR-Suche schützen.")] = True,
    identifikatoren: Annotated[bool, typer.Option(
        "--identifikatoren/--keine-identifikatoren",
        help="Berufe, Kliniken, Studien und Titel schwärzen (Tabelle 'identifikatoren').")]
    = True,
    unscharf: Annotated[bool, typer.Option(
        "--unscharf/--genau",
        help="Textdateien: abweichend geschriebene Namen unscharf suchen (Transkripte).")] = True,
    aufbereiten: Annotated[str, typer.Option(
        "--aufbereiten", help="Scans und Fotos vor der OCR aufbereiten (begradigen, Hintergrund,"
                              " Hochskalieren): auto oder aus.")] = "auto",
    papier: Annotated[str, typer.Option(
        "--papier", help="Seitengröße für Fotos und Bilder: a4, a5, a3, letter, legal oder bild"
                         " (so groß wie das Bild).")] = "a4",
    tabellen: Annotated[bool, typer.Option(
        "--tabellen/--keine-tabellen",
        help="Tesseract: Tabellengitter erkennen und die Zellen ohne Linien erneut lesen.")]
    = True,
    vokabular: Annotated[bool, typer.Option(
        "--vokabular/--kein-vokabular",
        help="Verworfene Rate-Treffer als <name>_pseudonym.vokabular.txt schreiben.")] = True,
    gliedern: Annotated[bool, typer.Option(
        "--gliedern/--roh",
        help="OCR-Text als Markdown gliedern: Absätze zusammenfügen, Überschriften, Listen,"
             " Tabellen.")] = True,
    referenzpopulation: Annotated[int | None, typer.Option(
        "--referenzpopulation",
        help="Restrisiko: aus wie vielen Menschen ein Angreifer wählt (Standard aus der"
             " Konfiguration, sonst 83.600.000 für Deutschland).")] = None,
    eigene_orte: Annotated[str | None, typer.Option(
        "--eigene-orte", help="Restrisiko: Orte des Absenders (Sprechstunde, Klinik), die nicht"
                              " als Patientenmerkmal zählen; Komma-getrennt.")] = None,
    empfaenger: Annotated[str | None, typer.Option(
        "--empfaenger",
        help="Öffentlicher OpenPGP-Schlüssel des Empfängers (Datei oder https-Adresse):"
             " Pseudonyme mit Einmalschlüssel, Zuordnung als verschlüsselte Kapsel"
             " <name>_pseudonym.kapsel.asc für den Empfänger; lokal wird nichts gespeichert.")]
    = None,
) -> None:
    """Dokumente und Texte schwärzen, Pseudonyme einsetzen, Gegenprobe, Bericht."""
    zertifikat = None
    if empfaenger:
        from pseudonymed.pseudonyme import pgp as pgpmodul

        try:
            zertifikat = pgpmodul.lade_zertifikat(empfaenger)
        except pgpmodul.PGPFehler as e:
            typer.echo(f"Empfänger-Schlüssel: {e}", err=True)
            raise typer.Exit(code=1) from e
        typer.echo(f"Empfänger: {zertifikat.beschreibung()}", err=True)
    try:
        papierformat(papier)
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from e
    if unlesbar not in ("schwaerzen", "lassen"):
        typer.echo("--unlesbar muss 'schwaerzen' oder 'lassen' sein.", err=True)
        raise typer.Exit(code=1)
    if datum not in ("jahr", "alter"):
        typer.echo("--datum muss 'jahr' oder 'alter' sein.", err=True)
        raise typer.Exit(code=1)
    if vorschau not in ("auto", "immer", "nie"):
        typer.echo("--vorschau muss 'auto', 'immer' oder 'nie' sein.", err=True)
        raise typer.Exit(code=1)
    if aufbereiten not in ("auto", "aus"):
        typer.echo("--aufbereiten muss 'auto' oder 'aus' sein.", err=True)
        raise typer.Exit(code=1)
    p = _patient_bestimmen(patient, vcf, kontakt, nr, konfig)
    if p.hinweise:
        typer.echo(
            "Hinweis: Die Kontaktkarte enthält Notizen. Angehörige, Zuweiser oder Begriffe daraus"
            " werden nicht automatisch geschwärzt; bei Bedarf 'kontakte export' nutzen und die"
            " Patientendatei ergänzen.", err=True,
        )
    opt = Ablaufoptionen(
        datumsform=datum, textdatei=textdatei, markdown=markdown, bericht=bericht,
        ziel_ordner=ziel,
        abgleich=_abgleich_entscheidung(neu, gleich), ocr=ocr, codes=codes, vorschau=vorschau,
        unlesbar=unlesbar, ner=ner, fachbegriffe=fachbegriffe, identifikatoren=identifikatoren,
        unscharf_text=unscharf,
        aufbereiten=aufbereiten, papierformat=papier, ocr_tabellen=tabellen,
        vokabular=vokabular, gliedern=gliedern,
        referenzpopulation=referenzpopulation or lade_einstellungen().referenzpopulation,
        eigene_orte=eigene_orte if eigene_orte is not None else lade_einstellungen().eigene_orte,
        empfaenger=zertifikat.armor if zertifikat else "",
        empfaenger_fingerabdruck=zertifikat.fingerabdruck if zertifikat else "",
        schwaerzen=Schwaerzoptionen(rastern=rastern, dpi=dpi, vermerk=vermerk,
                                    textebene=textebene),
    )
    alles_frei = True
    with (einmalspeicher(schema) if zertifikat else _speicher(speicher, schema)) as sp:
        for datei in dateien:
            try:
                erg = pseudonymisiere(datei, p, opt, speicher=sp)
            except UnsichererAbgleich as e:
                _unsicher_melden(p, e)
                raise typer.Exit(code=4) from e
            except (ValueError, NotImplementedError, OCRFehler) as e:
                typer.echo(f"{datei.name}: {e}", err=True)
                alles_frei = False
                continue
            typer.echo(als_text(erg.bericht))
            if erg.vorschau:
                typer.echo(f"Vorschau:    {erg.vorschau[0].parent} ({len(erg.vorschau)} Seiten)")
            if erg.freigegeben:
                typer.echo(f"Freigegeben: {erg.ziel}")
                if erg.ziel_kapsel:
                    typer.echo(f"Kapsel:      {erg.ziel_kapsel}  (nur an den Empfänger geben)")
                if erg.ziel_txt and not erg.ist_text:
                    typer.echo(f"Text:        {erg.ziel_txt}")
                if erg.ziel_md:
                    typer.echo(f"Markdown:    {erg.ziel_md}")
            else:
                alles_frei = False
                typer.echo(f"NICHT freigegeben: {datei.name}", err=True)
            if erg.ziel_bericht:
                typer.echo(f"Bericht:     {erg.ziel_bericht}")
            wort = "Patientin" if p.weiblich else "Patient"
            typer.echo(f"Pseudonym der Hauptperson: {wort} {erg.kennung}\n")
    if not alles_frei:
        raise typer.Exit(code=2)


# the command's name until November 2026, kept for existing scripts
app.command("anonymisieren", hidden=True)(pseudonymisieren)


@app.command()
def pruefen(
    datei: Annotated[Path, typer.Argument(exists=True,
                                          help="Bereits pseudonymisierte PDF- oder Textdatei.")],
    patient: PatientOption = None,
    vcf: VcfOption = None,
    kontakt: KontaktOption = None,
    nr: NrOption = None,
    konfig: KonfigOption = None,
    speicher: SpeicherOption = None,
    schema: SchemaOption = "hash",
    neu: NeuOption = False,
    gleich: GleichOption = None,
) -> None:
    """Gegenprobe an einer vorhandenen Datei: sind noch Identifikatoren enthalten?"""
    import pymupdf

    from pseudonymed.erkennung.deterministisch import regeln_fuer
    from pseudonymed.pruefung.gegenprobe import pruefe
    from pseudonymed.pseudonyme.vergabe import Pseudonymvergabe

    p = _patient_bestimmen(patient, vcf, kontakt, nr, konfig)
    with _speicher(speicher, schema) as sp:
        try:
            kennung = sp.kennung_fuer(p, _abgleich_entscheidung(neu, gleich)).kennung
        except UnsichererAbgleich as e:
            _unsicher_melden(p, e)
            raise typer.Exit(code=4) from e
        geheimnis = sp.geheimnis
    regeln = regeln_fuer(p, Pseudonymvergabe(p, kennung, geheimnis=geheimnis))
    # the patient's own code may look like a file number ("AZ…"): never a leftover
    if ist_textdatei(datei):
        from pseudonymed.ablauf import gegenprobe_textdatei

        g = gegenprobe_textdatei(datei, regeln, pseudonyme=[kennung])
    else:
        with pymupdf.open(datei) as doc:
            g = pruefe(doc, regeln, pseudonyme=[kennung])
    if g.bestanden:
        typer.echo(f"{datei.name}: Gegenprobe bestanden.")
        return
    typer.echo(f"{datei.name}: Gegenprobe NICHT bestanden ({len(g.resttreffer)} Resttreffer).")
    for r in g.resttreffer[:50]:
        typer.echo(f"  Seite {r['seite']}: {r['regel']} ({r['quelle']})")
    for a in g.anmerkungen:
        typer.echo(f"  Anmerkung: {a}")
    raise typer.Exit(code=2)


@app.command()
def rueckersetzen(
    textdatei: Annotated[Path | None, typer.Argument(
        help="Text des Sprachmodells (sonst Standardeingabe).")] = None,
    kennung: Annotated[str | None, typer.Option(
        "--kennung", "-k", "--buchstabe", help="Pseudonym-Kennung, z. B. K7Q4MX oder A.")] = None,
    suche: Annotated[str | None, typer.Option("--suche", "-s",
                                              help="Namensteil des Patienten.")] = None,
    form: Annotated[str, typer.Option("--form", help="'vollname' oder 'anrede'.")] = "vollname",
    ausgabe: Annotated[Path | None, typer.Option("--ausgabe", "-o", help="Zieldatei.")] = None,
    speicher: SpeicherOption = None,
    kapsel: Annotated[Path | None, typer.Option(
        "--kapsel", exists=True,
        help="Schlüsselkapsel eines Absenders (<name>_pseudonym.kapsel.asc) statt der lokalen"
             " Datenbank.")] = None,
    privater_schluessel: Annotated[Path | None, typer.Option(
        "--privater-schluessel", exists=True,
        help="Privater OpenPGP-Schlüssel als Datei; ohne Angabe entschlüsselt GnuPG aus dem"
             " Schlüsselbund.")] = None,
    passphrase: Annotated[str | None, typer.Option(
        "--passphrase", envvar=umgebung.namen("PGP_PASSPHRASE"),
        help="Passphrase des privaten Schlüssels (sonst fragt der GnuPG-Agent).")] = None,
) -> None:
    """Pseudonyme im Text des Sprachmodells durch die Klardaten ersetzen (lokal)."""
    from pseudonymed.pseudonyme.rueckersetzung import rueckersetzen as _rueck

    if form not in ("vollname", "anrede"):
        typer.echo("--form muss 'vollname' oder 'anrede' sein.", err=True)
        raise typer.Exit(code=1)
    if kapsel is not None:
        from pseudonymed.pseudonyme import kapsel as kapselmodul
        from pseudonymed.pseudonyme.abgleich import abgleichen
        from pseudonymed.pseudonyme.pgp import PGPFehler

        try:
            k = kapselmodul.oeffne(kapsel, privater_schluessel, passphrase)
        except (PGPFehler, kapselmodul.KapselFehler, OSError) as e:
            typer.echo(f"Kapsel: {e}", err=True)
            raise typer.Exit(code=1) from e
        typer.echo(f"Kapsel: {k.beschreibung()}", err=True)
        info, zuordnung, bekannte = k.info(), k.zuordnungen, [k.kennung]
        try:
            with _speicher(speicher) as sp:
                gleiche = [v for v in abgleichen(k.als_patient(), sp.kandidaten()) if v.gleich]
        except OSError:
            gleiche = []
        if gleiche:
            typer.echo(f"Entspricht dem lokalen Eintrag {gleiche[0].kennung}"
                       f" ({gleiche[0].nachname}, {gleiche[0].vorname}).", err=True)
    else:
        if not kennung and not suche:
            typer.echo("Bitte --kennung, --suche oder --kapsel angeben.", err=True)
            raise typer.Exit(code=1)
        with _speicher(speicher) as sp:
            treffer = sp.finde(kennung_=kennung, suche=suche)
            if len(treffer) != 1:
                typer.echo(
                    f"{len(treffer)} Einträge gefunden; bitte eindeutig wählen"
                    " (siehe 'pseudonyme').",
                    err=True,
                )
                raise typer.Exit(code=1)
            info = treffer[0]
            zuordnung = sp.zuordnung(info["schluessel"])
            bekannte = sp.kennungen()
    text = textdatei.read_text(encoding="utf-8") if textdatei else sys.stdin.read()
    r = _rueck(text, zuordnung, info, form=form, bekannte_kennungen=bekannte)
    if ausgabe:
        ausgabe.write_text(r.text, encoding="utf-8")
        typer.echo(f"{r.ersetzt} Ersetzungen, geschrieben nach {ausgabe}", err=True)
    else:
        typer.echo(r.text, nl=False)
    if r.verbleibend:
        typer.echo("Nicht aufgelöst: " + ", ".join(r.verbleibend), err=True)
        for rest, hinweis in r.hinweise.items():
            typer.echo(f"  {rest}: {hinweis}", err=True)
        raise typer.Exit(code=3)


@app.command()
def pseudonyme(speicher: SpeicherOption = None) -> None:
    """Vergebene Kennungen auflisten, mit verknüpften Schreibweisen (nur lokal)."""
    with _speicher(speicher) as sp:
        eintraege = sp.finde()
        aliasse = {e["schluessel"]: sp.aliasse(e["schluessel"]) for e in eintraege}
    if not eintraege:
        typer.echo("Noch keine Pseudonyme vergeben.")
        return
    for e in eintraege:
        geb = e["geburtsdatum"] or "–"
        typer.echo(f"{e['kennung']:>8}  {e['nachname']}, {e['vorname']}  geb. {geb}"
                   f"  ({e['angelegt'][:10]})")
        for a in aliasse[e["schluessel"]]:
            typer.echo(f"{'':8}  = {a['nachname']}, {a['vorname']}  geb. {a['geburtsdatum'] or '–'}"
                       f"  (Abgleich {a['score']:.2f})")


@app.command()
def gui() -> None:
    """Grafische Oberfläche starten (Extra 'gui')."""
    from pseudonymed.gui.app import main

    raise typer.Exit(code=main([]))


@app.command()
def vorlage() -> None:
    """JSON-Vorlage für eine Patientendatei ausgeben."""
    from pseudonymed.quellen.manuell import vorlage as _vorlage

    typer.echo(_vorlage(), nl=False)


@app.command()
def konfig(speicher: SpeicherOption = None) -> None:
    """Version und Pfade anzeigen."""
    import pymupdf

    typer.echo(f"PseudonyMed {__version__}")
    typer.echo(f"PyMuPDF {pymupdf.version[0]} (MuPDF {pymupdf.version[1]})")
    from pseudonymed.dokument.ocr import tesseract_programm, tesseract_sprachen
    from pseudonymed.konfig import lade_einstellungen

    backends = verfuegbare_backends()
    einstellungen = lade_einstellungen()
    typer.echo("OCR-Backends:        " + (", ".join(backends) if backends else
                                          "keine (Extra 'ocr' installieren oder Tesseract)")
               + f"  (bevorzugt: {einstellungen.ocr_backend})")
    tess = tesseract_programm(einstellungen.tesseract_pfad or None)
    if tess:
        sprachen = tesseract_sprachen(tess)
        relevante = [s for s in ("deu", "eng") if s in sprachen]
        sprachtext = ", ".join(relevante) or "deu/eng fehlen"
        typer.echo(f"Tesseract:           {tess}  (Sprachen: {sprachtext},"
                   f" insgesamt {len(sprachen)})")
    typer.echo("Barcodes/QR:         " + ("zxing-cpp" if codes_verfuegbar() else
                                          "nicht verfügbar (Extra 'codes')"))
    from pseudonymed.erkennung import ner as nermodul

    typer.echo("NER:                 " + (f"spaCy mit {nermodul.verfuegbares_modell()}"
                                          if nermodul.verfuegbar() else
                                          "nicht verfügbar (Extra 'ner' + deutsches Modell)"))
    from pseudonymed.erkennung import fachbegriffe as fachmodul

    fb = fachmodul.standard()
    quellen = ", ".join(f"{name} {n}" for name, n in fb.quellen)
    typer.echo(f"Fachbegriffe:        {fb.anzahl} geschützte Begriffe ({quellen});"
               f" eigene Listen in {fachmodul.datenordner()}")
    from pseudonymed.konfig import schluesselbund
    from pseudonymed.pseudonyme.speicher import standard_schluesselpfad

    typer.echo(f"Pseudonym-Datenbank: {speicher or standardpfad()}")
    schluessel = schluesselpfad_zu(speicher) if speicher else standard_schluesselpfad()
    typer.echo(f"Pseudonymschlüssel:  {schluessel}"
               "  (sichern; gleicher Schlüssel = gleiche Kennungen auf jedem Rechner)")
    typer.echo("Schlüsselbund:       " + (schluesselbund() or "keiner (Extra 'carddav')"))
    if einstellungen.adressbuch_lokal:
        typer.echo(f"Lokales Adressbuch:  {einstellungen.adressbuch_lokal}")
    from pseudonymed.pseudonyme import pgp as pgpmodul

    typer.echo("OpenPGP:             " + (", ".join(pgpmodul.engines()) or
                                         "nicht verfügbar (Extra 'pgp' oder GnuPG)"))
    if einstellungen.empfaenger_schluessel:
        typer.echo(f"Empfänger-Schlüssel: {einstellungen.empfaenger_schluessel}"
                   f" (Fingerabdruck {einstellungen.empfaenger_fingerabdruck or 'ungeprüft'})")
    k = lade_konfig()
    typer.echo(f"Konfiguration:       {konfigpfad()}")
    if k.vollstaendig:
        buch = k.adressbuch or "einziges Adressbuch"
        typer.echo(f"CardDAV:             {k.benutzer} @ {k.url} ({buch})")
    else:
        typer.echo("CardDAV:             nicht eingerichtet")


datenbank_app = typer.Typer(
    help="Pseudonym-Datenbank und Schlüssel: Statistik, Sicherung, Wiederherstellung.",
    no_args_is_help=True,
)
app.add_typer(datenbank_app, name="datenbank")


@datenbank_app.command("status")
def datenbank_status(speicher: SpeicherOption = None) -> None:
    """Was gespeichert ist: Zuordnungen je Kategorie, Größe, Integrität, Schlüssel."""
    from pseudonymed.pseudonyme import sicherung as sichmodul

    st = sichmodul.statistik(speicher)
    breite = max(len(m) for m, _w in st.zeilen())
    for merkmal, wert in st.zeilen():
        typer.echo(f"{merkmal:<{breite}}  {wert}")


@datenbank_app.command("zusatzdaten")
def datenbank_zusatzdaten(
    name: Annotated[str, typer.Argument(help="Welche Zusatzdaten: kliniken-at")],
    datei: Annotated[Path | None, typer.Option(
        "--datei", help="Excel-Mappe des Ministeriums statt Abruf über das Netz")] = None,
    speicher: SpeicherOption = None,
) -> None:
    """Daten laden, die PseudonyMed nicht mitliefern darf, direkt aus der Quelle in die eigene
    Datenbank (bisher: Krankenanstalten in Österreich, BMASGPK)."""
    from pseudonymed.erkennung import zusatzdaten as zusatzmodul
    from pseudonymed.erkennung.vokabular import Vokabular

    if name != "kliniken-at":
        raise typer.BadParameter("bekannt ist nur: kliniken-at")
    typer.echo(zusatzmodul.HINWEIS_KLINIKEN_AT)
    try:
        with Vokabular(speicher) as v:
            bericht = zusatzmodul.lade_kliniken_at(v.verbindung, datei=datei)
    except (zusatzmodul.ZusatzFehler, OSError, ValueError) as e:
        typer.echo(f"Laden fehlgeschlagen: {e}", err=True)
        raise typer.Exit(1) from e
    typer.echo(bericht.beschreibung())


@datenbank_app.command("speicherort")
def datenbank_speicherort(
    datenbank: Annotated[Path | None, typer.Option("--datenbank", help="Neuer Pfad der"
                                                                      " Datenbank.")] = None,
    schluessel: Annotated[Path | None, typer.Option("--schluessel", help="Neuer Pfad der"
                                                                        " Schlüsseldatei.")]
    = None,
    kopieren: Annotated[bool, typer.Option(
        "--kopieren/--nicht-kopieren",
        help="Vorhandene Dateien an den neuen Ort kopieren (Originale werden umbenannt).")]
    = True,
) -> None:
    """Datenbank und Schlüsseldatei an beliebigen Orten ablegen; ohne Angaben: anzeigen."""
    from pseudonymed.pseudonyme import sicherung as sichmodul
    from pseudonymed.pseudonyme.speicher import standard_schluesselpfad, standardpfad

    if datenbank is None and schluessel is None:
        typer.echo(f"Datenbank:     {standardpfad()}")
        typer.echo(f"Schlüsseldatei: {standard_schluesselpfad()}")
        return
    try:
        v = sichmodul.verlege(datenbank or standardpfad(), schluessel or standard_schluesselpfad(),
                              kopieren)
    except (sichmodul.SicherungFehler, OSError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from e
    typer.echo(v.beschreibung())


@datenbank_app.command("exportieren")
def datenbank_exportieren(
    ziel: Annotated[Path, typer.Argument(help="Zielordner oder Dateiname (.zip).")],
    speicher: SpeicherOption = None,
    konfig: Annotated[bool, typer.Option("--konfig/--ohne-konfig",
                                         help="konfig.toml einschließen.")] = True,
    vokabular: Annotated[bool, typer.Option("--vokabular/--ohne-vokabular",
                                            help="Nutzervokabular einschließen.")] = True,
    passphrase: Annotated[str | None, typer.Option(
        "--passphrase", envvar=umgebung.namen("SICHERUNG_PASSPHRASE"),
        help="Passphrase für das Archiv (sonst Abfrage).")] = None,
    unverschluesselt: Annotated[bool, typer.Option(
        "--unverschluesselt", help="Ohne Verschlüsselung schreiben (nicht empfohlen).")] = False,
) -> None:
    """Datenbank, Schlüssel und wahlweise Konfiguration und Vokabular sichern."""
    from pseudonymed.pseudonyme import pgp as pgpmodul
    from pseudonymed.pseudonyme import sicherung as sichmodul

    if unverschluesselt:
        passphrase = None
    elif passphrase is None:
        if not pgpmodul.verfuegbar():
            typer.echo("Verschlüsselung braucht das Extra 'pgp' oder GnuPG; sonst"
                       " --unverschluesselt.", err=True)
            raise typer.Exit(code=1)
        passphrase = typer.prompt("Passphrase für das Archiv", hide_input=True,
                                  confirmation_prompt=True)
    if passphrase is not None and len(passphrase) < 8:
        typer.echo("Passphrase zu kurz (mindestens 8 Zeichen).", err=True)
        raise typer.Exit(code=1)
    try:
        pfad = sichmodul.exportiere(ziel, speicher, konfig, vokabular, passphrase)
    except (sichmodul.SicherungFehler, pgpmodul.PGPFehler, OSError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from e
    typer.echo(f"Gesichert: {pfad}" + ("" if passphrase else "  (UNVERSCHLÜSSELT: enthält"
                                                           " Schlüssel und Klardaten)"))


@datenbank_app.command("importieren")
def datenbank_importieren(
    archiv: Annotated[Path, typer.Argument(exists=True, help="Sicherungsarchiv.")],
    speicher: SpeicherOption = None,
    modus: Annotated[str, typer.Option("--modus", help="ersetzen oder zusammenfuehren.")]
    = "ersetzen",
    passphrase: Annotated[str | None, typer.Option(
        "--passphrase", envvar=umgebung.namen("SICHERUNG_PASSPHRASE"),
        help="Passphrase des Archivs (sonst Abfrage, falls verschlüsselt).")] = None,
    konfig: Annotated[bool, typer.Option("--konfig/--ohne-konfig",
                                         help="Konfiguration übernehmen.")] = True,
    vokabular: Annotated[bool, typer.Option("--vokabular/--ohne-vokabular",
                                            help="Nutzervokabular übernehmen.")] = True,
    ja: Annotated[bool, typer.Option("--ja", help="Ohne Rückfrage.")] = False,
) -> None:
    """Ein Archiv wiederherstellen (ersetzen) oder in die Datenbank übernehmen."""
    from pseudonymed.pseudonyme import sicherung as sichmodul

    if modus not in sichmodul.MODI:
        typer.echo(f"--modus muss eines von {', '.join(sichmodul.MODI)} sein.", err=True)
        raise typer.Exit(code=1)
    if passphrase is None and not archiv.read_bytes().startswith(b"PK"):
        passphrase = typer.prompt("Passphrase des Archivs", hide_input=True)
    try:
        m = sichmodul.pruefe(archiv, passphrase)
    except (sichmodul.SicherungFehler, OSError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from e
    typer.echo(f"Archiv: {m.beschreibung()}")
    if not ja and not typer.confirm(
            "Datenbank und Schlüssel ersetzen (die jetzigen werden daneben gesichert)?"
            if modus == "ersetzen" else "Einträge des Archivs übernehmen?"):
        raise typer.Exit(code=1)
    try:
        b = sichmodul.importiere(archiv, speicher, passphrase, modus, konfig, vokabular)
    except (sichmodul.SicherungFehler, OSError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from e
    typer.echo(b.beschreibung())


schluessel_app = typer.Typer(
    help="OpenPGP-Schlüssel für Schlüsselkapseln: Pseudonyme, die ein Empfänger auflöst.",
    no_args_is_help=True,
)
app.add_typer(schluessel_app, name="schluessel")


@schluessel_app.command("zeigen")
def schluessel_zeigen(
    quelle: Annotated[str, typer.Argument(help="Schlüsseldatei (.asc) oder https-Adresse.")],
) -> None:
    """Fingerabdruck und Namen eines öffentlichen Schlüssels anzeigen (zum Abgleich)."""
    from pseudonymed.pseudonyme import pgp as pgpmodul

    try:
        z = pgpmodul.lade_zertifikat(quelle)
    except pgpmodul.PGPFehler as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from e
    typer.echo(f"Fingerabdruck: {z.gruppen}")
    for n in z.nutzer:
        typer.echo(f"Name:          {n}")
    if z.ablauf:
        typer.echo(f"Gültig bis:    {z.ablauf}")


@schluessel_app.command("erzeugen")
def schluessel_erzeugen(
    name: Annotated[str, typer.Option("--name", prompt="Name", help="Ihr Name.")],
    email: Annotated[str, typer.Option("--email", prompt="E-Mail", help="Ihre E-Mail-Adresse.")],
    ziel: Annotated[Path, typer.Option("--ziel", help="Ordner für die beiden Dateien.")]
    = Path("."),
) -> None:
    """Ein Schlüsselpaar für Empfänger ohne GnuPG erzeugen: öffentlichen Schlüssel
    veröffentlichen, den privaten sicher verwahren (er hat keine Passphrase)."""
    from pseudonymed.pseudonyme import pgp as pgpmodul

    try:
        oeffentlich, geheim = pgpmodul.erzeuge_schluesselpaar(name, email)
    except pgpmodul.PGPFehler as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from e
    ziel.mkdir(parents=True, exist_ok=True)
    pub, sec = ziel / "pseudonymed_oeffentlich.asc", ziel / "pseudonymed_privat.asc"
    if sec.exists():
        typer.echo(f"{sec} existiert schon; nicht überschrieben.", err=True)
        raise typer.Exit(code=1)
    pub.write_text(oeffentlich, encoding="utf-8")
    sec.write_text(geheim, encoding="utf-8")
    try:
        os.chmod(sec, 0o600)
    except OSError:
        pass
    z = pgpmodul.lade_zertifikat(oeffentlich)
    typer.echo(f"Öffentlicher Schlüssel: {pub}  (veröffentlichen, z. B. auf der Webseite)")
    typer.echo(f"Privater Schlüssel:     {sec}  (geheim halten, sichern; ohne Passphrase)")
    typer.echo(f"Fingerabdruck:          {z.gruppen}")


fachbegriffe_app = typer.Typer(
    help="Medizinisches Fachvokabular, das NER und unscharfe OCR-Suche nicht anfassen dürfen.",
    no_args_is_help=True,
)
app.add_typer(fachbegriffe_app, name="fachbegriffe")


@fachbegriffe_app.command("status")
def fachbegriffe_status() -> None:
    """Umfang und Kategorien des Vokabulars anzeigen."""
    from pseudonymed.erkennung import fachbegriffe as fachmodul
    from pseudonymed.erkennung import vokabular as vokmodul

    fb = fachmodul.standard()
    typer.echo(f"{fb.anzahl} geschützte Begriffe")
    for name, n in fb.quellen:
        typer.echo(f"  {n:>7}  {name}")
    typer.echo(f"Tabelle vokabular in {vokmodul.standardpfad()} (Schritt „3 Fachvokabular“,"
               " 'fachbegriffe hinzufuegen|entfernen|exportieren|importieren')")


@fachbegriffe_app.command("hinzufuegen")
def fachbegriffe_hinzufuegen(
    begriffe: Annotated[list[str], typer.Argument(help="Begriffe (auch mehrwortig).")],
    kategorie: Annotated[str, typer.Option("--kategorie", help="Kategorie in der Tabelle.")]
    = "Nutzervokabular",
) -> None:
    """Begriffe ins Vokabular aufnehmen."""
    from pseudonymed.erkennung import fachbegriffe as fachmodul
    from pseudonymed.erkennung import vokabular as vokmodul

    with vokmodul.Vokabular() as v:
        n = v.fuege_hinzu(begriffe, kategorie)
    fachmodul.standard.cache_clear()
    typer.echo(f"{n} neue Begriffe ({kategorie}).")


@fachbegriffe_app.command("entfernen")
def fachbegriffe_entfernen(
    begriffe: Annotated[list[str], typer.Argument(help="Begriffe, genau wie gespeichert.")],
) -> None:
    """Begriffe aus dem Vokabular löschen (auch mitgelieferte)."""
    from pseudonymed.erkennung import fachbegriffe as fachmodul
    from pseudonymed.erkennung import vokabular as vokmodul

    with vokmodul.Vokabular() as v:
        n = v.entferne(begriffe)
    fachmodul.standard.cache_clear()
    typer.echo(f"{n} Begriffe gelöscht.")


@fachbegriffe_app.command("exportieren")
def fachbegriffe_exportieren(
    datei: Annotated[Path, typer.Argument(help="Zieldatei (.csv).")],
    kategorie: Annotated[str, typer.Option("--kategorie", help="Nur diese Kategorie.")] = "",
) -> None:
    """Das Vokabular als CSV (begriff,kategorie) schreiben."""
    from pseudonymed.erkennung import vokabular as vokmodul

    with vokmodul.Vokabular() as v:
        n = v.exportiere_csv(datei, kategorie)
    typer.echo(f"{n} Begriffe nach {datei} geschrieben.")


@fachbegriffe_app.command("pruefen")
def fachbegriffe_pruefen(
    woerter: Annotated[list[str], typer.Argument(help="Wörter oder Wortfolgen.")],
) -> None:
    """Prüfen, ob Wörter als Fachbegriffe geschützt sind."""
    from pseudonymed.erkennung import fachbegriffe as fachmodul

    fb = fachmodul.standard()
    for w in woerter:
        spannen = fb.spannen(w)
        ganz = bool(spannen) and fb.geschuetzt(w, 0, len(w), spannen, ganz=True)
        typer.echo(f"{w}: {'geschützt' if ganz else ('teilweise' if spannen else 'frei')}")


@fachbegriffe_app.command("ergaenzen")
def fachbegriffe_ergaenzen(
    datei: Annotated[Path, typer.Argument(
        exists=True, help="Liste, ein Begriff je Zeile, z. B. eine"
                          " <name>_pseudonym.vokabular.txt.")],
    name: Annotated[str, typer.Option("--name", help="Eigene Liste im Datenordner.")]
    = "nutzervokabular",
) -> None:
    """Begriffe einer Liste oder CSV in das Vokabular übernehmen (zusammenführen)."""
    from pseudonymed.erkennung import fachbegriffe as fachmodul

    try:
        ziel, neu = fachmodul.ergaenze(datei, name)
    except OSError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from e
    typer.echo(f"{neu} neue Begriffe nach {ziel} übernommen; jetzt {fachmodul.standard().anzahl}"
               " geschützte Begriffe")


@fachbegriffe_app.command("importieren")
def fachbegriffe_importieren(
    datei: Annotated[Path, typer.Argument(exists=True, help="Quelldatei.")],
    name: Annotated[str, typer.Option("--name", help="Name der Liste im Datenordner.")],
    format_: Annotated[str, typer.Option("--format", help="liste, csv oder mesh-xml.")]
    = "liste",
    spalte: Annotated[int, typer.Option("--spalte", help="Spalte (0-basiert) bei csv.")] = 0,
    trennzeichen: Annotated[str, typer.Option("--trennzeichen", help="Trennzeichen bei csv.")]
    = ";",
) -> None:
    """Begriffe aus MeSH-XML (NLM), ICD-10-GM/Alpha-ID (csv) oder einer Liste übernehmen."""
    from pseudonymed.erkennung import fachbegriffe as fachmodul

    try:
        ziel, n = fachmodul.importiere(datei, name, format_, spalte, trennzeichen)
    except (ValueError, OSError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from e
    typer.echo(f"{n} Begriffe nach {ziel} übernommen (Kategorie {name}); jetzt"
               f" {fachmodul.standard().anzahl} geschützte Begriffe")


identifikatoren_app = typer.Typer(
    help="Identifikatoren außerhalb der Patientendaten: Berufe, Kliniken, Studien, Titel.",
    no_args_is_help=True,
)
app.add_typer(identifikatoren_app, name="identifikatoren")


@identifikatoren_app.command("status")
def identifikatoren_status() -> None:
    """Umfang je Klasse anzeigen."""
    from pseudonymed.erkennung import identifikatoren as identmodul

    with identmodul.Identifikatoren() as ident:
        typer.echo(f"{ident.anzahl()} Identifikatoren in {ident.pfad}")
        for klasse, n in ident.klassen():
            typer.echo(f"  {n:>7}  {klasse}")
        if ident.geloescht():
            typer.echo(f"  {ident.geloescht():>7}  gelöschte mitgelieferte Einträge")
    typer.echo("Registernummern (NCT, DRKS, ISRCTN, EU CT, EudraCT) werden immer erkannt;"
               " Schritt „2 Identifikatoren“.")


@identifikatoren_app.command("hinzufuegen")
def identifikatoren_hinzufuegen(
    begriffe: Annotated[list[str], typer.Argument(help="Begriffe (auch mehrwortig).")],
    klasse: Annotated[str, typer.Option(
        "--klasse", help="Klinik, Beruf, Studie, Titel oder Sonstiges (eigene Klassen wie"
                         " Sonstiges: überall geschwärzt).")] = "Sonstiges",
) -> None:
    """Eigene Identifikatoren aufnehmen (Arbeitgeber, Vereine, Einrichtungen …)."""
    from pseudonymed.erkennung import identifikatoren as identmodul

    with identmodul.Identifikatoren() as ident:
        n = ident.fuege_hinzu(begriffe, klasse)
    typer.echo(f"{n} neue Identifikatoren ({klasse}).")


@identifikatoren_app.command("entfernen")
def identifikatoren_entfernen(
    begriffe: Annotated[list[str], typer.Argument(help="Begriffe, wie gespeichert.")],
    klasse: Annotated[str, typer.Option("--klasse", help="Nur in dieser Klasse.")] = "",
) -> None:
    """Identifikatoren löschen (auch mitgelieferte; sie bleiben bei Updates gelöscht)."""
    from pseudonymed.erkennung import identifikatoren as identmodul

    with identmodul.Identifikatoren() as ident:
        n = ident.entferne(s for b in begriffe for s in ident.schluessel_fuer(b, klasse))
    typer.echo(f"{n} Identifikatoren gelöscht.")


@identifikatoren_app.command("exportieren")
def identifikatoren_exportieren(
    datei: Annotated[Path, typer.Argument(help="Zieldatei (.csv).")],
    klasse: Annotated[str, typer.Option("--klasse", help="Nur diese Klasse.")] = "",
) -> None:
    """Die Identifikatoren als CSV (begriff,klasse,quelle,angaben) schreiben."""
    from pseudonymed.erkennung import identifikatoren as identmodul

    with identmodul.Identifikatoren() as ident:
        n = ident.exportiere_csv(datei, klasse)
    typer.echo(f"{n} Identifikatoren nach {datei} geschrieben.")


@identifikatoren_app.command("importieren")
def identifikatoren_importieren(
    datei: Annotated[Path, typer.Argument(exists=True, help="CSV (begriff[,klasse]) oder Liste.")],
    klasse: Annotated[str, typer.Option("--klasse", help="Klasse ohne Klassenspalte.")]
    = "Sonstiges",
) -> None:
    """Identifikatoren aus einer CSV oder Liste übernehmen."""
    from pseudonymed.erkennung import identifikatoren as identmodul

    try:
        with identmodul.Identifikatoren() as ident:
            neu, gelesen = ident.importiere_csv(datei, klasse)
    except OSError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from e
    typer.echo(f"{neu} neue von {gelesen} Identifikatoren übernommen.")


@identifikatoren_app.command("pruefen")
def identifikatoren_pruefen(
    texte: Annotated[list[str], typer.Argument(help="Sätze oder eine Textdatei.")],
) -> None:
    """Zeigen, was die Analyse in einem Satz als Identifikator schwärzen würde (Titel nur
    vor erkannten Personen, hier nicht geprüft)."""
    from pseudonymed.erkennung import identifikatoren as identmodul

    erkenner = identmodul.standard()
    for t in texte:
        if Path(t).is_file():
            t = Path(t).read_text(encoding="utf-8", errors="replace")
        treffer = erkenner.kandidaten(t)
        typer.echo(f"{len(treffer)} Treffer" + "".join(
            f"\n  {k.klasse}: {t[k.start:k.ende]}" for k in treffer))


kontakte_app = typer.Typer(
    help="Patientensuche im CardDAV-Adressbuch (Nextcloud).", no_args_is_help=True
)
app.add_typer(kontakte_app, name="kontakte")


@kontakte_app.command()
def einrichten(
    url: Annotated[str, typer.Option("--url", prompt="Nextcloud-Adresse (https://…)",
                                     help="Basisadresse der Nextcloud.")],
    benutzer: Annotated[str, typer.Option("--benutzer", prompt=True, help="Nextcloud-Benutzer.")],
    adressbuch: Annotated[str, typer.Option(
        "--adressbuch", help="Adressbuch (URI oder Name), leer = einziges.")] = "",
    konfig: KonfigOption = None,
    schluesselbund: Annotated[bool, typer.Option(
        "--schluesselbund/--kein-schluesselbund",
        help="App-Passwort im Schlüsselbund des Betriebssystems ablegen.")] = True,
    passwort_datei: Annotated[bool, typer.Option(
        "--passwort-datei", help="Ohne Schlüsselbund: App-Passwort in der Konfigurationsdatei"
                                 " (nur für den Benutzer lesbar) ablegen.")] = False,
) -> None:
    """Zugang einrichten: Adresse, Benutzer, App-Passwort (Nextcloud → Sicherheit)."""
    passwort = (umgebung.variable("CARDDAV_PASSWORT")
                or typer.prompt("App-Passwort", hide_input=True))
    k = CardDAVKonfig(url=url.strip(), benutzer=benutzer.strip(), adressbuch=adressbuch.strip())
    try:
        client = CardDAVClient(k.url, k.benutzer, passwort)
        buecher = client.adressbuecher()
        if k.adressbuch:
            client.adressbuch(k.adressbuch)
    except CardDAVFehler as e:
        typer.echo(f"Verbindung fehlgeschlagen: {e}", err=True)
        raise typer.Exit(code=1) from e
    if schluesselbund and passwort_speichern(k, passwort):
        typer.echo(f"App-Passwort im Schlüsselbund abgelegt ({k.kennung}).")
    elif passwort_datei:
        k.passwort_datei = passwort
        typer.echo("App-Passwort in der Konfigurationsdatei abgelegt (Rechte 0600).")
    else:
        typer.echo(
            "Kein Schlüsselbund verfügbar (Extra 'carddav' installieren) – Passwort stattdessen"
            f" in {UMGEBUNG_PASSWORT} setzen oder --passwort-datei wählen.", err=True,
        )
    pfad = speichere_konfig(k, konfig)
    typer.echo(f"Konfiguration gespeichert: {pfad}")
    typer.echo("Adressbücher: " + ", ".join(f"{b.name} ({b.uri})" for b in buecher))


@kontakte_app.command("lokal")
def kontakte_lokal(
    pfad: Annotated[Path | None, typer.Argument(
        help="vCard-Datei oder Ordner mit vCard-Dateien; ohne Angabe: anzeigen.")] = None,
    entfernen: Annotated[bool, typer.Option("--entfernen",
                                            help="Lokales Adressbuch austragen.")] = False,
    konfig: KonfigOption = None,
) -> None:
    """Ein lokales Adressbuch (vCard-Datei oder Ordner) einrichten oder anzeigen."""
    from pseudonymed.konfig import lade_einstellungen, speichere_einstellungen
    from pseudonymed.quellen.adressbuch import lokale_dateien

    e = lade_einstellungen(konfig)
    if entfernen:
        e.adressbuch_lokal = ""
        speichere_einstellungen(e, konfig)
        typer.echo("Lokales Adressbuch ausgetragen.")
        return
    if pfad is None:
        typer.echo(e.adressbuch_lokal or "Kein lokales Adressbuch eingerichtet.")
        return
    dateien = lokale_dateien(pfad)
    if not dateien:
        typer.echo(f"{pfad}: keine vCard-Datei(en) gefunden.", err=True)
        raise typer.Exit(code=1)
    e.adressbuch_lokal = str(pfad.expanduser().resolve())
    speichere_einstellungen(e, konfig)
    typer.echo(f"Lokales Adressbuch: {e.adressbuch_lokal} ({len(dateien)} Datei(en)).")


@kontakte_app.command()
def adressbuecher(konfig: KonfigOption = None) -> None:
    """Adressbücher des Benutzers auflisten."""
    client, _k = _verbindung(konfig)
    try:
        buecher = client.adressbuecher()
    except CardDAVFehler as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from e
    for b in buecher:
        typer.echo(f"{b.uri:30}  {b.name}")


@kontakte_app.command()
def suchen(
    begriff: Annotated[str, typer.Argument(help="Name, Namensteil, Geburtsjahr oder -datum.")],
    konfig: KonfigOption = None,
    speicher: SpeicherOption = None,
) -> None:
    """Kontakte suchen; zeigt vergebene Pseudonym-Buchstaben an."""
    patienten = _kontakte_suchen(begriff, konfig)
    if not patienten:
        typer.echo(f"Kein Kontakt passt zu {begriff!r}.")
        raise typer.Exit(code=1)
    with _speicher(speicher) as sp:
        for i, p in enumerate(patienten, 1):
            info = sp.patient_info(p.schluessel())
            typer.echo(_zeile(i, p, info["kennung"] if info else None))


def _patient_text(p: Patient) -> str:
    felder = [
        ("Name", f"{p.nachname}, {p.vorname}"),
        ("Anrede/Geschlecht", {"m": "Herr", "w": "Frau"}.get(p.geschlecht, "unbekannt")),
        ("Titel", p.titel),
        ("Geburtsname", p.geburtsname),
        ("Geburtsdatum", p.geburtsdatum.strftime("%d.%m.%Y") if p.geburtsdatum else ""),
        ("Anschrift", ", ".join(t for t in (p.strasse, f"{p.plz} {p.ort}".strip()) if t)),
        ("Telefon", "; ".join(p.telefone)),
        ("E-Mail", "; ".join(p.emails)),
        ("Versichertennummer", p.versichertennummer),
        ("Patienten-ID", p.patienten_id),
    ]
    zeilen = [f"{name + ':':20} {wert}" for name, wert in felder if wert]
    for h in p.hinweise:
        zeilen.append(f"{'Notiz:':20} {h}")
    zeilen.append(
        f"{'Pseudonymschlüssel:':20} {p.schluessel()}"
    )
    return "\n".join(zeilen)


@kontakte_app.command()
def zeigen(
    begriff: Annotated[str, typer.Argument(help="Suchbegriff.")],
    nr: NrOption = None,
    als_json: Annotated[bool, typer.Option("--json", help="Als Patientendatei (JSON) ausgeben.")]
    = False,
    konfig: KonfigOption = None,
) -> None:
    """Abgeleitete Identifikatoren eines Kontakts anzeigen (nur lokal)."""
    p = _auswahl(_kontakte_suchen(begriff, konfig), begriff, nr)
    if als_json:
        typer.echo(json.dumps(p.als_dict(), ensure_ascii=False, indent=2))
    else:
        typer.echo(_patient_text(p))


@kontakte_app.command()
def export(
    begriff: Annotated[str, typer.Argument(help="Suchbegriff.")],
    ausgabe: Annotated[Path, typer.Option("--ausgabe", "-o", help="Zieldatei (JSON).")],
    nr: NrOption = None,
    ueberschreiben: Annotated[bool, typer.Option("--ueberschreiben",
                                                 help="Vorhandene Datei ersetzen.")] = False,
    konfig: KonfigOption = None,
) -> None:
    """Kontakt als Patientendatei speichern, um Angehörige, Zuweiser und Begriffe zu ergänzen."""
    p = _auswahl(_kontakte_suchen(begriff, konfig), begriff, nr)
    if ausgabe.exists() and not ueberschreiben:
        typer.echo(f"{ausgabe} existiert bereits (--ueberschreiben).", err=True)
        raise typer.Exit(code=1)
    daten = p.als_dict()
    daten.setdefault("angehoerige", [])
    daten.setdefault("zuweiser", [])
    daten.setdefault("aktenzeichen", [])
    daten.setdefault("begriffe", [])
    ausgabe.write_text(json.dumps(daten, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    typer.echo(f"Patientendatei geschrieben: {ausgabe}")


if __name__ == "__main__":
    app()
