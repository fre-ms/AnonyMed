"""The pipeline shared by CLI and GUI: load, read (text layer and/or OCR), find,
redact, clean, check, report."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

import pymupdf

from anonymed.dokument import aufbereitung as aufmodul
from anonymed.dokument import ocr as ocrmodul
from anonymed.dokument import textdatei as textmodul
from anonymed.dokument.laden import BILDENDUNGEN, HEIC_ENDUNGEN, oeffne
from anonymed.dokument.textebene import Seitentext, lese_seite
from anonymed.erkennung import codes as codemodul
from anonymed.erkennung import fachbegriffe as fachmodul
from anonymed.erkennung import ner as nermodul
from anonymed.erkennung import zahlwoerter
from anonymed.erkennung.deterministisch import (
    Regel,
    finde,
    kasten_zu_fundstellen,
    regeln_fuer,
)
from anonymed.erkennung.unscharf import Ziel, finde_unscharf, ziele_fuer
from anonymed.kern.modell import Art, Fundstelle, Gegenprobe, Patient, Pruefbericht
from anonymed.pruefung.bericht import erstelle_bericht, speichere_bericht
from anonymed.pruefung.gegenprobe import (
    in_pseudonym,
    pruefe,
    pseudonymspannen,
    pseudonymspannen_tolerant,
    toleranzschluessel,
)
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
    unsichtbare_textebene,
    unsichtbarer_textblock,
)


@dataclass
class Ablaufoptionen:
    datumsform: str = "jahr"  # "jahr" -> [geb. 1990], "alter" -> [Alter 36]
    textdatei: bool = True  # write <name>_anonym.txt in reading order
    markdown: bool = False  # write <name>_anonym.md as well
    bericht: bool = True  # write <name>_anonym.bericht.json
    suffix: str = "_anonym"
    ziel_ordner: Path | None = None
    abgleich: str | None = None  # decision for an uncertain match: "neu" or "gleich:<KENNUNG>"
    ocr: str = "auto"  # "auto", "rapidocr", "tesseract" or "aus"
    ocr_dpi: int = ocrmodul.OCR_DPI
    codes: bool = True  # blank barcodes and QR codes (extra "codes")
    unlesbar: str = "schwaerzen"  # lines OCR could not read: "schwaerzen" or "lassen"
    ner: bool = False  # people and places the rules do not know (extra "ner")
    fachbegriffe: bool = True  # protect medical terms from NER and fuzzy OCR matching
    unscharf_text: bool = True  # text files: fuzzy search for misspelt identifiers (transcripts)
    aufbereiten: str = "auto"  # image-only pages: "auto" (suggested clean-up) or "aus"
    aufbereitung: dict[int, aufmodul.Aufbereitung] | None = None  # explicit choice per page
    vorschau: str = "auto"  # "auto" (pages that needed OCR), "immer", "nie"
    schwaerzen: Schwaerzoptionen = field(default_factory=Schwaerzoptionen)

    def als_dict(self) -> dict[str, object]:
        return {
            "datumsform": self.datumsform,
            "textdatei": self.textdatei,
            "markdown": self.markdown,
            "textebene": self.schwaerzen.textebene,
            "rastern": self.schwaerzen.rastern,
            "dpi": self.schwaerzen.dpi if self.schwaerzen.rastern else None,
            "vermerk": self.schwaerzen.vermerk,
            "ocr": self.ocr,
            "codes": self.codes,
            "ner": self.ner,
            "fachbegriffe": self.fachbegriffe,
            "unscharf_text": self.unscharf_text,
            "aufbereiten": self.aufbereiten,
        }


@dataclass
class Ergebnis:
    quelle: Path
    bericht: Pruefbericht
    fundstellen: list[Fundstelle]
    kennung: str
    ziel_pdf: Path | None = None
    ziel_txt: Path | None = None
    ziel_md: Path | None = None
    ziel_bericht: Path | None = None
    vorschau: list[Path] = field(default_factory=list)
    text: str = ""
    ist_text: bool = False  # the source was a text file; ``ziel_txt`` is the output itself

    @property
    def freigegeben(self) -> bool:
        return self.ziel_pdf is not None or (self.ist_text and self.ziel_txt is not None)

    @property
    def ziel(self) -> Path | None:
        """The released file: the PDF, or the text for a text source."""
        return self.ziel_txt if self.ist_text else self.ziel_pdf


def zielpfad(quelle: Path, opt: Ablaufoptionen, endung: str = ".pdf") -> Path:
    ordner = opt.ziel_ordner or quelle.parent
    return ordner / f"{quelle.stem}{opt.suffix}{endung}"


@dataclass
class Vorlage:
    """Phase zero: the file is open and its pages are known, nothing is read in detail.

    Pages without a text layer (scans, photos) are candidates for the clean-up step;
    each comes with an automatic suggestion.  Call ``schliessen`` when done.
    """

    quelle: Path
    doc: pymupdf.Document
    foto: bool
    kandidaten: list[int]
    vorschlaege: dict[int, aufmodul.Aufbereitung]

    @property
    def seitenzahl(self) -> int:
        return len(self.doc)

    def seitenbild(self, nummer: int, dpi: int = 100) -> bytes:
        return self.doc[nummer].get_pixmap(dpi=dpi, alpha=False).tobytes("png")

    def vorschau(self, nummer: int, a: aufmodul.Aufbereitung, dpi: int = 100) -> bytes:
        return aufmodul.vorschau_png(self.doc[nummer], a, dpi)

    def seitenbreite(self, nummer: int, a: aufmodul.Aufbereitung) -> float:
        r = self.doc[nummer].rect
        return r.width if a.drehung in (0, 180) else r.height

    def schliessen(self) -> None:
        try:
            self.doc.close()
        except Exception:  # already closed
            pass


def lade(pfad: str | Path, opt: Ablaufoptionen | None = None) -> Vorlage:
    """Open a PDF or image and find the pages worth cleaning up before OCR."""
    opt = opt or Ablaufoptionen()
    quelle = Path(pfad)
    doc = oeffne(quelle)
    foto = quelle.suffix.lower() in BILDENDUNGEN | HEIC_ENDUNGEN
    kandidaten = [i for i, page in enumerate(doc) if not lese_seite(page, i).hat_textebene]
    vorschlaege: dict[int, aufmodul.Aufbereitung] = {}
    if aufmodul.verfuegbar() and opt.aufbereiten != "aus":
        for i in kandidaten:
            bild, skala = aufmodul.rendere(doc[i], 150)
            vorschlaege[i] = aufmodul.vorschlag(bild, foto, skala, aufmodul.native_dpi(doc[i]))
    return Vorlage(quelle, doc, foto, kandidaten, vorschlaege)


def _aufbereitungsplan(
    doc: pymupdf.Document, quelle: Path, opt: Ablaufoptionen,
    explizit: dict[int, aufmodul.Aufbereitung] | None,
) -> dict[int, aufmodul.Aufbereitung]:
    """Which pages get which clean-up: the explicit choice, else the suggestion for
    every image-only page when ``aufbereiten`` is "auto"."""
    plan = {n: a for n, a in (explizit or opt.aufbereitung or {}).items() if a.aktiv}
    if opt.aufbereiten == "auto" and aufmodul.verfuegbar() and explizit is None \
            and opt.aufbereitung is None:
        foto = quelle.suffix.lower() in BILDENDUNGEN | HEIC_ENDUNGEN
        for i, page in enumerate(doc):
            if i in plan or lese_seite(page, i).hat_textebene:
                continue
            bild, skala = aufmodul.rendere(page, 150)
            a = aufmodul.vorschlag(bild, foto, skala, aufmodul.native_dpi(page))
            if a.aktiv:
                plan[i] = a
    return plan


@dataclass
class _Seite:
    nummer: int
    text: Seitentext
    ocr: Seitentext | None = None
    fundstellen: list[Fundstelle] = field(default_factory=list)
    fachrects: list[tuple[float, float, float, float]] = field(default_factory=list)
    fachspannen: list[tuple[int, int]] = field(default_factory=list)
    fachanzahl: int = 0
    korrektur: str | None = None  # the OCR text as the user corrected it
    korrektur_treffer: list[Fundstelle] = field(default_factory=list)
    ocr_dpi: int = ocrmodul.OCR_DPI  # resolution the page was (and is re-)read at
    ocr_max_pixel: int = ocrmodul.MAX_PIXEL


def _lese_seiten(
    doc: pymupdf.Document, regeln: list[Regel], ziele: list[Ziel], opt: Ablaufoptionen,
    backend: ocrmodul.OCRBackend | None, vergabe: Pseudonymvergabe | None = None,
    ner: nermodul.NER | None = None, fachbegriffe: fachmodul.Fachbegriffe | None = None,
    verschont: list[str] | None = None, faktoren: dict[int, int] | None = None,
) -> tuple[list[_Seite], list[int]]:
    """Text layer and, where needed and possible, OCR per page; returns the pages and
    the numbers (1-based) of pages that needed OCR but got none.  ``faktoren`` holds
    the upscaling factor of cleaned-up pages: they are read at that multiple of the
    OCR resolution."""
    seiten: list[_Seite] = []
    ohne_text: list[int] = []
    for i, page in enumerate(doc):
        textebene = lese_seite(page, i)
        eintrag = _Seite(i, textebene)
        faktor = max(1, (faktoren or {}).get(i, 1))
        eintrag.ocr_dpi = opt.ocr_dpi * faktor
        eintrag.ocr_max_pixel = ocrmodul.MAX_PIXEL * faktor
        eintrag.fundstellen = finde(textebene, regeln)
        if fachbegriffe is not None and textebene.hat_textebene:
            _merke_fachbegriffe(eintrag, textebene, fachbegriffe)
        if ner is not None and vergabe is not None and textebene.hat_textebene:
            neue = nermodul.finde_ner(textebene, ner, vergabe, eintrag.fundstellen, "text",
                                      fachbegriffe, verschont)
            eintrag.fundstellen += neue
        if ocrmodul.braucht_ocr(page, textebene):
            if backend is None:
                ohne_text.append(i + 1)
            else:
                eintrag.ocr = ocrmodul.erkenne_seite(page, backend, i, eintrag.ocr_dpi,
                                                     max_pixel=eintrag.ocr_max_pixel)
                if fachbegriffe is not None:
                    _merke_fachbegriffe(eintrag, eintrag.ocr, fachbegriffe)
                genau = finde(eintrag.ocr, regeln)
                for f in genau:
                    f.quelle = "ocr"
                eintrag.fundstellen += genau + finde_unscharf(eintrag.ocr, ziele, genau,
                                                              fachbegriffe)
                if ner is not None and vergabe is not None:
                    ocr_treffer = [f for f in eintrag.fundstellen if f.quelle == "ocr"]
                    neue = nermodul.finde_ner(eintrag.ocr, ner, vergabe, ocr_treffer, "ocr",
                                              fachbegriffe, verschont)
                    eintrag.fundstellen = [f for f in eintrag.fundstellen
                                           if f.quelle != "ocr" or f in ocr_treffer] + neue
                if opt.unlesbar == "schwaerzen":
                    belegt = [(f.start, f.ende) for f in eintrag.fundstellen if f.quelle == "ocr"]
                    for u in ocrmodul.unlesbare_regionen(eintrag.ocr):
                        if not any(u.start < y and u.ende > x for x, y in belegt):
                            eintrag.fundstellen.append(u)
        if opt.codes and codemodul.verfuegbar():
            eintrag.fundstellen += codemodul.finde_codes(page, i)
        seiten.append(eintrag)
    return seiten, ohne_text


def _merke_fachbegriffe(eintrag: _Seite, seite: Seitentext,
                        fachbegriffe: fachmodul.Fachbegriffe) -> None:
    """Remember where medical terms sit, for the green marks of the review step."""
    spannen = fachbegriffe.spannen(seite.text)
    eintrag.fachanzahl += len(spannen)
    eintrag.fachspannen += spannen
    for a, b in spannen:
        eintrag.fachrects += [tuple(r) for r in seite.rects(a, b)]


def markdown_aus_text(text: str) -> str:
    """The reading-order text as Markdown: page markers become headings."""
    zeilen = ["# Anonymisierter Text", ""]
    for zeile in text.splitlines():
        if zeile.startswith("=== Seite ") and zeile.endswith(" ==="):
            zeilen.append("## " + zeile.strip("= ").strip())
        elif zeile.strip() == "--- Bildtext (OCR, ungeprüft) ---":
            zeilen.append("## Bildtext (OCR, ungeprüft)")
        else:
            zeilen.append(zeile)
    return "\n".join(zeilen).rstrip() + "\n"


def _anonym(text: str, fundstellen: list[Fundstelle]) -> str:
    """``text`` with the hits replaced, blank runs collapsed."""
    return re.sub(r"\n{3,}", "\n\n", textmodul.ersetze(text, fundstellen)).strip()


def _ocr_text_anonym(s: _Seite) -> str:
    """The OCR text of a page with pseudonyms: the user's correction when there is one."""
    if s.korrektur is not None:
        return _anonym(s.korrektur, s.korrektur_treffer)
    assert s.ocr is not None
    quellen = ("ocr",) + (("manuell",) if not s.text.hat_textebene else ())
    treffer = [f for f in s.fundstellen if f.quelle in quellen and f.ende > f.start]
    return _anonym(s.ocr.text, treffer)


