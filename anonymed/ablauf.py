"""The pipeline shared by CLI and GUI: load, read (text layer and/or OCR), find,
redact, clean, check, report."""

from __future__ import annotations

import dataclasses
import functools
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

import pymupdf

from anonymed.dokument import aufbereitung as aufmodul
from anonymed.dokument import gliederung as gliedmodul
from anonymed.dokument import ocr as ocrmodul
from anonymed.dokument import textdatei as textmodul
from anonymed.dokument.laden import BILDENDUNGEN, HEIC_ENDUNGEN, oeffne
from anonymed.dokument.textabgleich import textebene_aus_korrektur
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
    geprueft_aus,
    in_pseudonym,
    pruefe,
    pseudonymspannen,
    pseudonymspannen_tolerant,
    toleranzschluessel,
)
from anonymed.pruefung.vorschau import schreibe_vorschau
from anonymed.pseudonyme import kapsel as kapselmodul
from anonymed.pseudonyme import pgp as pgpmodul
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
    ocr_sprachen: str = ""  # language packs: "deu+eng" (Tesseract) or "de", "latin" (RapidOCR)
    ocr_dpi: int = ocrmodul.OCR_DPI
    codes: bool = True  # blank barcodes and QR codes (extra "codes")
    unlesbar: str = "schwaerzen"  # lines OCR could not read: "schwaerzen" or "lassen"
    ner: bool = False  # people and places the rules do not know (extra "ner")
    fachbegriffe: bool = True  # protect medical terms from NER and fuzzy OCR matching
    unscharf_text: bool = True  # text files: fuzzy search for misspelt identifiers (transcripts)
    aufbereiten: str = "auto"  # image-only pages: "auto" (suggested clean-up) or "aus"
    verworfene_merken: bool = False  # remember discarded hits per patient in the store
    aufbereitung: dict[int, aufmodul.Aufbereitung] | None = None  # explicit choice per page
    vorschau: str = "auto"  # "auto" (pages that needed OCR), "immer", "nie"
    papierformat: str = "a4"  # page size for photos and images: a4, a5, a3, letter, legal, bild
    ocr_tabellen: bool = True  # Tesseract: second pass for sparse text (tables), merged in
    vokabular: bool = True  # write <name>_anonym.vokabular.txt with the discarded guesses
    gliedern: bool = True  # OCR text as Markdown: paragraphs joined, headings, lists, tables
    empfaenger: str = ""  # armoured public key of a recipient: one-time pseudonyms + capsule
    empfaenger_fingerabdruck: str = ""
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
            "ocr_sprachen": self.ocr_sprachen,
            "codes": self.codes,
            "ner": self.ner,
            "fachbegriffe": self.fachbegriffe,
            "unscharf_text": self.unscharf_text,
            "aufbereiten": self.aufbereiten,
            "papierformat": self.papierformat,
            "ocr_tabellen": self.ocr_tabellen,
            "gliedern": self.gliedern,
            "empfaenger": self.empfaenger_fingerabdruck or None,
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
    ziel_vokabular: Path | None = None  # discarded guesses, one term per line
    ziel_kapsel: Path | None = None  # the mapping, encrypted to the recipient
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


def vokabular_aus(verworfen: Iterable[Fundstelle]) -> list[str]:
    """Terms for the user's vocabulary: the texts of the discarded guesses (NER, fuzzy
    search), one per entry.  Discarded hits of the fixed rules are left out on purpose:
    their text is one of the patient's own identifiers and must not land in a side file."""
    gesehen: dict[str, str] = {}
    for f in verworfen:
        if not (f.regel.startswith(("ner.", "unscharf.")) or f.quelle == "unscharf"):
            continue
        begriff = " ".join(f.text.split())
        if len(begriff) < 3 or not any(c.isalpha() for c in begriff):
            continue
        gesehen.setdefault(begriff.casefold(), begriff)
    return sorted(gesehen.values(), key=str.casefold)


VOKABULAR_KOPF = ("# AnonyMed: im Prüfschritt verworfene Rate-Treffer (NER, unscharfe Suche),"
                  " ein Begriff je Zeile.\n# In „4 Analysieren“ als Nutzervokabular importierbar;"
                  " Zeilen mit # werden überlesen.\n")


