"""The pipeline shared by CLI and GUI: load, read (text layer and/or OCR), find,
redact, clean, check, report."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

import pymupdf

from anonymed.dokument import ocr as ocrmodul
from anonymed.dokument import textdatei as textmodul
from anonymed.dokument.laden import oeffne
from anonymed.dokument.textebene import Seitentext, lese_seite
from anonymed.erkennung import codes as codemodul
from anonymed.erkennung import fachbegriffe as fachmodul
from anonymed.erkennung import ner as nermodul
from anonymed.erkennung.deterministisch import (
    Regel,
    anonymisierter_text,
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
    ner: bool = False  # people and places the rules do not know (extra "ner")
    fachbegriffe: bool = True  # protect medical terms from NER and fuzzy OCR matching
    unscharf_text: bool = True  # text files: fuzzy search for misspelt identifiers (transcripts)
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
            "ner": self.ner,
            "fachbegriffe": self.fachbegriffe,
            "unscharf_text": self.unscharf_text,
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
class _Seite:
    nummer: int
    text: Seitentext
    ocr: Seitentext | None = None
    fundstellen: list[Fundstelle] = field(default_factory=list)
    fachrects: list[tuple[float, float, float, float]] = field(default_factory=list)
    fachspannen: list[tuple[int, int]] = field(default_factory=list)
    fachanzahl: int = 0


def _lese_seiten(
    doc: pymupdf.Document, regeln: list[Regel], ziele: list[Ziel], opt: Ablaufoptionen,
    backend: ocrmodul.OCRBackend | None, vergabe: Pseudonymvergabe | None = None,
    ner: nermodul.NER | None = None, fachbegriffe: fachmodul.Fachbegriffe | None = None,
    verschont: list[str] | None = None,
) -> tuple[list[_Seite], list[int]]:
    """Text layer and, where needed and possible, OCR per page; returns the pages and
    the numbers (1-based) of pages that needed OCR but got none."""
    seiten: list[_Seite] = []
    ohne_text: list[int] = []
    for i, page in enumerate(doc):
        textebene = lese_seite(page, i)
        eintrag = _Seite(i, textebene)
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
                eintrag.ocr = ocrmodul.erkenne_seite(page, backend, i, opt.ocr_dpi)
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


def _text(seiten: list[_Seite]) -> str:
    """Reading-order text with pseudonyms: text layer first, OCR text as its own block."""
    def mit_spannen(s: _Seite, quellen: tuple[str, ...]) -> list[Fundstelle]:
        return [f for f in s.fundstellen if f.quelle in quellen and f.ende > f.start]

    teile = anonymisierter_text(
        [s.text for s in seiten],
        [f for s in seiten for f in mit_spannen(s, ("text",) + (("manuell",)
                                                              if s.text.hat_textebene else ()))],
    )
    ocr_seiten = [s for s in seiten if s.ocr is not None]
    if ocr_seiten:
        ocr_text = anonymisierter_text(
            [s.ocr for s in ocr_seiten if s.ocr is not None],
            [f for s in ocr_seiten for f in mit_spannen(s, ("ocr",) + (("manuell",)
                                                                     if not s.text.hat_textebene
                                                                     else ()))],
        )
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
            nachher = ocrmodul.erkenne_seite(doc[s.nummer], backend, s.nummer, opt.ocr_dpi)
            spannen = pseudonymspannen(nachher.text, pseudonyme)
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
        this rule/text pair."""
        for s in self.seiten:
            if fundstelle in s.fundstellen:
                s.fundstellen.remove(fundstelle)
                self.verworfen.append(fundstelle)
                return

    @property
    def toleriert(self) -> set[tuple[str, str]]:
        return {toleranzschluessel(f.regel, f.text) for f in self.verworfen if f.text}

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
) -> Vorbereitung | Textvorbereitung:
    """Phase one of :func:`anonymisiere`: read the file and find the hits.

    PDFs and images yield a :class:`Vorbereitung`, text files (``.txt``,
    ``.md``, ``.srt``, ``.vtt``) a :class:`Textvorbereitung`.
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
            seiten, ohne_text = _lese_seiten(doc, r.regeln, r.ziele, opt, backend, r.vergabe,
                                             r.ner, r.fachbegriffe, verschont)
        except Exception:
            doc.close()
            original.close()
            raise
    except Exception:
        if eigener_speicher:
            speicher.close()
        raise
    warnungen = list(r.warnungen)
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
        seiten=seiten, ohne_text=ohne_text, warnungen=warnungen,
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

    @property
    def toleriert(self) -> set[tuple[str, str]]:
        return {toleranzschluessel(f.regel, f.text) for f in self.verworfen if f.text}

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
    misspelt known name must get the patient's pseudonym, not a NER code."""
    text = textmodul.lese_textdatei(quelle)
    seite = textmodul.textseite(textmodul.analysetext(text, quelle.suffix))
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
        regeln=r.regeln, ziele=r.ziele, text=text, seiten=[eintrag], warnungen=warnungen,
    )