def _text(seiten: list[_Seite]) -> str:
    """Reading-order text with pseudonyms: text layer first, OCR text as its own block."""
    def block(s: _Seite, text: str) -> str:
        return f"=== Seite {s.nummer + 1} ===\n\n{text}"

    bloecke = []
    for s in seiten:
        quellen = ("text",) + (("manuell",) if s.text.hat_textebene else ())
        treffer = [f for f in s.fundstellen if f.quelle in quellen and f.ende > f.start]
        bloecke.append(block(s, _anonym(s.text.text, treffer)))
    teile = "\n\n".join(bloecke) + "\n"
    ocr_seiten = [s for s in seiten if s.ocr is not None]
    if ocr_seiten:
        ocr_text = "\n\n".join(block(s, _ocr_text_anonym(s)) for s in ocr_seiten) + "\n"
        teile = teile.rstrip("\n") + "\n\n--- Bildtext (OCR, ungeprüft) ---\n\n" + ocr_text
    return teile


def _gegenprobe_bilder(
    doc: pymupdf.Document, seiten: list[_Seite], regeln: list[Regel], ziele: list[Ziel],
    backend: ocrmodul.OCRBackend | None, opt: Ablaufoptionen, gegenprobe: Gegenprobe,
    toleriert: set[tuple[str, str]] | None = None, pseudonyme: list[str] | None = None,
) -> None:
    """OCR the output pages that were read by OCR and look for leftovers (exact and
    fuzzy); re-scan codes."""
    toleriert = toleriert or set()
    pseudonyme = pseudonyme or []
    for s in seiten:
        if s.nummer >= len(doc):
            continue
        if s.ocr is not None and backend is not None:
            nachher = ocrmodul.erkenne_seite(doc[s.nummer], backend, s.nummer, s.ocr_dpi,
                                             max_pixel=s.ocr_max_pixel)
            spannen = pseudonymspannen_tolerant(nachher.text, pseudonyme)
            genau = finde(nachher, regeln)
            fach = fachmodul.standard() if opt.fachbegriffe else None
            for f in genau + finde_unscharf(nachher, ziele, genau, fach):
                if toleranzschluessel(f.regel, f.text) in toleriert \
                        or in_pseudonym(f.start, f.ende, spannen):
                    continue
                eintrag = f.ohne_klartext()
                eintrag["quelle"] = "ocr"
                gegenprobe.resttreffer.append(eintrag)
        if opt.codes and codemodul.verfuegbar():
            for f in codemodul.finde_codes(doc[s.nummer], s.nummer):
                gegenprobe.resttreffer.append(f.ohne_klartext())