def _kapsel_schreiben(v: Vorbereitung | Textvorbereitung, ergebnis: Ergebnis,
                      hinweise: list[str]) -> None:
    """For a recipient: the mapping of this document, encrypted to their key."""
    if not v.opt.empfaenger:
        return
    zertifikat = pgpmodul.lade_zertifikat(v.opt.empfaenger)
    kapsel = kapselmodul.erstelle(v.patient, v.kennung, v.vergabe, v.quelle.name,
                                  zertifikat.fingerabdruck, v.speicher.geheimnis)
    ziel = kapselmodul.schreibe(kapsel, zertifikat, zielpfad(v.quelle, v.opt, kapselmodul.ENDUNG))
    ergebnis.ziel_kapsel = ziel
    hinweise.append(f"Schlüsselkapsel für den Empfänger (Fingerabdruck … {zertifikat.kurz})"
                    f" geschrieben: {ziel.name}. Die Zuordnung der Pseudonyme steht nur dort,"
                    " verschlüsselt; die lokale Datenbank bleibt unberührt. Die Kapsel geht an"
                    " den Empfänger, nie an das Sprachmodell.")


def _vokabular_schreiben(v: Vorbereitung | Textvorbereitung, ergebnis: Ergebnis,
                         hinweise: list[str]) -> None:
    if not v.opt.vokabular:
        return
    begriffe = vokabular_aus(v.verworfen)
    if not begriffe:
        return
    ziel = zielpfad(v.quelle, v.opt, ".vokabular.txt")
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_text(VOKABULAR_KOPF + "\n".join(begriffe) + "\n", encoding="utf-8")
    ergebnis.ziel_vokabular = ziel
    hinweise.append(f"{len(begriffe)} verworfene Rate-Treffer als Vokabular geschrieben"
                    f" ({ziel.name}); in „4 Analysieren“ importierbar.")


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

    def vorschau(self, nummer: int, a: aufmodul.Aufbereitung,
                 dpi: int = 100) -> tuple[bytes, float]:
        """PNG of the processed page and its pixels per point."""
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
    doc = oeffne(quelle, opt.papierformat)
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
    ocr_filter: object = None  # letter-aware finish applied before the engine
    gliederung: gliedmodul.Gliederung | None = None  # layout roles of the OCR lines


def _lese_roh(
    doc: pymupdf.Document, opt: Ablaufoptionen, backend: ocrmodul.OCRBackend | None,
    plaene: dict[int, aufmodul.Aufbereitung] | None = None,
) -> tuple[list[_Seite], list[int]]:
    """Read every page: text layer and, where needed and possible, OCR.  No detection
    yet.  Returns the pages and the numbers (1-based) of pages that needed OCR but got
    none.  ``plaene`` holds the clean-up of pages: an upscaled page is read at that
    multiple of the OCR resolution (capped), a page in letter mode gets the letter-aware
    finish before the engine."""
    seiten: list[_Seite] = []
    ohne_text: list[int] = []
    for i, page in enumerate(doc):
        textebene = lese_seite(page, i)
        eintrag = _Seite(i, textebene)
        plan = (plaene or {}).get(i)
        faktor = max(1, plan.hochskalieren) if plan is not None else 1
        eintrag.ocr_dpi = opt.ocr_dpi * faktor
        eintrag.ocr_max_pixel = min(ocrmodul.MAX_PIXEL * faktor, 9000)
        if plan is not None and plan.textmodus:
            eintrag.ocr_filter = functools.partial(aufmodul.textverfeinerung, faktor=faktor)
        if ocrmodul.braucht_ocr(page, textebene):
            if backend is None:
                ohne_text.append(i + 1)
            else:
                eintrag.ocr = ocrmodul.erkenne_seite(page, backend, i, eintrag.ocr_dpi,
                                                     max_pixel=eintrag.ocr_max_pixel,
                                                     filter_=eintrag.ocr_filter)
                eintrag.gliederung = gliedmodul.gliedere_seite(page, eintrag.ocr)
        seiten.append(eintrag)
    return seiten, ohne_text