def gegenprobe_text(
    text: str, endung: str, regeln: list[Regel], ziele: list[Ziel] | None = None,
    fachbegriffe: fachmodul.Fachbegriffe | None = None,
    toleriert: set[tuple[str, str]] | None = None, pseudonyme: list[str] | None = None,
) -> Gegenprobe:
    """Re-run the rules (and, with ``ziele``, the fuzzy search) over an anonymised text."""
    toleriert = toleriert or set()
    analyse = textmodul.analysetext(text, endung)
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
    pfad = Path(pfad)
    return gegenprobe_text(textmodul.lese_textdatei(pfad), pfad.suffix, regeln,
                           pseudonyme=pseudonyme)


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
        gegenprobe = gegenprobe_text(ausgabe, quelle.suffix, v.regeln,
                                     v.ziele if opt.unscharf_text else None, fach,
                                     v.toleriert, pseudonyme)
        v.speicher.merke(v.patient, v.vergabe)
        ziel = zielpfad(quelle, opt, quelle.suffix)
        ergebnis = Ergebnis(quelle=quelle, bericht=None, fundstellen=fundstellen,  # type: ignore[arg-type]
                            kennung=v.kennung, text=ausgabe, ist_text=True)
        if gegenprobe.bestanden:
            ziel.parent.mkdir(parents=True, exist_ok=True)
            ziel.write_text(ausgabe, encoding="utf-8")
            ergebnis.ziel_txt = ziel
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


def abschliessen(v: Vorbereitung | Textvorbereitung) -> Ergebnis:
    """Phase two: redact, clean, save, check, report.  Closes the documents."""
    if v.abgeschlossen:
        raise RuntimeError("Diese Vorbereitung wurde bereits abgeschlossen.")
    if isinstance(v, Textvorbereitung):
        return _abschliessen_text(v)
    v.abgeschlossen = True
    opt, quelle, seiten, warnungen = v.opt, v.quelle, v.seiten, list(v.warnungen)
    try:
        fundstellen = v.fundstellen
        v.vergabe.registriere_treffer(fundstellen)
        text = _text(seiten)
        try:
            for s in seiten:
                lage = schwaerze_seite(v.doc[s.nummer], s.text, s.fundstellen, opt.schwaerzen)
                if not lage.lage_intakt:
                    rastere_seite(v.doc, s.nummer, v.original, s.text, s.fundstellen,
                                  opt.schwaerzen)
                    warnungen.append(
                        f"Seite {s.nummer + 1}: Textlayout nach der Redaktion verschoben,"
                        " Seite deshalb gerastert (ohne Textebene)."
                    )
            bereinige(v.doc, vermerk=opt.schwaerzen.vermerk)
            ausgabe = v.doc
            if opt.schwaerzen.rastern:
                ausgabe = rastere(v.doc, opt.schwaerzen.dpi)
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
        else:
            ziel.unlink(missing_ok=True)
            warnungen.append("Nicht freigegeben: Ausgabe verworfen.")
        ergebnis.bericht = erstelle_bericht(
            quelle, ergebnis.ziel_pdf, seitenzahl, not ocr_seiten and not v.ohne_text,
            fundstellen, gegenprobe, opt.als_dict(), warnungen,
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
