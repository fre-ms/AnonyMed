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
from anonymed.pseudonyme.speicher import Pseudonymspeicher, standardpfad
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


def _speicher(pfad: Path | None) -> Pseudonymspeicher:
    return Pseudonymspeicher(pfad)


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


def _zeile(nr: int, p: Patient, buchstabe: str | None = None) -> str:
    geb = p.geburtsdatum.strftime("%d.%m.%Y") if p.geburtsdatum else "–"
    ort = " ".join(t for t in (p.plz, p.ort) if t)
    pseudonym = f"  → {'Patientin' if p.weiblich else 'Patient'} {buchstabe}" if buchstabe else ""
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
    dateien: Annotated[list[Path], typer.Argument(help="PDF- oder Bilddateien.", exists=True)],
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
    bericht: Annotated[bool, typer.Option("--bericht/--kein-bericht",
                                          help="Prüfbericht als JSON schreiben.")] = True,
    vermerk: Annotated[bool, typer.Option("--vermerk/--ohne-vermerk",
                                          help="Vermerk in den PDF-Metadaten.")] = True,
    speicher: SpeicherOption = None,
) -> None:
    """Dokumente schwärzen, Pseudonyme einsetzen, Gegenprobe, Bericht."""
    if datum not in ("jahr", "alter"):
        typer.echo("--datum muss 'jahr' oder 'alter' sein.", err=True)
        raise typer.Exit(code=1)
    p = _patient_bestimmen(patient, vcf, kontakt, nr, konfig)
    if p.hinweise:
        typer.echo(
            "Hinweis: Die Kontaktkarte enthält Notizen. Angehörige, Zuweiser oder Begriffe daraus"
            " werden nicht automatisch geschwärzt; bei Bedarf 'kontakte export' nutzen und die"
            " Patientendatei ergänzen.", err=True,
        )
    opt = Ablaufoptionen(
        datumsform=datum, textdatei=textdatei, bericht=bericht, ziel_ordner=ziel,
        schwaerzen=Schwaerzoptionen(rastern=rastern, dpi=dpi, vermerk=vermerk),
    )
    alles_frei = True
    with _speicher(speicher) as sp:
        for datei in dateien:
            try:
                erg = anonymisiere(datei, p, opt, speicher=sp)
            except (ValueError, NotImplementedError) as e:
                typer.echo(f"{datei.name}: {e}", err=True)
                alles_frei = False
                continue
            typer.echo(als_text(erg.bericht))
            if erg.freigegeben:
                typer.echo(f"Freigegeben: {erg.ziel_pdf}")
                if erg.ziel_txt:
                    typer.echo(f"Text:        {erg.ziel_txt}")
            else:
                alles_frei = False
                typer.echo(f"NICHT freigegeben: {datei.name}", err=True)
            if erg.ziel_bericht:
                typer.echo(f"Bericht:     {erg.ziel_bericht}")
            wort = "Patientin" if p.weiblich else "Patient"
            typer.echo(f"Pseudonym der Hauptperson: {wort} {erg.buchstabe}\n")
    if not alles_frei:
        raise typer.Exit(code=2)


@app.command()
def pruefen(
    datei: Annotated[Path, typer.Argument(exists=True, help="Bereits anonymisierte PDF-Datei.")],
    patient: PatientOption = None,
    vcf: VcfOption = None,
    kontakt: KontaktOption = None,
    nr: NrOption = None,
    konfig: KonfigOption = None,
    speicher: SpeicherOption = None,
) -> None:
    """Gegenprobe an einer vorhandenen Datei: sind noch Identifikatoren enthalten?"""
    import pymupdf

    from anonymed.erkennung.deterministisch import regeln_fuer
    from anonymed.pruefung.gegenprobe import pruefe
    from anonymed.pseudonyme.vergabe import Pseudonymvergabe

    p = _patient_bestimmen(patient, vcf, kontakt, nr, konfig)
    with _speicher(speicher) as sp:
        buchstabe = sp.buchstabe_fuer(p)
    regeln = regeln_fuer(p, Pseudonymvergabe(p, buchstabe))
    with pymupdf.open(datei) as doc:
        g = pruefe(doc, regeln)
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
    buchstabe: Annotated[str | None, typer.Option("--buchstabe", "-b",
                                                  help="Pseudonym-Buchstabe, z. B. A.")] = None,
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
    if not buchstabe and not suche:
        typer.echo("Bitte --buchstabe oder --suche angeben.", err=True)
        raise typer.Exit(code=1)
    with _speicher(speicher) as sp:
        treffer = sp.finde(buchstabe_=buchstabe, suche=suche)
        if len(treffer) != 1:
            typer.echo(
                f"{len(treffer)} Einträge gefunden; bitte eindeutig wählen (siehe 'pseudonyme').",
                err=True,
            )
            raise typer.Exit(code=1)
        info = treffer[0]
        zuordnung = sp.zuordnung(info["schluessel"])
    text = textdatei.read_text(encoding="utf-8") if textdatei else sys.stdin.read()
    r = _rueck(text, zuordnung, info, form=form)
    if ausgabe:
        ausgabe.write_text(r.text, encoding="utf-8")
        typer.echo(f"{r.ersetzt} Ersetzungen, geschrieben nach {ausgabe}", err=True)
    else:
        typer.echo(r.text, nl=False)
    if r.verbleibend:
        typer.echo("Nicht aufgelöst: " + ", ".join(r.verbleibend), err=True)
        raise typer.Exit(code=3)


@app.command()
def pseudonyme(speicher: SpeicherOption = None) -> None:
    """Vergebene Buchstaben auflisten (nur lokal)."""
    with _speicher(speicher) as sp:
        eintraege = sp.finde()
    if not eintraege:
        typer.echo("Noch keine Pseudonyme vergeben.")
        return
    for e in eintraege:
        typer.echo(f"{e['buchstabe']:>3}  {e['nachname']}, {e['vorname']}  ({e['angelegt'][:10]})")


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
    typer.echo(f"Pseudonym-Datenbank: {speicher or standardpfad()}")
    k = lade_konfig()
    typer.echo(f"Konfiguration:       {konfigpfad()}")
    if k.vollstaendig:
        buch = k.adressbuch or "einziges Adressbuch"
        typer.echo(f"CardDAV:             {k.benutzer} @ {k.url} ({buch})")
    else:
        typer.echo("CardDAV:             nicht eingerichtet")


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
            typer.echo(_zeile(i, kontakt.patient, info["buchstabe"] if info else None))


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