def _erkenne_seiten(
    doc: pymupdf.Document, seiten: list[_Seite], regeln: list[Regel], ziele: list[Ziel],
    opt: Ablaufoptionen, vergabe: Pseudonymvergabe | None = None,
    ner: nermodul.NER | None = None, fachbegriffe: fachmodul.Fachbegriffe | None = None,
    verschont: list[str] | None = None,
) -> None:
    """Run the detection over pages that were read already: rules, vocabulary marks,
    NER on the text layer; on OCR text additionally the fuzzy search and the unreadable
    lines; codes on the page image; the user's corrected text as text-only hits."""
    for eintrag in seiten:
        page = doc[eintrag.nummer]
        textebene = eintrag.text
        eintrag.fundstellen = finde(textebene, regeln)
        eintrag.fachrects, eintrag.fachspannen, eintrag.fachanzahl = [], [], 0
        if fachbegriffe is not None and textebene.hat_textebene:
            _merke_fachbegriffe(eintrag, textebene, fachbegriffe)
        if ner is not None and vergabe is not None and textebene.hat_textebene:
            neue = nermodul.finde_ner(textebene, ner, vergabe, eintrag.fundstellen, "text",
                                      fachbegriffe, verschont)
            eintrag.fundstellen += neue
        if eintrag.ocr is not None:
            if fachbegriffe is not None:
                _merke_fachbegriffe(eintrag, eintrag.ocr, fachbegriffe)
            genau = finde(eintrag.ocr, regeln)
            for f in genau:
                f.quelle = "ocr"
            eintrag.fundstellen += genau + finde_unscharf(eintrag.ocr, ziele, genau, fachbegriffe)
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
            if eintrag.korrektur is not None:
                eintrag.korrektur_treffer = _korrektur_treffer(eintrag, regeln, ziele, opt,
                                                               fachbegriffe)
        if opt.codes and codemodul.verfuegbar():
            eintrag.fundstellen += codemodul.finde_codes(page, eintrag.nummer)


def _gemerkte_verwerfen(seiten: list[_Seite], gemerkt: set[tuple[str, str]]) -> list[Fundstelle]:
    """Move hits the user discarded in an earlier session (same rule and text) out of
    the pages; they come back green and can be restored."""
    verworfen: list[Fundstelle] = []
    if not gemerkt:
        return verworfen
    for s in seiten:
        for f in list(s.fundstellen):
            if f.text and toleranzschluessel(f.regel, f.text) in gemerkt:
                s.fundstellen.remove(f)
                verworfen.append(f)
    return verworfen


def _korrektur_treffer(eintrag: _Seite, regeln: list[Regel], ziele: list[Ziel],
                       opt: Ablaufoptionen,
                       fachbegriffe: fachmodul.Fachbegriffe | None) -> list[Fundstelle]:
    """Rules and fuzzy search over the user's corrected OCR text: text-only hits."""
    assert eintrag.korrektur is not None
    seite = textmodul.textseite(eintrag.korrektur, eintrag.nummer)
    treffer = finde(seite, regeln)
    if opt.unscharf_text:
        treffer += finde_unscharf(seite, ziele, treffer, fachbegriffe, grossschreibung=True)
    for f in treffer:
        f.quelle = "korrektur"
        f.rects = []
    return treffer


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


def _lesetext(s: _Seite, opt: Ablaufoptionen) -> str:
    """The OCR text as it is shown and corrected: the user's correction, else the raw
    reading, structured as Markdown when asked for."""
    if s.korrektur is not None:
        return s.korrektur
    if s.ocr is None:
        return ""
    if opt.gliedern and s.gliederung is not None:
        return s.gliederung.rendere(s.ocr.text)
    return s.ocr.text


def _ocr_text_anonym(s: _Seite, gliedern: bool = True) -> str:
    """The OCR text of a page with pseudonyms: the user's correction when there is one,
    else the reading with the hits replaced, structured as Markdown when asked for
    (the structure is applied after the replacement: both texts share their lines)."""
    if s.korrektur is not None:
        return _anonym(s.korrektur, s.korrektur_treffer)
    assert s.ocr is not None
    quellen = ("ocr",) + (("manuell",) if not s.text.hat_textebene else ())
    treffer = [f for f in s.fundstellen if f.quelle in quellen and f.ende > f.start]
    roh = textmodul.ersetze(s.ocr.text, treffer)
    if gliedern and s.gliederung is not None:
        roh = s.gliederung.rendere(roh)
    return re.sub(r"\n{3,}", "\n\n", roh).strip()