@dataclass
class Vorbereitung:
    """Phase one: the document is read and the hits are known, nothing is changed yet.

    A user interface can drop hits (``verwerfe``) and add boxes (``kasten``)
    before :func:`abschliessen` applies them.  Call ``schliessen`` when the
    object is discarded without finishing.
    """

    ist_text: ClassVar[bool] = False

    quelle: Path
    patient: Patient
    opt: Ablaufoptionen
    speicher: Pseudonymspeicher
    eigener_speicher: bool
    kennung: str
    vergabe: Pseudonymvergabe
    regeln: list[Regel]
    ziele: list[Ziel]
    backend: ocrmodul.OCRBackend | None
    doc: pymupdf.Document
    original: pymupdf.Document
    seiten: list[_Seite]
    ohne_text: list[int]
    warnungen: list[str] = field(default_factory=list)
    verworfen: list[Fundstelle] = field(default_factory=list)
    aufbereitet: dict[int, aufmodul.Aufbereitung] = field(default_factory=dict)
    abgeschlossen: bool = False

    @property
    def fundstellen(self) -> list[Fundstelle]:
        return [f for s in self.seiten for f in s.fundstellen]

    @property
    def ocr_seiten(self) -> list[int]:
        return [s.nummer + 1 for s in self.seiten if s.ocr is not None]

    def fachbegriffe_rects(self, nummer: int) -> list[tuple[float, float, float, float]]:
        """Rectangles of the medical terms on page ``nummer`` (0-based), for display."""
        return list(self.seiten[nummer].fachrects)

    def fachbegriffe_spannen(self, nummer: int) -> list[tuple[int, int]]:
        """Text offsets of the medical terms on page ``nummer`` (0-based)."""
        return list(self.seiten[nummer].fachspannen)

    @property
    def fachbegriffe_anzahl(self) -> int:
        return sum(s.fachanzahl for s in self.seiten)

    def seitenbild(self, nummer: int, dpi: int = 100) -> bytes:
        """PNG of the original page for the preview."""
        return self.original[nummer].get_pixmap(dpi=dpi, alpha=False).tobytes("png")

    def verwerfe(self, fundstelle: Fundstelle) -> None:
        """Drop a hit the user judged a false positive; the re-check will tolerate
        this rule/text pair.  The hit stays known (``verworfen``) and can be restored."""
        for s in self.seiten:
            if fundstelle in s.fundstellen:
                s.fundstellen.remove(fundstelle)
                self.verworfen.append(fundstelle)
                return

    def wiederherstelle(self, fundstelle: Fundstelle) -> None:
        """Take a discarded hit back."""
        if fundstelle in self.verworfen:
            self.verworfen.remove(fundstelle)
            self.seiten[fundstelle.seite].fundstellen.append(fundstelle)

    @property
    def toleriert(self) -> set[tuple[str, str]]:
        return {toleranzschluessel(f.regel, f.text) for f in self.verworfen if f.text}

    def ocr_text(self, nummer: int) -> str:
        """The recognised text of page ``nummer`` as it will be written out: the user's
        correction when there is one, else the OCR reading; "" without OCR."""
        s = self.seiten[nummer]
        if s.korrektur is not None:
            return s.korrektur
        return s.ocr.text if s.ocr is not None else ""

    def setze_ocr_text(self, nummer: int, text: str) -> list[Fundstelle]:
        """Replace the OCR text of page ``nummer`` by the user's correction.

        The correction feeds the text file, the Markdown and the invisible text layer;
        the redaction in the image keeps the OCR boxes (mask what the OCR misread).
        The rules and the fuzzy search run over the corrected text; their hits are
        returned and shown with source ``korrektur``.
        """
        s = self.seiten[nummer]
        if s.ocr is None:
            raise ValueError("Diese Seite hat keinen OCR-Text.")
        if text.strip() == s.ocr.text.strip():
            s.korrektur, s.korrektur_treffer = None, []
            return []
        s.korrektur = text
        seite = textmodul.textseite(text, nummer)
        treffer = finde(seite, self.regeln)
        if self.opt.unscharf_text:
            fach = fachmodul.standard() if self.opt.fachbegriffe else None
            treffer += finde_unscharf(seite, self.ziele, treffer, fach, grossschreibung=True)
        for f in treffer:
            f.quelle = "korrektur"
            f.rects = []
        s.korrektur_treffer = treffer
        return treffer

    @property
    def korrektur_treffer(self) -> list[Fundstelle]:
        return [f for s in self.seiten for f in s.korrektur_treffer]

    def unsichere_rects(self, nummer: int) -> list[tuple[float, float, float, float]]:
        """Boxes of OCR words read with low confidence on page ``nummer``: uncertain,
        worth a look, not hits."""
        seite = self.seiten[nummer]
        if seite.ocr is None:
            return []
        rects: list[tuple[float, float, float, float]] = []
        for (a, b), konf in zip(seite.ocr.wortspannen, seite.ocr.konfidenzen, strict=True):
            if konf < ocrmodul.UNSICHER_KONFIDENZ:
                rects += [tuple(r) for r in seite.ocr.rects(a, b)]
        return rects

    def kasten(self, nummer: int, rect: tuple[float, float, float, float],
               pseudonym: str = "[geschwärzt]") -> list[Fundstelle]:
        """Add a manual box on page ``nummer`` (0-based); returns the hits it became."""
        seite = self.seiten[nummer]
        bildseite = seite.ocr is not None and not seite.text.hat_textebene
        basis = seite.ocr if bildseite else seite.text
        neue = kasten_zu_fundstellen(basis, rect, pseudonym)
        for f in neue:
            f.quelle = "manuell"
        seite.fundstellen += neue
        return neue

    def maske(self, nummer: int, rect: tuple[float, float, float, float]) -> list[Fundstelle]:
        """Black out a region of page ``nummer``: text and pixels under it vanish, nothing
        is written into it, and hits lying wholly inside it are dropped as redundant.
        The text output shows ``[geschwärzt]`` in its place."""
        seite = self.seiten[nummer]
        innen = pymupdf.Rect(rect)
        seite.fundstellen = [
            f for f in seite.fundstellen
            if f.regel == "maske" or not f.rects
            or not all(innen.contains(pymupdf.Rect(r)) for r in f.rects)
        ]
        neue = self.kasten(nummer, rect, "[geschwärzt]")
        for f in neue:
            f.regel = "maske"
        return neue

    def schliessen(self) -> None:
        for d in (self.doc, self.original):
            try:
                d.close()
            except Exception:  # already closed
                pass
        if self.eigener_speicher:
            self.speicher.close()


