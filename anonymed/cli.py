"""Command line: anonymise, check, re-identify, list pseudonyms, show configuration."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer

from anonymed import __version__
from anonymed.ablauf import Ablaufoptionen, anonymisiere
from anonymed.pruefung.bericht import als_text
from anonymed.pseudonyme.speicher import Pseudonymspeicher, standardpfad
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


def _speicher(pfad: Path | None) -> Pseudonymspeicher:
    return Pseudonymspeicher(pfad)


def _patient_laden(pfad: Path):
    from anonymed.quellen.manuell import lade_patient

    try:
        return lade_patient(pfad)
    except (OSError, ValueError) as e:
        typer.echo(f"Patientendatei nicht lesbar: {e}", err=True)
        raise typer.Exit(code=1) from e


@app.callback()
def _hauptbefehl() -> None:
    """AnonyMed: lokale Anonymisierung mit stabilen Pseudonymen."""


@app.command()
def anonymisieren(
    dateien: Annotated[list[Path], typer.Argument(help="PDF- oder Bilddateien.", exists=True)],
    patient: Annotated[Path, typer.Option("--patient", "-p", exists=True,
                                          help="Patientendatei (JSON/TOML, siehe 'vorlage').")],
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
    p = _patient_laden(patient)
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
            typer.echo(f"Pseudonym der Hauptperson: Patient/in {erg.buchstabe}\n")
    if not alles_frei:
        raise typer.Exit(code=2)


@app.command()
def pruefen(
    datei: Annotated[Path, typer.Argument(exists=True, help="Bereits anonymisierte PDF-Datei.")],
    patient: Annotated[Path, typer.Option("--patient", "-p", exists=True)],
    speicher: SpeicherOption = None,
) -> None:
    """Gegenprobe an einer vorhandenen Datei: sind noch Identifikatoren enthalten?"""
    import pymupdf

    from anonymed.erkennung.deterministisch import regeln_fuer
    from anonymed.pruefung.gegenprobe import pruefe
    from anonymed.pseudonyme.vergabe import Pseudonymvergabe

    p = _patient_laden(patient)
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


@app.command()
def kontakte() -> None:
    """Patientensuche im CardDAV-Adressbuch (folgt in Meilenstein M2)."""
    typer.echo("Die CardDAV-Anbindung folgt in Meilenstein M2.", err=True)
    raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