def _text(seiten: list[_Seite], gliedern: bool = True) -> str:
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
        ocr_text = "\n\n".join(block(s, _ocr_text_anonym(s, gliedern))
                                for s in ocr_seiten) + "\n"
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
                                             max_pixel=s.ocr_max_pixel, filter_=s.ocr_filter)
            spannen = pseudonymspannen_tolerant(nachher.text, pseudonyme)
            genau = finde(nachher, regeln)
            fach = fachmodul.standard() if opt.fachbegriffe else None
            for f in genau + finde_unscharf(nachher, ziele, genau, fach):
                if in_pseudonym(f.start, f.ende, spannen):
                    continue
                if toleranzschluessel(f.regel, f.text) in toleriert:
                    if not opt.schwaerzen.textebene:  # else the text layer counted it
                        gegenprobe.belassen.append(f.regel)
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
    hinweise: list[str] = field(default_factory=list)
    verworfen: list[Fundstelle] = field(default_factory=list)
    aufbereitet: dict[int, aufmodul.Aufbereitung] = field(default_factory=dict)
    abgeschlossen: bool = False
    version: int = 0  # grows with every change the user makes in the review
    wiederhergestellt: set[tuple[str, str]] = field(default_factory=set)

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
                self.wiederhergestellt.discard(toleranzschluessel(fundstelle.regel,
                                                                  fundstelle.text))
                self.version += 1
                return

    def wiederherstelle(self, fundstelle: Fundstelle) -> None:
        """Take a discarded hit back."""
        if fundstelle in self.verworfen:
            self.verworfen.remove(fundstelle)
            self.seiten[fundstelle.seite].fundstellen.append(fundstelle)
            self.wiederhergestellt.add(toleranzschluessel(fundstelle.regel, fundstelle.text))
            self.version += 1

    @property
    def toleriert(self) -> set[tuple[str, str]]:
        return {toleranzschluessel(f.regel, f.text) for f in self.verworfen if f.text}

    def ocr_text(self, nummer: int) -> str:
        """The recognised text of page ``nummer`` as it will be written out: the user's
        correction when there is one, else the OCR reading (structured as Markdown when
        asked for); "" without OCR."""
        return _lesetext(self.seiten[nummer], self.opt)

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
        if _unveraendert(s, text):
            s.korrektur, s.korrektur_treffer = None, []
            return []
        s.korrektur = text
        fach = fachmodul.standard() if self.opt.fachbegriffe else None
        s.korrektur_treffer = _korrektur_treffer(s, self.regeln, self.ziele, self.opt, fach)
        self.version += 1
        return s.korrektur_treffer

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
        self.version += 1
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
    if opt.empfaenger and not speicher.ist_einmal:
        raise ValueError("Pseudonyme für einen Empfänger brauchen den Einmalspeicher"
                         " (einmalspeicher()): die lokale Datenbank darf die Zuordnung nicht"
                         " erhalten, und die Codes dürfen nicht die eigenen sein.")
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


@dataclass
class Lesung:
    """The reading phase: the pages of a PDF or image are read (text layer, OCR after
    the clean-up) but not yet searched.  The desktop lets the user check and correct
    the recognised text here before :func:`analysiere` looks for identifiers."""

    ist_text: ClassVar[bool] = False

    quelle: Path
    opt: Ablaufoptionen
    doc: pymupdf.Document
    original: pymupdf.Document
    backend: ocrmodul.OCRBackend | None
    seiten: list[_Seite]
    ohne_text: list[int]
    plan: dict[int, aufmodul.Aufbereitung] = field(default_factory=dict)
    warnungen: list[str] = field(default_factory=list)
    hinweise: list[str] = field(default_factory=list)
    verbraucht: bool = False

    @property
    def seitenzahl(self) -> int:
        return len(self.seiten)

    @property
    def ocr_seiten(self) -> list[int]:
        return [s.nummer + 1 for s in self.seiten if s.ocr is not None]

    def hat_ocr(self, nummer: int) -> bool:
        return self.seiten[nummer].ocr is not None

    def seitenbild(self, nummer: int, dpi: int = 100) -> bytes:
        """PNG of the page as the OCR saw it (cleaned up)."""
        return self.doc[nummer].get_pixmap(dpi=dpi, alpha=False).tobytes("png")

    def text(self, nummer: int) -> str:
        """Text of the page: the (corrected, structured) OCR text for OCR pages, else
        the text layer."""
        s = self.seiten[nummer]
        return _lesetext(s, self.opt) if s.ocr is not None else s.text.text

    def ocr_text(self, nummer: int) -> str:
        return _lesetext(self.seiten[nummer], self.opt)

    def setze_ocr_text(self, nummer: int, text: str) -> None:
        """Keep the user's correction of the OCR text; the text as read (raw or
        structured) resets it."""
        s = self.seiten[nummer]
        if s.ocr is None:
            raise ValueError("Diese Seite hat keinen OCR-Text.")
        s.korrektur = None if _unveraendert(s, text) else text

    def schliessen(self) -> None:
        for d in (self.doc, self.original):
            try:
                d.close()
            except Exception:  # already closed
                pass