@dataclass
class _Ruestzeug:
    """What detection needs for one patient: identity, pseudonyms, rules, helpers."""

    kennung: str
    vergabe: Pseudonymvergabe
    regeln: list[Regel]
    ziele: list[Ziel]
    warnungen: list[str]
    ner: nermodul.NER | None
    fachbegriffe: fachmodul.Fachbegriffe | None


def _ruestzeug(patient: Patient, opt: Ablaufoptionen, speicher: Pseudonymspeicher) -> _Ruestzeug:
    kennung = speicher.kennung_fuer(patient, opt.abgleich)
    vergabe = Pseudonymvergabe(patient, kennung.kennung, opt.datumsform,
                               geheimnis=speicher.geheimnis)
    warnungen: list[str] = []
    if kennung.verknuepft and kennung.abgleich is not None:
        a = kennung.abgleich
        felder = ", ".join(f"{k} {v:.2f}" for k, v in a.felder.items())
        warnungen.append(
            f"Patient dem vorhandenen Eintrag {a.kennung} zugeordnet"
            f" (Abgleich {a.score:.2f}: {felder})."
        )
    ner = None
    if opt.ner:
        if nermodul.verfuegbar():
            ner = nermodul.NER()
        else:
            warnungen.append("NER gewünscht, aber spaCy oder das deutsche Modell fehlt"
                             " (Extra 'ner', siehe Anleitung); ohne NER fortgefahren.")
    fachbegriffe = fachmodul.standard() if opt.fachbegriffe else None
    return _Ruestzeug(kennung.kennung, vergabe, regeln_fuer(patient, vergabe),
                      ziele_fuer(patient, vergabe), warnungen, ner, fachbegriffe)


