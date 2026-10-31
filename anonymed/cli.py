"""Command line: anonymise, check, re-identify, list pseudonyms, show configuration."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Annotated

import typer

from anonymed import __version__
from anonymed.ablauf import Ablaufoptionen, anonymisiere
from anonymed.dokument.ocr import OCRFehler, verfuegbare_backends
from anonymed.dokument.textdatei import ist_textdatei
from anonymed.erkennung.codes import verfuegbar as codes_verfuegbar
from anonymed.kern.modell import Patient
from anonymed.konfig import (
    UMGEBUNG_PASSWORT,
    CardDAVKonfig,
    konfigpfad,
    lade_konfig,
    passwort_laden,
    passwort_speichern,
    speichere_konfig,
)
from anonymed.pruefung.bericht import als_text
from anonymed.pseudonyme.speicher import (
    SCHEMATA,
    Pseudonymspeicher,
    UnsichererAbgleich,
    schluesselpfad_zu,
    standardpfad,
)
from anonymed.quellen.carddav import CardDAVClient, CardDAVFehler, Kontakt, passt
from anonymed.schwaerzen.pdf import Schwaerzoptionen

app = typer.Typer(
    help="AnonyMed – Patientendokumente lokal anonymisieren, Pseudonyme später zurücksetzen.",
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
    from anonymed.quellen.manuell import lade_patient

    try:
        return lade_patient(pfad)
    except (OSError, ValueError) as e:
        typer.echo(f"Patientendatei nicht lesbar: {e}", err=True)
        raise typer.Exit(code=1) from e


def _verbindung(konfig: Path | None) -> tuple[CardDAVClient, CardDAVKonfig]:
    k = lade_konfig(konfig)
    if not k.vollstaendig:
        typer.echo("CardDAV ist nicht eingerichtet: zuerst 'anonymed kontakte einrichten'.",
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


def _kontakte_suchen(begriff: str, konfig: Path | None) -> list[Kontakt]:
    client, k = _verbindung(konfig)
    try:
        return client.suche(client.adressbuch(k.adressbuch), begriff)
    except CardDAVFehler as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from e


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
    from anonymed.quellen.vcf import patienten_aus_vcf

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
        return _auswahl([k.patient for k in _kontakte_suchen(kontakt, konfig)], kontakt, nr)
    typer.echo("Bitte --patient, --vcf oder --kontakt angeben.", err=True)
    raise typer.Exit(code=1)


@app.callback()
def _hauptbefehl() -> None:
    """AnonyMed: lokale Anonymisierung mit stabilen Pseudonymen."""


@app.command()
def anonymisieren(
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
    unscharf: Annotated[bool, typer.Option(
        "--unscharf/--genau",
        help="Textdateien: abweichend geschriebene Namen unscharf suchen (Transkripte).")] = True,
) -> None:
    """Dokumente und Texte schwärzen, Pseudonyme einsetzen, Gegenprobe, Bericht."""
    if unlesbar not in ("schwaerzen", "lassen"):
        typer.echo("--unlesbar muss 'schwaerzen' oder 'lassen' sein.", err=True)
        raise typer.Exit(code=1)
    if datum not in ("jahr", "alter"):
        typer.echo("--datum muss 'jahr' oder 'alter' sein.", err=True)
        raise typer.Exit(code=1)
    if vorschau not in ("auto", "immer", "nie"):
        typer.echo("--vorschau muss 'auto', 'immer' oder 'nie' sein.", err=True)
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
        unlesbar=unlesbar, ner=ner, fachbegriffe=fachbegriffe, unscharf_text=unscharf,
        schwaerzen=Schwaerzoptionen(rastern=rastern, dpi=dpi, vermerk=vermerk,
                                    textebene=textebene),
    )
    alles_frei = True
    with _speicher(speicher, schema) as sp:
        for datei in dateien:
            try:
                erg = anonymisiere(datei, p, opt, speicher=sp)
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


@app.command()
def pruefen(
    datei: Annotated[Path, typer.Argument(exists=True,
                                          help="Bereits anonymisierte PDF- oder Textdatei.")],
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

    from anonymed.erkennung.deterministisch import regeln_fuer
    from anonymed.pruefung.gegenprobe import pruefe
    from anonymed.pseudonyme.vergabe import Pseudonymvergabe

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
        from anonymed.ablauf import gegenprobe_textdatei

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
) -> None:
    """Pseudonyme im Text des Sprachmodells durch die Klardaten ersetzen (lokal)."""
    from anonymed.pseudonyme.rueckersetzung import rueckersetzen as _rueck

    if form not in ("vollname", "anrede"):
        typer.echo("--form muss 'vollname' oder 'anrede' sein.", err=True)
        raise typer.Exit(code=1)
    if not kennung and not suche:
        typer.echo("Bitte --kennung oder --suche angeben.", err=True)
        raise typer.Exit(code=1)
    with _speicher(speicher) as sp:
        treffer = sp.finde(kennung_=kennung, suche=suche)
        if len(treffer) != 1:
            typer.echo(
                f"{len(treffer)} Einträge gefunden; bitte eindeutig wählen (siehe 'pseudonyme').",
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
    from anonymed.gui.app import main

    raise typer.Exit(code=main([]))


@app.command()
def vorlage() -> None:
    """JSON-Vorlage für eine Patientendatei ausgeben."""
    from anonymed.quellen.manuell import vorlage as _vorlage

    typer.echo(_vorlage(), nl=False)


@app.command()
def konfig(speicher: SpeicherOption = None) -> None:
    """Version und Pfade anzeigen."""
    import pymupdf

    typer.echo(f"AnonyMed {__version__}")
    typer.echo(f"PyMuPDF {pymupdf.version[0]} (MuPDF {pymupdf.version[1]})")
    from anonymed.dokument.ocr import tesseract_programm, tesseract_sprachen
    from anonymed.konfig import lade_einstellungen

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
    from anonymed.erkennung import ner as nermodul

    typer.echo("NER:                 " + (f"spaCy mit {nermodul.verfuegbares_modell()}"
                                          if nermodul.verfuegbar() else
                                          "nicht verfügbar (Extra 'ner' + deutsches Modell)"))
    from anonymed.erkennung import fachbegriffe as fachmodul

    fb = fachmodul.standard()
    quellen = ", ".join(f"{name} {n}" for name, n in fb.quellen)
    typer.echo(f"Fachbegriffe:        {fb.anzahl} geschützte Begriffe ({quellen});"
               f" eigene Listen in {fachmodul.datenordner()}")
    typer.echo(f"Pseudonym-Datenbank: {speicher or standardpfad()}")
    typer.echo(f"Pseudonymschlüssel:  {schluesselpfad_zu(speicher or standardpfad())}"
               "  (sichern; gleicher Schlüssel = gleiche Kennungen auf jedem Rechner)")
    k = lade_konfig()
    typer.echo(f"Konfiguration:       {konfigpfad()}")
    if k.vollstaendig:
        buch = k.adressbuch or "einziges Adressbuch"
        typer.echo(f"CardDAV:             {k.benutzer} @ {k.url} ({buch})")
    else:
        typer.echo("CardDAV:             nicht eingerichtet")


fachbegriffe_app = typer.Typer(
    help="Medizinisches Fachvokabular, das NER und unscharfe OCR-Suche nicht anfassen dürfen.",
    no_args_is_help=True,
)
app.add_typer(fachbegriffe_app, name="fachbegriffe")


@fachbegriffe_app.command("status")
def fachbegriffe_status() -> None:
    """Umfang und Quellen des Vokabulars anzeigen."""
    from anonymed.erkennung import fachbegriffe as fachmodul

    fb = fachmodul.standard()
    typer.echo(f"{fb.anzahl} geschützte Begriffe")
    for name, n in fb.quellen:
        typer.echo(f"  {n:>7}  {name}")
    typer.echo(f"Eigene Listen: {fachmodul.datenordner()} (*.txt, ein Begriff je Zeile)")


@fachbegriffe_app.command("pruefen")
def fachbegriffe_pruefen(
    woerter: Annotated[list[str], typer.Argument(help="Wörter oder Wortfolgen.")],
) -> None:
    """Prüfen, ob Wörter als Fachbegriffe geschützt sind."""
    from anonymed.erkennung import fachbegriffe as fachmodul

    fb = fachmodul.standard()
    for w in woerter:
        spannen = fb.spannen(w)
        ganz = bool(spannen) and fb.geschuetzt(w, 0, len(w), spannen, ganz=True)
        typer.echo(f"{w}: {'geschützt' if ganz else ('teilweise' if spannen else 'frei')}")


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
    from anonymed.erkennung import fachbegriffe as fachmodul

    try:
        ziel = fachmodul.importiere(datei, name, format_, spalte, trennzeichen)
    except (ValueError, OSError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from e
    n = sum(1 for z in ziel.read_text(encoding="utf-8").splitlines() if not z.startswith("#"))
    typer.echo(f"{n} Begriffe nach {ziel} geschrieben; jetzt {fachmodul.standard().anzahl}"
               " geschützte Begriffe")


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
) -> None:
    """Zugang einrichten: Adresse, Benutzer, App-Passwort (Nextcloud → Sicherheit)."""
    passwort = os.environ.get(UMGEBUNG_PASSWORT) or typer.prompt("App-Passwort", hide_input=True)
    k = CardDAVKonfig(url=url.strip(), benutzer=benutzer.strip(), adressbuch=adressbuch.strip())
    try:
        client = CardDAVClient(k.url, k.benutzer, passwort)
        buecher = client.adressbuecher()
        if k.adressbuch:
            client.adressbuch(k.adressbuch)
    except CardDAVFehler as e:
        typer.echo(f"Verbindung fehlgeschlagen: {e}", err=True)
        raise typer.Exit(code=1) from e
    pfad = speichere_konfig(k, konfig)
    typer.echo(f"Konfiguration gespeichert: {pfad}")
    if schluesselbund:
        if passwort_speichern(k, passwort):
            typer.echo(f"App-Passwort im Schlüsselbund abgelegt ({k.kennung}).")
        else:
            typer.echo(
                "Kein Schlüsselbund verfügbar (Extra 'carddav' installieren) – Passwort stattdessen"
                f" in {UMGEBUNG_PASSWORT} setzen.", err=True,
            )
    typer.echo("Adressbücher: " + ", ".join(f"{b.name} ({b.uri})" for b in buecher))


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
    kontakte = _kontakte_suchen(begriff, konfig)
    if not kontakte:
        typer.echo(f"Kein Kontakt passt zu {begriff!r}.")
        raise typer.Exit(code=1)
    with _speicher(speicher) as sp:
        for i, kontakt in enumerate(kontakte, 1):
            info = sp.patient_info(kontakt.patient.schluessel())
            typer.echo(_zeile(i, kontakt.patient, info["kennung"] if info else None))


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
    p = _auswahl([k.patient for k in _kontakte_suchen(begriff, konfig)], begriff, nr)
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
    p = _auswahl([k.patient for k in _kontakte_suchen(begriff, konfig)], begriff, nr)
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