def _unveraendert(s: _Seite, text: str) -> bool:
    """True when ``text`` is the OCR reading itself, raw or structured."""
    assert s.ocr is not None
    kandidaten = [s.ocr.text.strip()]
    if s.gliederung is not None:
        kandidaten.append(s.gliederung.rendere(s.ocr.text).strip())
    return text.strip() in kandidaten


def lese(
    pfad: str | Path,
    opt: Ablaufoptionen | None = None,
    aufbereitung: dict[int, aufmodul.Aufbereitung] | None = None,
) -> Lesung:
    """Reading phase for a PDF or image: clean-up, text layer, OCR.  No patient needed."""
    opt = opt or Ablaufoptionen()
    quelle = Path(pfad)
    backend = ocrmodul.backend(opt.ocr, opt.ocr_sprachen, tabellen=opt.ocr_tabellen)
    doc = oeffne(quelle, opt.papierformat)
    original = oeffne(quelle, opt.papierformat)
    try:
        plan = _aufbereitungsplan(doc, quelle, opt, aufbereitung)
        for nummer in sorted(plan):
            aufmodul.seiten_ersetzen([doc, original], nummer, plan[nummer], opt.ocr_dpi)
        seiten, ohne_text = _lese_roh(doc, opt, backend, plan)
    except Exception:
        doc.close()
        original.close()
        raise
    warnungen: list[str] = []
    hinweise: list[str] = []
    for nummer in sorted(plan):
        hinweise.append(f"Seite {nummer + 1} vor der OCR aufbereitet:"
                        f" {aufmodul.beschreibung(plan[nummer])}.")
    if ohne_text:
        warnungen.append(
            "Seite(n) ohne Textebene und ohne OCR: " + ", ".join(map(str, ohne_text))
            + " – OCR-Backend installieren (Extra 'ocr' oder Tesseract) oder --ocr prüfen."
        )
    ocr_seiten = [s.nummer + 1 for s in seiten if s.ocr is not None]
    if ocr_seiten:
        unsicher = sum(s.ocr.unsichere_woerter for s in seiten if s.ocr is not None)
        hinweise.append(
            f"Scan/Foto: Seite(n) {', '.join(map(str, ocr_seiten))} per OCR gelesen"
            f" ({backend.name if backend else '?'}, {unsicher} unsichere Wörter)."
        )
        hinweise.append("OCR-Seiten: Handschrift wird nicht erkannt; erkannten Text und"
                        " Vorschaubilder vor der Weitergabe prüfen.")
    return Lesung(quelle=quelle, opt=opt, doc=doc, original=original, backend=backend,
                  seiten=seiten, ohne_text=ohne_text, plan=plan, warnungen=warnungen,
                  hinweise=hinweise)