def _ner_warnung(r: _Ruestzeug, fundstellen: list[Fundstelle], verschont: list[str]) -> str | None:
    if r.ner is None:
        return None
    ner_treffer = sum(1 for f in fundstellen if f.regel.startswith("ner."))
    schutz = (f", {len(verschont)} Kandidaten als Fachbegriffe verschont"
              f" ({r.fachbegriffe.anzahl} Begriffe)" if r.fachbegriffe is not None else "")
    return (f"NER ({r.ner.name}): {ner_treffer} zusätzliche Personen/Orte gefunden{schutz};"
            " NER rät, Treffer im Prüfschritt ansehen.")


def vorbereiten(
    pfad: str | Path,
    patient: Patient,
    opt: Ablaufoptionen | None = None,
    speicher: Pseudonymspeicher | None = None,
    aufbereitung: dict[int, aufmodul.Aufbereitung] | None = None,
) -> Vorbereitung | Textvorbereitung:
    """Phase one of :func:`anonymisiere`: read the file and find the hits.

    PDFs and images yield a :class:`Vorbereitung`, text files (``.txt``,
    ``.md``, ``.srt``, ``.vtt``) a :class:`Textvorbereitung`.  ``aufbereitung``
    (per page, 0-based) replaces image-only pages by their cleaned-up image
    before OCR; without it the suggestion is applied when ``opt.aufbereiten``
    is "auto".
    """
    opt = opt or Ablaufoptionen()
    quelle = Path(pfad)
    eigener_speicher = speicher is None
    speicher = speicher or Pseudonymspeicher()
    try:
        r = _ruestzeug(patient, opt, speicher)
        if textmodul.ist_textdatei(quelle):
            return _vorbereiten_text(quelle, patient, opt, speicher, eigener_speicher, r)
        verschont: list[str] = []
        backend = ocrmodul.backend(opt.ocr)
        doc = oeffne(quelle)
        original = oeffne(quelle)
        try:
            plan = _aufbereitungsplan(doc, quelle, opt, aufbereitung)
            for nummer in sorted(plan):
                aufmodul.seiten_ersetzen([doc, original], nummer, plan[nummer], opt.ocr_dpi)
            faktoren = {n: a.hochskalieren for n, a in plan.items() if a.hochskalieren > 1}
            seiten, ohne_text = _lese_seiten(doc, r.regeln, r.ziele, opt, backend, r.vergabe,
                                             r.ner, r.fachbegriffe, verschont, faktoren)
        except Exception:
            doc.close()
            original.close()
            raise
    except Exception:
        if eigener_speicher:
            speicher.close()
        raise
    warnungen = list(r.warnungen)
    for nummer in sorted(plan):
        warnungen.append(f"Seite {nummer + 1} vor der OCR aufbereitet:"
                         f" {aufmodul.beschreibung(plan[nummer])}.")
    if ohne_text:
        warnungen.append(
            "Seite(n) ohne Textebene und ohne OCR: " + ", ".join(map(str, ohne_text))
            + " – OCR-Backend installieren (Extra 'ocr' oder Tesseract) oder --ocr prüfen."
        )
    ocr_seiten = [s.nummer + 1 for s in seiten if s.ocr is not None]
    hinweis = _ner_warnung(r, [f for s in seiten for f in s.fundstellen], verschont)
    if hinweis:
        warnungen.append(hinweis)
    if ocr_seiten:
        unsicher = sum(s.ocr.unsichere_woerter for s in seiten if s.ocr is not None)
        unlesbar = sum(1 for s in seiten for f in s.fundstellen if f.regel == "ocr.unlesbar")
        warnungen.append(
            f"Scan/Foto: Seite(n) {', '.join(map(str, ocr_seiten))} per OCR gelesen"
            f" ({backend.name if backend else '?'}, {unsicher} unsichere Wörter"
            f", {unlesbar} unlesbare Zeilen geschwärzt)."
            " Handschrift wird nicht erkannt; Vorschaubilder vor der Weitergabe prüfen."
        )
    return Vorbereitung(
        quelle=quelle, patient=patient, opt=opt, speicher=speicher,
        eigener_speicher=eigener_speicher, kennung=r.kennung, vergabe=r.vergabe,
        regeln=r.regeln, ziele=r.ziele, backend=backend, doc=doc, original=original,
        seiten=seiten, ohne_text=ohne_text, warnungen=warnungen, aufbereitet=plan,
    )


