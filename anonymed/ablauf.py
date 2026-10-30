"""The pipeline shared by CLI and GUI: load, find, redact, clean, check, report."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from anonymed.dokument.laden import oeffne
from anonymed.dokument.textebene import lese_dokument
from anonymed.erkennung.deterministisch import anonymisierter_text, finde, regeln_fuer
from anonymed.kern.modell import Fundstelle, Patient, Pruefbericht
from anonymed.pruefung.bericht import erstelle_bericht, speichere_bericht
from anonymed.pruefung.gegenprobe import pruefe
from anonymed.pseudonyme.speicher import Pseudonymspeicher
from anonymed.pseudonyme.vergabe import Pseudonymvergabe
from anonymed.schwaerzen.pdf import (
    Schwaerzoptionen,
    bereinige,
    rastere,
    rastere_seite,
    schwaerze_seite,
    speichere,
)


@dataclass
class Ablaufoptionen:
    datumsform: str = "jahr"  # "jahr" -> [geb. 1990], "alter" -> [Alter 36]
    textdatei: bool = True  # write <name>_anonym.txt in reading order
    bericht: bool = True  # write <name>_anonym.bericht.json
    suffix: str = "_anonym"
    ziel_ordner: Path | None = None
    abgleich: str | None = None  # decision for an uncertain match: "neu" or "gleich:<KENNUNG>"
    schwaerzen: Schwaerzoptionen = field(default_factory=Schwaerzoptionen)

    def als_dict(self) -> dict[str, object]:
        return {
            "datumsform": self.datumsform,
            "textdatei": self.textdatei,
            "rastern": self.schwaerzen.rastern,
            "dpi": self.schwaerzen.dpi if self.schwaerzen.rastern else None,
            "vermerk": self.schwaerzen.vermerk,
        }


@dataclass
class Ergebnis:
    quelle: Path
    bericht: Pruefbericht
    fundstellen: list[Fundstelle]
    kennung: str
    ziel_pdf: Path | None = None
    ziel_txt: Path | None = None
    ziel_bericht: Path | None = None
    text: str = ""

    @property
    def freigegeben(self) -> bool:
        return self.ziel_pdf is not None


def zielpfad(quelle: Path, opt: Ablaufoptionen, endung: str = ".pdf") -> Path:
    ordner = opt.ziel_ordner or quelle.parent
    return ordner / f"{quelle.stem}{opt.suffix}{endung}"


def anonymisiere(
    pfad: str | Path,
    patient: Patient,
    opt: Ablaufoptionen | None = None,
    speicher: Pseudonymspeicher | None = None,
) -> Ergebnis:
    """Anonymise one file.  The output is only kept when the check passes."""
    opt = opt or Ablaufoptionen()
    quelle = Path(pfad)
    eigener_speicher = speicher is None
    speicher = speicher or Pseudonymspeicher()
    try:
        kennung = speicher.kennung_fuer(patient, opt.abgleich)
        vergabe = Pseudonymvergabe(patient, kennung.kennung, opt.datumsform,
                                   geheimnis=speicher.geheimnis)
        regeln = regeln_fuer(patient, vergabe)
        warnungen: list[str] = []
        if kennung.verknuepft and kennung.abgleich is not None:
            a = kennung.abgleich
            felder = ", ".join(f"{k} {v:.2f}" for k, v in a.felder.items())
            warnungen.append(
                f"Patient dem vorhandenen Eintrag {a.kennung} zugeordnet"
                f" (Abgleich {a.score:.2f}: {felder})."
            )

        doc = oeffne(quelle)
        original = oeffne(quelle)
        try:
            seiten = lese_dokument(doc)
            ohne_text = [s.nummer + 1 for s in seiten if not s.hat_textebene]
            if ohne_text:
                warnungen.append(
                    "Seite(n) ohne Textebene: " + ", ".join(map(str, ohne_text))
                    + " – OCR folgt in M3; Scans und Fotos nie ungeprüft weitergeben."
                )
            fundstellen = [f for s in seiten for f in finde(s, regeln)]
            vergabe.registriere_treffer(fundstellen)
            text = anonymisierter_text(seiten, fundstellen)
            for s in seiten:
                eigene = [f for f in fundstellen if f.seite == s.nummer]
                lage = schwaerze_seite(doc[s.nummer], s, eigene, opt.schwaerzen)
                if not lage.lage_intakt:
                    rastere_seite(doc, s.nummer, original, s, eigene, opt.schwaerzen)
                    warnungen.append(
                        f"Seite {s.nummer + 1}: Textlayout nach der Redaktion verschoben,"
                        " Seite deshalb gerastert (ohne Textebene)."
                    )
            bereinige(doc, vermerk=opt.schwaerzen.vermerk)
            ausgabe = doc
            if opt.schwaerzen.rastern:
                ausgabe = rastere(doc, opt.schwaerzen.dpi)
                bereinige(ausgabe, vermerk=opt.schwaerzen.vermerk)
                warnungen.append("Ausgabe gerastert; OCR-Gegenprobe folgt in M3.")
            ziel = zielpfad(quelle, opt)
            ziel.parent.mkdir(parents=True, exist_ok=True)
            speichere(ausgabe, ziel)
            if ausgabe is not doc:
                ausgabe.close()
        finally:
            doc.close()
            original.close()

        with pymupdf.open(ziel) as kontrolle:
            gegenprobe = pruefe(kontrolle, regeln, zusatztexte=[text] if opt.textdatei else [])
            seitenzahl = len(kontrolle)

        speicher.merke(patient, vergabe)

        ergebnis = Ergebnis(quelle=quelle, bericht=None, fundstellen=fundstellen,  # type: ignore[arg-type]
                            kennung=kennung.kennung, text=text)
        if ohne_text:
            gegenprobe.anmerkungen.append(
                "Seiten ohne Textebene können bis zur OCR (M3) nicht geprüft werden"
            )
        if gegenprobe.bestanden and not ohne_text:
            ergebnis.ziel_pdf = ziel
            if opt.textdatei:
                ergebnis.ziel_txt = zielpfad(quelle, opt, ".txt")
                ergebnis.ziel_txt.write_text(text, encoding="utf-8")
        else:
            ziel.unlink(missing_ok=True)
            warnungen.append("Nicht freigegeben: Ausgabe verworfen.")
        ergebnis.bericht = erstelle_bericht(
            quelle, ergebnis.ziel_pdf, seitenzahl, not ohne_text, fundstellen, gegenprobe,
            opt.als_dict(), warnungen,
        )
        if opt.bericht:
            ergebnis.ziel_bericht = zielpfad(quelle, opt, ".bericht.json")
            speichere_bericht(ergebnis.bericht, ergebnis.ziel_bericht)
        return ergebnis
    finally:
        if eigener_speicher:
            speicher.close()