def analysiere(
    lesung: Lesung,
    patient: Patient,
    opt: Ablaufoptionen | None = None,
    speicher: Pseudonymspeicher | None = None,
) -> Vorbereitung:
    """Detection phase over a :class:`Lesung`: rules, vocabulary, fuzzy search, NER,
    codes, unreadable lines, the corrected OCR text.  Takes over the documents of the
    reading (which must not be used afterwards)."""
    if lesung.verbraucht:
        raise RuntimeError("Diese Lesung wurde bereits analysiert.")
    opt = opt or lesung.opt
    eigener_speicher = speicher is None
    speicher = speicher or Pseudonymspeicher()
    try:
        r = _ruestzeug(patient, opt, speicher)
        verschont: list[str] = []
        _erkenne_seiten(lesung.doc, lesung.seiten, r.regeln, r.ziele, opt, r.vergabe, r.ner,
                        r.fachbegriffe, verschont)
    except Exception:
        if eigener_speicher:
            speicher.close()
        raise
    lesung.verbraucht = True
    seiten = lesung.seiten
    warnungen = list(r.warnungen) + list(lesung.warnungen)
    hinweise = list(lesung.hinweise)
    hinweis = _ner_warnung(r, [f for s in seiten for f in s.fundstellen], verschont)
    if hinweis:
        hinweise.append(hinweis)
    unlesbar = sum(1 for s in seiten for f in s.fundstellen if f.regel == "ocr.unlesbar")
    if unlesbar:
        hinweise.append(f"{unlesbar} unlesbare OCR-Zeile(n) vorsorglich geschwärzt.")
    verworfen = _gemerkte_verwerfen(seiten, speicher.verworfene(patient))
    if verworfen:
        hinweise.append(f"{len(verworfen)} Treffer nach gespeicherter Entscheidung verworfen"
                        " (grün im Prüfschritt; Wiederherstellen hebt sie auf).")
    return Vorbereitung(
        quelle=lesung.quelle, patient=patient, opt=opt, speicher=speicher,
        eigener_speicher=eigener_speicher, kennung=r.kennung, vergabe=r.vergabe,
        regeln=r.regeln, ziele=r.ziele, backend=lesung.backend, doc=lesung.doc,
        original=lesung.original, seiten=seiten, ohne_text=lesung.ohne_text,
        warnungen=warnungen, hinweise=hinweise, aufbereitet=lesung.plan, verworfen=verworfen,
    )