# -- text files ------------------------------------------------------------------------------------


@dataclass
class Textvorbereitung:
    """Phase one for a text file (transcript, note, subtitles).

    Same surface as :class:`Vorbereitung` as far as a user interface needs it:
    ``fundstellen``, ``verwerfe``, ``toleriert``, ``warnungen``; instead of
    boxes the user marks spans (``markiere``).  The hits' offsets refer to the
    analysis copy, which has the same length as ``text``.
    """

    ist_text: ClassVar[bool] = True

    quelle: Path
    patient: Patient
    opt: Ablaufoptionen
    speicher: Pseudonymspeicher
    eigener_speicher: bool
    kennung: str
    vergabe: Pseudonymvergabe
    regeln: list[Regel]
    ziele: list[Ziel]
    text: str
    seiten: list[_Seite]
    ausgabe_endung: str = ".txt"  # the input's own for text and subtitles, .txt for JSON/e-mail
    ohne_text: list[int] = field(default_factory=list)
    warnungen: list[str] = field(default_factory=list)
    verworfen: list[Fundstelle] = field(default_factory=list)
    abgeschlossen: bool = False

    @property
    def fundstellen(self) -> list[Fundstelle]:
        return sorted((f for s in self.seiten for f in s.fundstellen), key=lambda f: f.start)

    @property
    def ocr_seiten(self) -> list[int]:
        return []

    def fachbegriffe_rects(self, nummer: int) -> list[tuple[float, float, float, float]]:
        return []

    def fachbegriffe_spannen(self, nummer: int) -> list[tuple[int, int]]:
        return list(self.seiten[nummer].fachspannen)

    @property
    def fachbegriffe_anzahl(self) -> int:
        return sum(s.fachanzahl for s in self.seiten)

    def verwerfe(self, fundstelle: Fundstelle) -> None:
        for s in self.seiten:
            if fundstelle in s.fundstellen:
                s.fundstellen.remove(fundstelle)
                self.verworfen.append(fundstelle)
                return

    def wiederherstelle(self, fundstelle: Fundstelle) -> None:
        if fundstelle in self.verworfen:
            self.verworfen.remove(fundstelle)
            self.seiten[fundstelle.seite].fundstellen.append(fundstelle)

    @property
    def toleriert(self) -> set[tuple[str, str]]:
        return {toleranzschluessel(f.regel, f.text) for f in self.verworfen if f.text}

    def unsichere_rects(self, nummer: int) -> list[tuple[float, float, float, float]]:
        return []

    def markiere(self, nummer: int, start: int, ende: int,
                 pseudonym: str = "[geschwärzt]") -> Fundstelle:
        """Mark ``[start, ende)`` by hand; hits it overlaps are replaced by it."""
        seite = self.seiten[nummer]
        text = seite.text.text  # the analysis copy, same length as ``self.text``
        start, ende = max(0, start), min(ende, len(text))
        while start < ende and text[start].isspace():
            start += 1
        while ende > start and text[ende - 1].isspace():
            ende -= 1
        if ende <= start:
            raise ValueError("Leere Auswahl.")
        seite.fundstellen = [f for f in seite.fundstellen
                             if not (start < f.ende and ende > f.start)]
        f = Fundstelle(seite=nummer, start=start, ende=ende, text=self.text[start:ende],
                       art=Art.MANUELL, regel="manuell", pseudonym=pseudonym, quelle="manuell")
        seite.fundstellen.append(f)
        return f

    def schliessen(self) -> None:
        if self.eigener_speicher:
            self.speicher.close()


def _vorbereiten_text(
    quelle: Path, patient: Patient, opt: Ablaufoptionen, speicher: Pseudonymspeicher,
    eigener_speicher: bool, r: _Ruestzeug,
) -> Textvorbereitung:
    """Rules, vocabulary, fuzzy search and NER over one text file, in that order: a
    misspelt known name must get the patient's pseudonym, not a NER code.  Number
    words are rewritten as digits first; hits touching them are widened to the words."""
    text, endung = textmodul.lese_quelle(quelle)
    norm = zahlwoerter.normalisiere(textmodul.analysetext(text, endung))
    seite = textmodul.textseite(norm.text)
    eintrag = _Seite(0, seite)
    eintrag.fundstellen = finde(seite, r.regeln)
    if r.fachbegriffe is not None:
        _merke_fachbegriffe(eintrag, seite, r.fachbegriffe)
    if opt.unscharf_text:
        eintrag.fundstellen += finde_unscharf(seite, r.ziele, eintrag.fundstellen, r.fachbegriffe,
                                              grossschreibung=True, quelle="unscharf")
    verschont: list[str] = []
    if r.ner is not None:
        eintrag.fundstellen += nermodul.finde_ner(seite, r.ner, r.vergabe, eintrag.fundstellen,
                                                  "text", r.fachbegriffe, verschont)
    eintrag.fundstellen = _auf_zahlwoerter_erweitert(eintrag.fundstellen, norm)
    warnungen = list(r.warnungen)
    hinweis = _ner_warnung(r, eintrag.fundstellen, verschont)
    if hinweis:
        warnungen.append(hinweis)
    unscharfe = sum(1 for f in eintrag.fundstellen if f.quelle == "unscharf")
    if unscharfe:
        warnungen.append(f"Unscharfe Suche: {unscharfe} abweichend geschriebene Treffer"
                         " (Transkript, Tippfehler); im Prüfschritt ansehen.")
    return Textvorbereitung(
        quelle=quelle, patient=patient, opt=opt, speicher=speicher,
        eigener_speicher=eigener_speicher, kennung=r.kennung, vergabe=r.vergabe,
        regeln=r.regeln, ziele=r.ziele, text=text, seiten=[eintrag], ausgabe_endung=endung,
        warnungen=warnungen,
    )


