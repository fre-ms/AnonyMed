"""The pipeline shared by CLI and GUI: load, read (text layer and/or OCR), find,
redact, clean, check, report."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from anonymed.dokument import ocr as ocrmodul
from anonymed.dokument.laden import oeffne
from anonymed.dokument.textebene import Seitentext, lese_seite
from anonymed.erkennung import codes as codemodul
from anonymed.erkennung.deterministisch import Regel, anonymisierter_text, finde, regeln_fuer
from anonymed.erkennung.unscharf import Ziel, finde_unscharf, ziele_fuer
from anonymed.kern.modell import Fundstelle, Gegenprobe, Patient, Pruefbericht
from anonymed.pruefung.bericht import erstelle_bericht, speichere_bericht
from anonymed.pruefung.gegenprobe import pruefe
from anonymed.pruefung.vorschau import schreibe_vorschau
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
    ocr: str = "auto"  # "auto", "rapidocr", "tesseract" or "aus"
    ocr_dpi: int = ocrmodul.OCR_DPI
    codes: bool = True  # blank barcodes and QR codes (extra "codes")
    unlesbar: str = "schwaerzen"  # lines OCR could not read: "schwaerzen" or "lassen"
    vorschau: str = "auto"  # "auto" (pages that needed OCR), "immer", "nie"
    schwaerzen: Schwaerzoptionen = field(default_factory=Schwaerzoptionen)

    def als_dict(self) -> dict[str, object]:
        return {
            "datumsform": self.datumsform,
            "textdatei": self.textdatei,
            "rastern": self.schwaerzen.rastern,
            "dpi": self.schwaerzen.dpi if self.schwaerzen.rastern else None,
            "vermerk": self.schwaerzen.vermerk,
            "ocr": self.ocr,
            "codes": self.codes,
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
    vorschau: list[Path] = field(default_factory=list)
    text: str = ""

    @property
    def freigegeben(self) -> bool:
        return self.ziel_pdf is not None


def zielpfad(quelle: Path, opt: Ablaufoptionen, endung: str = ".pdf") -> Path:
    ordner = opt.ziel_ordner or quelle.parent
    return ordner / f"{quelle.stem}{opt.suffix}{endung}"


@dataclass
class _Seite:
    nummer: int
    text: Seitentext
    ocr: Seitentext | None = None
    fundstellen: list[Fundstelle] = field(default_factory=list)


def _lese_seiten(
    doc: pymupdf.Document, regeln: list[Regel], ziele: list[Ziel], opt: Ablaufoptionen,
    backend: ocrmodul.OCRBackend | None,
) -> tuple[list[_Seite], list[int]]:
    """Text layer and, where needed and possible, OCR per page; returns the pages and
    the numbers (1-based) of pages that needed OCR but got none."""
    seiten: list[_Seite] = []
    ohne_text: list[int] = []
    for i, page in enumerate(doc):
        textebene = lese_seite(page, i)
        eintrag = _Seite(i, textebene)
        eintrag.fundstellen = finde(textebene, regeln)
        if ocrmodul.braucht_ocr(page, textebene):
            if backend is None:
                ohne_text.append(i + 1)
            else:
                eintrag.ocr = ocrmodul.erkenne_seite(page, backend, i, opt.ocr_dpi)
                genau = finde(eintrag.ocr, regeln)
                for f in genau:
                    f.quelle = "ocr"
                eintrag.fundstellen += genau + finde_unscharf(eintrag.ocr, ziele, genau)
                if opt.unlesbar == "schwaerzen":
                    belegt = [(f.start, f.ende) for f in eintrag.fundstellen if f.quelle == "ocr"]
                    for u in ocrmodul.unlesbare_regionen(eintrag.ocr):
                        if not any(u.start < y and u.ende > x for x, y in belegt):
                            eintrag.fundstellen.append(u)
        if opt.codes and codemodul.verfuegbar():
            eintrag.fundstellen += codemodul.finde_codes(page, i)
        seiten.append(eintrag)
    return seiten, ohne_text


def _text(seiten: list[_Seite]) -> str:
    """Reading-order text with pseudonyms: text layer first, OCR text as its own block."""
    teile = anonymisierter_text([s.text for s in seiten],
                                [f for s in seiten for f in s.fundstellen if f.quelle == "text"])
    ocr_seiten = [s for s in seiten if s.ocr is not None]
    if ocr_seiten:
        ocr_text = anonymisierter_text(
            [s.ocr for s in ocr_seiten if s.ocr is not None],
            [f for s in ocr_seiten for f in s.fundstellen if f.quelle == "ocr"],
        )
        teile = teile.rstrip("\n") + "\n\n--- Bildtext (OCR, ungeprüft) ---\n\n" + ocr_text
    return teile


def _gegenprobe_bilder(
    doc: pymupdf.Document, seiten: list[_Seite], regeln: list[Regel], ziele: list[Ziel],
    backend: ocrmodul.OCRBackend | None, opt: Ablaufoptionen, gegenprobe: Gegenprobe,
) -> None:
    """OCR the output pages that were read by OCR and look for leftovers (exact and
    fuzzy); re-scan codes."""
    for s in seiten:
        if s.nummer >= len(doc):
            continue
        if s.ocr is not None and backend is not None:
            nachher = ocrmodul.erkenne_seite(doc[s.nummer], backend, s.nummer, opt.ocr_dpi)
            genau = finde(nachher, regeln)
            for f in genau + finde_unscharf(nachher, ziele, genau):
                eintrag = f.ohne_klartext()
                eintrag["quelle"] = "ocr"
                gegenprobe.resttreffer.append(eintrag)
        if opt.codes and codemodul.verfuegbar():
            for f in codemodul.finde_codes(doc[s.nummer], s.nummer):
                gegenprobe.resttreffer.append(f.ohne_klartext())


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
        ziele = ziele_fuer(patient, vergabe)
        warnungen: list[str] = []
        if kennung.verknuepft and kennung.abgleich is not None:
            a = kennung.abgleich
            felder = ", ".join(f"{k} {v:.2f}" for k, v in a.felder.items())
            warnungen.append(
                f"Patient dem vorhandenen Eintrag {a.kennung} zugeordnet"
                f" (Abgleich {a.score:.2f}: {felder})."
            )
        backend = ocrmodul.backend(opt.ocr)

        doc = oeffne(quelle)
        original = oeffne(quelle)
        try:
            seiten, ohne_text = _lese_seiten(doc, regeln, ziele, opt, backend)
            if ohne_text:
                warnungen.append(
                    "Seite(n) ohne Textebene und ohne OCR: " + ", ".join(map(str, ohne_text))
                    + " – OCR-Backend installieren (Extra 'ocr' oder Tesseract) oder --ocr prüfen."
                )
            ocr_seiten = [s.nummer + 1 for s in seiten if s.ocr is not None]
            if ocr_seiten:
                unsicher = sum(s.ocr.unsichere_woerter for s in seiten if s.ocr is not None)
                unlesbar = sum(1 for s in seiten for f in s.fundstellen
                               if f.regel == "ocr.unlesbar")
                warnungen.append(
                    f"Scan/Foto: Seite(n) {', '.join(map(str, ocr_seiten))} per OCR gelesen"
                    f" ({backend.name if backend else '?'}, {unsicher} unsichere Wörter"
                    f", {unlesbar} unlesbare Zeilen geschwärzt)."
                    " Handschrift wird nicht erkannt; Vorschaubilder vor der Weitergabe prüfen."
                )
            fundstellen = [f for s in seiten for f in s.fundstellen]
            vergabe.registriere_treffer(fundstellen)
            text = _text(seiten)
            for s in seiten:
                lage = schwaerze_seite(doc[s.nummer], s.text, s.fundstellen, opt.schwaerzen)
                if not lage.lage_intakt:
                    rastere_seite(doc, s.nummer, original, s.text, s.fundstellen, opt.schwaerzen)
                    warnungen.append(
                        f"Seite {s.nummer + 1}: Textlayout nach der Redaktion verschoben,"
                        " Seite deshalb gerastert (ohne Textebene)."
                    )
            bereinige(doc, vermerk=opt.schwaerzen.vermerk)
            ausgabe = doc
            if opt.schwaerzen.rastern:
                ausgabe = rastere(doc, opt.schwaerzen.dpi)
                bereinige(ausgabe, vermerk=opt.schwaerzen.vermerk)
                warnungen.append("Ausgabe gerastert.")
            ziel = zielpfad(quelle, opt)
            ziel.parent.mkdir(parents=True, exist_ok=True)
            speichere(ausgabe, ziel)
            if ausgabe is not doc:
                ausgabe.close()
        finally:
            doc.close()
            original.close()

        vorschau: list[Path] = []
        with pymupdf.open(ziel) as kontrolle:
            gegenprobe = pruefe(kontrolle, regeln, zusatztexte=[text] if opt.textdatei else [])
            _gegenprobe_bilder(kontrolle, seiten, regeln, ziele, backend, opt, gegenprobe)
            seitenzahl = len(kontrolle)
            if opt.vorschau == "immer" or (opt.vorschau == "auto" and ocr_seiten):
                vorschau = schreibe_vorschau(kontrolle, zielpfad(quelle, opt, "_vorschau"))

        speicher.merke(patient, vergabe)

        ergebnis = Ergebnis(quelle=quelle, bericht=None, fundstellen=fundstellen,  # type: ignore[arg-type]
                            kennung=kennung.kennung, text=text, vorschau=vorschau)
        if ohne_text:
            gegenprobe.anmerkungen.append("Seiten ohne Textebene konnten nicht geprüft werden")
        if gegenprobe.bestanden and not ohne_text:
            ergebnis.ziel_pdf = ziel
            if opt.textdatei:
                ergebnis.ziel_txt = zielpfad(quelle, opt, ".txt")
                ergebnis.ziel_txt.write_text(text, encoding="utf-8")
        else:
            ziel.unlink(missing_ok=True)
            warnungen.append("Nicht freigegeben: Ausgabe verworfen.")
        ergebnis.bericht = erstelle_bericht(
            quelle, ergebnis.ziel_pdf, seitenzahl, not ocr_seiten and not ohne_text, fundstellen,
            gegenprobe, opt.als_dict(), warnungen,
        )
        if opt.bericht:
            ergebnis.ziel_bericht = zielpfad(quelle, opt, ".bericht.json")
            speichere_bericht(ergebnis.bericht, ergebnis.ziel_bericht)
        return ergebnis
    finally:
        if eigener_speicher:
            speicher.close()