def vorbereiten(
    pfad: str | Path,
    patient: Patient,
    opt: Ablaufoptionen | None = None,
    speicher: Pseudonymspeicher | None = None,
    aufbereitung: dict[int, aufmodul.Aufbereitung] | None = None,
) -> Vorbereitung | Textvorbereitung:
    """Phase one of :func:`anonymisiere`: read the file and find the hits.

    PDFs and images yield a :class:`Vorbereitung` (reading and detection in one go,
    see :func:`lese` and :func:`analysiere` for the two halves), text files
    (``.txt``, ``.md``, ``.srt``, ``.vtt``, ``.json``, ``.eml``) a
    :class:`Textvorbereitung`.  ``aufbereitung`` (per page, 0-based) replaces
    image-only pages by their cleaned-up image before OCR; without it the suggestion
    is applied when ``opt.aufbereiten`` is "auto".
    """
    opt = opt or Ablaufoptionen()
    quelle = Path(pfad)
    if textmodul.ist_textdatei(quelle):
        eigener_speicher = speicher is None
        speicher = speicher or Pseudonymspeicher()
        try:
            r = _ruestzeug(patient, opt, speicher)
            return _vorbereiten_text(quelle, patient, opt, speicher, eigener_speicher, r)
        except Exception:
            if eigener_speicher:
                speicher.close()
            raise
    lesung = lese(quelle, opt, aufbereitung)
    try:
        return analysiere(lesung, patient, opt, speicher)
    except Exception:
        lesung.schliessen()
        raise


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
    hinweise: list[str] = field(default_factory=list)
    verworfen: list[Fundstelle] = field(default_factory=list)
    abgeschlossen: bool = False
    version: int = 0
    wiederhergestellt: set[tuple[str, str]] = field(default_factory=set)
    aufbereitet: dict[int, aufmodul.Aufbereitung] = field(default_factory=dict)

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
                self.wiederhergestellt.discard(toleranzschluessel(fundstelle.regel,
                                                                  fundstelle.text))
                self.version += 1
                return

    def wiederherstelle(self, fundstelle: Fundstelle) -> None:
        if fundstelle in self.verworfen:
            self.verworfen.remove(fundstelle)
            self.seiten[fundstelle.seite].fundstellen.append(fundstelle)
            self.wiederhergestellt.add(toleranzschluessel(fundstelle.regel, fundstelle.text))
            self.version += 1

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
        self.version += 1
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
    hinweise: list[str] = []
    hinweis = _ner_warnung(r, eintrag.fundstellen, verschont)
    if hinweis:
        hinweise.append(hinweis)
    unscharfe = sum(1 for f in eintrag.fundstellen if f.quelle == "unscharf")
    if unscharfe:
        hinweise.append(f"Unscharfe Suche: {unscharfe} abweichend geschriebene Treffer"
                        " (Transkript, Tippfehler); im Prüfschritt ansehen.")
    verworfen = _gemerkte_verwerfen([eintrag], speicher.verworfene(patient))
    if verworfen:
        hinweise.append(f"{len(verworfen)} Treffer nach gespeicherter Entscheidung verworfen"
                        " (grün im Prüfschritt; Wiederherstellen hebt sie auf).")
    return Textvorbereitung(
        quelle=quelle, patient=patient, opt=opt, speicher=speicher,
        eigener_speicher=eigener_speicher, kennung=r.kennung, vergabe=r.vergabe,
        regeln=r.regeln, ziele=r.ziele, text=text, seiten=[eintrag], ausgabe_endung=endung,
        warnungen=warnungen, hinweise=hinweise, verworfen=verworfen,
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
    g = Gegenprobe(geprueft=geprueft_aus(regeln))
    for f in rest:
        if in_pseudonym(f.start, f.ende, spannen):
            continue
        if toleranzschluessel(f.regel, f.text) in toleriert:
            g.belassen.append(f.regel)
            continue
        g.resttreffer.append(f.ohne_klartext())
    return g


def gegenprobe_textdatei(pfad: str | Path, regeln: list[Regel],
                         pseudonyme: list[str] | None = None) -> Gegenprobe:
    """``anonymed pruefen`` for a text file: the rules alone, nothing tolerated; inside
    ``pseudonyme`` (at least the patient's code) no rule counts."""
    text, endung = textmodul.lese_quelle(pfad)
    return gegenprobe_text(text, endung, regeln, pseudonyme=pseudonyme)


def _abschliessen_text(v: Textvorbereitung, endgueltig: bool = True) -> Ergebnis:
    """Phase two for a text file: replace, check, write, report."""
    opt, quelle, warnungen, hinweise = v.opt, v.quelle, list(v.warnungen), list(v.hinweise)
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
        if endgueltig:
            _entscheidungen_merken(v)
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
            _vokabular_schreiben(v, ergebnis, hinweise)
            _kapsel_schreiben(v, ergebnis, hinweise)
        else:
            warnungen.append("Nicht freigegeben: keine Ausgabe geschrieben.")
        ergebnis.bericht = erstelle_bericht(quelle, ergebnis.ziel_txt, 1, True, fundstellen,
                                            gegenprobe, opt.als_dict(), warnungen, hinweise)
        if opt.bericht:
            ziel.parent.mkdir(parents=True, exist_ok=True)
            ergebnis.ziel_bericht = zielpfad(quelle, opt, ".bericht.json")
            speichere_bericht(ergebnis.bericht, ergebnis.ziel_bericht)
        return ergebnis
    finally:
        if endgueltig:
            v.abgeschlossen = True
            v.schliessen()


def _entscheidungen_merken(v: Vorbereitung | Textvorbereitung) -> None:
    """Store (or forget) the user's discard decisions for this patient when asked to."""
    if not v.opt.verworfene_merken:
        return
    if v.toleriert:
        v.speicher.merke_verworfen(v.patient, v.toleriert)
    if v.wiederhergestellt:
        v.speicher.vergiss_verworfen(v.patient, v.wiederhergestellt)


def _textebene_einbetten(doc: pymupdf.Document, seiten: list[_Seite], gerastert: set[int],
                         opt: Schwaerzoptionen) -> None:
    """Invisible text for pages that have none of their own: OCR pages get their OCR
    words; rasterised pages get their text-layer words and their pseudonyms as well."""
    for s in seiten:
        page = doc[s.nummer]
        if s.nummer in gerastert:
            unsichtbare_textebene(page, s.text, s.fundstellen, mit_pseudonymen=True, opt=opt)
        if s.ocr is not None and s.korrektur is not None:
            # the corrected (or restructured) text, every word on the box of the OCR
            # word it came from, so that selecting it in a reader covers the image word
            ebene = textebene_aus_korrektur(s.korrektur, s.ocr)
            treffer = s.korrektur_treffer
            if s.nummer in gerastert:
                treffer = [dataclasses.replace(f, rects=[tuple(r) for r in
                                                         ebene.rects(f.start, f.ende)])
                           for f in treffer]
            unsichtbare_textebene(page, ebene, treffer, mit_pseudonymen=s.nummer in gerastert,
                                  opt=opt)
        elif s.ocr is not None:
            ocr_treffer = [f for f in s.fundstellen if f.quelle in ("ocr", "manuell")]
            unsichtbare_textebene(page, s.ocr, ocr_treffer,
                                  mit_pseudonymen=s.nummer in gerastert, opt=opt)


def abschliessen(v: Vorbereitung | Textvorbereitung, endgueltig: bool = True) -> Ergebnis:
    """Phase two: redact, clean, save, check, report.

    Works on copies of the documents, so it can run more than once: the desktop
    runs it as the check before the export and again for the export itself.  With
    ``endgueltig`` the preparation is closed afterwards and the discard decisions
    are stored when the options ask for it.
    """
    if v.abgeschlossen:
        raise RuntimeError("Diese Vorbereitung wurde bereits abgeschlossen.")
    if isinstance(v, Textvorbereitung):
        return _abschliessen_text(v, endgueltig)
    opt, quelle, seiten = v.opt, v.quelle, v.seiten
    warnungen, hinweise = list(v.warnungen), list(v.hinweise)
    try:
        fundstellen = v.fundstellen + v.korrektur_treffer
        v.vergabe.registriere_treffer(fundstellen)
        text = _text(seiten, opt.gliedern)
        doc = pymupdf.open("pdf", v.doc.tobytes())
        original = pymupdf.open("pdf", v.original.tobytes())
        try:
            gerastert: set[int] = set()
            for s in seiten:
                lage = schwaerze_seite(doc[s.nummer], s.text, s.fundstellen, opt.schwaerzen)
                if not lage.lage_intakt:
                    rastere_seite(doc, s.nummer, original, s.text, s.fundstellen, opt.schwaerzen)
                    gerastert.add(s.nummer)
                    warnungen.append(
                        f"Seite {s.nummer + 1}: Textlayout nach der Redaktion verschoben,"
                        " Seite deshalb gerastert."
                    )
            if opt.schwaerzen.textebene and not opt.schwaerzen.rastern:
                _textebene_einbetten(doc, seiten, gerastert, opt.schwaerzen)
            bereinige(doc, vermerk=opt.schwaerzen.vermerk)
            ausgabe = doc
            if opt.schwaerzen.rastern:
                ausgabe = rastere(doc, opt.schwaerzen.dpi)
                if opt.schwaerzen.textebene:
                    _textebene_einbetten(ausgabe, seiten, {s.nummer for s in seiten},
                                         opt.schwaerzen)
                bereinige(ausgabe, vermerk=opt.schwaerzen.vermerk)
                hinweise.append("Ausgabe gerastert.")
            ziel = zielpfad(quelle, opt)
            ziel.parent.mkdir(parents=True, exist_ok=True)
            speichere(ausgabe, ziel)
            if ausgabe is not doc:
                ausgabe.close()
        finally:
            doc.close()
            original.close()

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
        if endgueltig:
            _entscheidungen_merken(v)

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
            _vokabular_schreiben(v, ergebnis, hinweise)
            _kapsel_schreiben(v, ergebnis, hinweise)
        else:
            ziel.unlink(missing_ok=True)
            warnungen.append("Nicht freigegeben: Ausgabe verworfen.")
        optionen = opt.als_dict()
        if v.aufbereitet:
            optionen["aufbereitung"] = {str(n + 1): a.als_dict() for n, a in v.aufbereitet.items()}
        ergebnis.bericht = erstelle_bericht(
            quelle, ergebnis.ziel_pdf, seitenzahl, not ocr_seiten and not v.ohne_text,
            fundstellen, gegenprobe, optionen, warnungen, hinweise,
        )
        if opt.bericht:
            ergebnis.ziel_bericht = zielpfad(quelle, opt, ".bericht.json")
            speichere_bericht(ergebnis.bericht, ergebnis.ziel_bericht)
        return ergebnis
    finally:
        if endgueltig:
            v.abgeschlossen = True
            v.schliessen()


def anonymisiere(
    pfad: str | Path,
    patient: Patient,
    opt: Ablaufoptionen | None = None,
    speicher: Pseudonymspeicher | None = None,
) -> Ergebnis:
    """Anonymise one file in one go.  The output is only kept when the check passes."""
    return abschliessen(vorbereiten(pfad, patient, opt, speicher))