def _auf_zahlwoerter_erweitert(fundstellen: list[Fundstelle],
                               norm: zahlwoerter.Normalisiert) -> list[Fundstelle]:
    """A hit on "21. Juli 1959" that stands for "einundzwanzigsten Juli neunzehnhundert-
    neunundfünfzig" must cover all those words; after widening, overlapping hits are
    reduced to the longest."""
    for f in fundstellen:
        if f.ende > f.start:
            a, b = norm.erweitere(f.start, f.ende)
            if (a, b) != (f.start, f.ende):
                f.start, f.ende, f.text = a, b, norm.text[a:b]
    belegt: list[tuple[int, int]] = []
    behalten: list[Fundstelle] = []
    for f in sorted(fundstellen, key=lambda f: (-(f.ende - f.start), f.start)):
        if f.ende > f.start and any(f.start < y and f.ende > x for x, y in belegt):
            continue
        belegt.append((f.start, f.ende))
        behalten.append(f)
    return sorted(behalten, key=lambda f: f.start)


def gegenprobe_text(
    text: str, endung: str, regeln: list[Regel], ziele: list[Ziel] | None = None,
    fachbegriffe: fachmodul.Fachbegriffe | None = None,
    toleriert: set[tuple[str, str]] | None = None, pseudonyme: list[str] | None = None,
) -> Gegenprobe:
    """Re-run the rules (and, with ``ziele``, the fuzzy search) over an anonymised text."""
    toleriert = toleriert or set()
    analyse = zahlwoerter.normalisiere(textmodul.analysetext(text, endung)).text
    seite = textmodul.textseite(analyse)
    spannen = pseudonymspannen(analyse, pseudonyme or [])
    genau = finde(seite, regeln)
    rest = list(genau)
    if ziele is not None:
        rest += finde_unscharf(seite, ziele, genau, fachbegriffe, grossschreibung=True,
                               quelle="unscharf")
    g = Gegenprobe()
    for f in rest:
        if toleranzschluessel(f.regel, f.text) in toleriert \
                or in_pseudonym(f.start, f.ende, spannen):
            continue
        g.resttreffer.append(f.ohne_klartext())
    return g


def gegenprobe_textdatei(pfad: str | Path, regeln: list[Regel],
                         pseudonyme: list[str] | None = None) -> Gegenprobe:
    """``anonymed pruefen`` for a text file: the rules alone, nothing tolerated; inside
    ``pseudonyme`` (at least the patient's code) no rule counts."""
    text, endung = textmodul.lese_quelle(pfad)
    return gegenprobe_text(text, endung, regeln, pseudonyme=pseudonyme)


def _abschliessen_text(v: Textvorbereitung) -> Ergebnis:
    """Phase two for a text file: replace, check, write, report."""
    v.abgeschlossen = True
    opt, quelle, warnungen = v.opt, v.quelle, list(v.warnungen)
    try:
        fundstellen = v.fundstellen
        v.vergabe.registriere_treffer(fundstellen)
        ausgabe = textmodul.ersetze(v.text, fundstellen)
        pseudonyme = list(v.vergabe.zuordnung) + [f.pseudonym for f in fundstellen]
        fach = fachmodul.standard() if opt.fachbegriffe else None
        gegenprobe = gegenprobe_text(ausgabe, v.ausgabe_endung, v.regeln,
                                     v.ziele if opt.unscharf_text else None, fach,
                                     v.toleriert, pseudonyme)
        v.speicher.merke(v.patient, v.vergabe)
        ziel = zielpfad(quelle, opt, v.ausgabe_endung)
        ergebnis = Ergebnis(quelle=quelle, bericht=None, fundstellen=fundstellen,  # type: ignore[arg-type]
                            kennung=v.kennung, text=ausgabe, ist_text=True)
        if gegenprobe.bestanden:
            ziel.parent.mkdir(parents=True, exist_ok=True)
            ziel.write_text(ausgabe, encoding="utf-8")
            ergebnis.ziel_txt = ziel
            if opt.markdown and ziel.suffix.lower() != ".md":
                ergebnis.ziel_md = zielpfad(quelle, opt, ".md")
                ergebnis.ziel_md.write_text(ausgabe, encoding="utf-8")
        else:
            warnungen.append("Nicht freigegeben: keine Ausgabe geschrieben.")
        ergebnis.bericht = erstelle_bericht(quelle, ergebnis.ziel_txt, 1, True, fundstellen,
                                            gegenprobe, opt.als_dict(), warnungen)
        if opt.bericht:
            ergebnis.ziel_bericht = zielpfad(quelle, opt, ".bericht.json")
            speichere_bericht(ergebnis.bericht, ergebnis.ziel_bericht)
        return ergebnis
    finally:
        if v.eigener_speicher:
            v.speicher.close()


def _textebene_einbetten(doc: pymupdf.Document, seiten: list[_Seite], gerastert: set[int],
                         opt: Schwaerzoptionen) -> None:
    """Invisible text for pages that have none of their own: OCR pages get their OCR
    words; rasterised pages get their text-layer words and their pseudonyms as well."""
    for s in seiten:
        page = doc[s.nummer]
        if s.nummer in gerastert:
            unsichtbare_textebene(page, s.text, s.fundstellen, mit_pseudonymen=True, opt=opt)
        if s.ocr is not None and s.korrektur is not None:
            unsichtbarer_textblock(page, _ocr_text_anonym(s), s.ocr, opt=opt)
        elif s.ocr is not None:
            ocr_treffer = [f for f in s.fundstellen if f.quelle in ("ocr", "manuell")]
            unsichtbare_textebene(page, s.ocr, ocr_treffer,
                                  mit_pseudonymen=s.nummer in gerastert, opt=opt)


def abschliessen(v: Vorbereitung | Textvorbereitung) -> Ergebnis:
    """Phase two: redact, clean, save, check, report.  Closes the documents."""
    if v.abgeschlossen:
        raise RuntimeError("Diese Vorbereitung wurde bereits abgeschlossen.")
    if isinstance(v, Textvorbereitung):
        return _abschliessen_text(v)
    v.abgeschlossen = True
    opt, quelle, seiten, warnungen = v.opt, v.quelle, v.seiten, list(v.warnungen)
    try:
        fundstellen = v.fundstellen + v.korrektur_treffer
        v.vergabe.registriere_treffer(fundstellen)
        text = _text(seiten)
        try:
            gerastert: set[int] = set()
            for s in seiten:
                lage = schwaerze_seite(v.doc[s.nummer], s.text, s.fundstellen, opt.schwaerzen)
                if not lage.lage_intakt:
                    rastere_seite(v.doc, s.nummer, v.original, s.text, s.fundstellen,
                                  opt.schwaerzen)
                    gerastert.add(s.nummer)
                    warnungen.append(
                        f"Seite {s.nummer + 1}: Textlayout nach der Redaktion verschoben,"
                        " Seite deshalb gerastert."
                    )
            if opt.schwaerzen.textebene and not opt.schwaerzen.rastern:
                _textebene_einbetten(v.doc, seiten, gerastert, opt.schwaerzen)
            bereinige(v.doc, vermerk=opt.schwaerzen.vermerk)
            ausgabe = v.doc
            if opt.schwaerzen.rastern:
                ausgabe = rastere(v.doc, opt.schwaerzen.dpi)
                if opt.schwaerzen.textebene:
                    _textebene_einbetten(ausgabe, seiten, {s.nummer for s in seiten},
                                         opt.schwaerzen)
                bereinige(ausgabe, vermerk=opt.schwaerzen.vermerk)
                warnungen.append("Ausgabe gerastert.")
            ziel = zielpfad(quelle, opt)
            ziel.parent.mkdir(parents=True, exist_ok=True)
            speichere(ausgabe, ziel)
            if ausgabe is not v.doc:
                ausgabe.close()
        finally:
            v.doc.close()
            v.original.close()

        ocr_seiten = v.ocr_seiten
        vorschau: list[Path] = []
        with pymupdf.open(ziel) as kontrolle:
            pseudonyme = list(v.vergabe.zuordnung) + [f.pseudonym for f in fundstellen]
            gegenprobe = pruefe(kontrolle, v.regeln,
                                zusatztexte=[text] if opt.textdatei else [],
                                toleriert=v.toleriert, pseudonyme=pseudonyme)
            _gegenprobe_bilder(kontrolle, seiten, v.regeln, v.ziele, v.backend, opt, gegenprobe,
                               v.toleriert, pseudonyme)
            seitenzahl = len(kontrolle)
            if opt.vorschau == "immer" or (opt.vorschau == "auto" and ocr_seiten):
                vorschau = schreibe_vorschau(kontrolle, zielpfad(quelle, opt, "_vorschau"))

        v.speicher.merke(v.patient, v.vergabe)

        ergebnis = Ergebnis(quelle=quelle, bericht=None, fundstellen=fundstellen,  # type: ignore[arg-type]
                            kennung=v.kennung, text=text, vorschau=vorschau)
        if v.ohne_text:
            gegenprobe.anmerkungen.append("Seiten ohne Textebene konnten nicht geprüft werden")
        if gegenprobe.bestanden and not v.ohne_text:
            ergebnis.ziel_pdf = ziel
            if opt.textdatei:
                ergebnis.ziel_txt = zielpfad(quelle, opt, ".txt")
                ergebnis.ziel_txt.write_text(text, encoding="utf-8")
            if opt.markdown:
                ergebnis.ziel_md = zielpfad(quelle, opt, ".md")
                ergebnis.ziel_md.write_text(markdown_aus_text(text), encoding="utf-8")
        else:
            ziel.unlink(missing_ok=True)
            warnungen.append("Nicht freigegeben: Ausgabe verworfen.")
        optionen = opt.als_dict()
        if v.aufbereitet:
            optionen["aufbereitung"] = {str(n + 1): a.als_dict() for n, a in v.aufbereitet.items()}
        ergebnis.bericht = erstelle_bericht(
            quelle, ergebnis.ziel_pdf, seitenzahl, not ocr_seiten and not v.ohne_text,
            fundstellen, gegenprobe, optionen, warnungen,
        )
        if opt.bericht:
            ergebnis.ziel_bericht = zielpfad(quelle, opt, ".bericht.json")
            speichere_bericht(ergebnis.bericht, ergebnis.ziel_bericht)
        return ergebnis
    finally:
        if v.eigener_speicher:
            v.speicher.close()


def anonymisiere(
    pfad: str | Path,
    patient: Patient,
    opt: Ablaufoptionen | None = None,
    speicher: Pseudonymspeicher | None = None,
) -> Ergebnis:
    """Anonymise one file in one go.  The output is only kept when the check passes."""
    return abschliessen(vorbereiten(pfad, patient, opt, speicher))
