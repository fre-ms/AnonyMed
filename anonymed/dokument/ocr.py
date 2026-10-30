"""OCR for scanned pages and photos, delivered as a :class:`Seitentext`.

Two backends: RapidOCR (pure pip wheels, default when installed, Latin model
for German text) and Tesseract (system binary, ``deu``).  Both return words
with boxes in page points; :func:`seitentext_aus_woerter` turns them into the
same character map the text layer produces, so that recognition and
redaction do not care where the text came from.  Character boxes inside a
word are approximated by dividing the word box evenly.
"""

from __future__ import annotations

import csv
import io
import shutil
import subprocess
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import pymupdf

from anonymed.dokument.textebene import Seitentext

OCR_DPI = 300
MAX_PIXEL = 4000  # longest side; photos from phones are larger than needed


@dataclass
class OCRWort:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    konfidenz: float = 1.0  # 0..1


class OCRBackend(Protocol):
    name: str

    def erkenne(self, pix: pymupdf.Pixmap, skala: float) -> list[OCRWort]:
        """Words of the rendered page; coordinates already divided by ``skala``."""


class OCRFehler(Exception):
    pass


def _rendere(page: pymupdf.Page, dpi: int = OCR_DPI) -> tuple[pymupdf.Pixmap, float]:
    """Render the page for OCR; returns the pixmap and the pixels-per-point scale."""
    skala = dpi / 72.0
    laengste = max(page.rect.width, page.rect.height) * skala
    if laengste > MAX_PIXEL:
        skala *= MAX_PIXEL / laengste
    pix = page.get_pixmap(matrix=pymupdf.Matrix(skala, skala), alpha=False,
                          colorspace=pymupdf.csRGB)
    return pix, skala


def _png(pix: pymupdf.Pixmap) -> bytes:
    return pix.tobytes("png")


# -- Tesseract -----------------------------------------------------------------------------------
class TesseractBackend:
    name = "tesseract"

    def __init__(self, sprache: str = "deu+eng", programm: str | None = None) -> None:
        self.programm = programm or shutil.which("tesseract")
        if not self.programm:
            raise OCRFehler("Tesseract ist nicht installiert (Programm 'tesseract' fehlt).")
        self.sprache = sprache

    @staticmethod
    def verfuegbar() -> bool:
        return shutil.which("tesseract") is not None

    def erkenne(self, pix: pymupdf.Pixmap, skala: float) -> list[OCRWort]:
        with tempfile.TemporaryDirectory() as ordner:
            bild = Path(ordner) / "seite.png"
            bild.write_bytes(_png(pix))
            try:
                lauf = subprocess.run(
                    [self.programm, str(bild), "-", "-l", self.sprache, "--psm", "3", "tsv"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    check=False, timeout=600,
                )
            except (OSError, subprocess.TimeoutExpired) as e:
                raise OCRFehler(f"Tesseract konnte nicht ausgeführt werden: {e}") from e
        if lauf.returncode != 0:
            raise OCRFehler(f"Tesseract meldet Fehler: {lauf.stderr.strip()[:300]}")
        woerter: list[OCRWort] = []
        for zeile in csv.DictReader(io.StringIO(lauf.stdout), delimiter="\t",
                                    quoting=csv.QUOTE_NONE):
            text = (zeile.get("text") or "").strip()
            if not text or zeile.get("level") != "5":
                continue
            try:
                x, y = float(zeile["left"]), float(zeile["top"])
                w, h = float(zeile["width"]), float(zeile["height"])
                konf = float(zeile.get("conf") or 0) / 100.0
            except (KeyError, ValueError):
                continue
            woerter.append(OCRWort(text, x / skala, y / skala, (x + w) / skala, (y + h) / skala,
                                   max(0.0, konf)))
        return woerter


# -- RapidOCR ------------------------------------------------------------------------------------
class RapidOCRBackend:
    name = "rapidocr"

    def __init__(self, sprache: str | None = "de") -> None:
        try:
            from rapidocr import RapidOCR
        except ImportError as e:
            raise OCRFehler("RapidOCR ist nicht installiert (Extra 'ocr').") from e
        self._RapidOCR = RapidOCR
        self._params = {"Global.log_level": "error"}
        self._engine = None
        self._standard = sprache is None
        if sprache:
            try:
                self._engine = RapidOCR(params={**self._params, "Rec.lang_type": sprache})
            except Exception:
                self._engine = None
        if self._engine is None:
            self._engine = RapidOCR(params=self._params)
            self._standard = True

    @staticmethod
    def verfuegbar() -> bool:
        try:
            import rapidocr  # noqa: F401
        except ImportError:
            return False
        return True

    def erkenne(self, pix: pymupdf.Pixmap, skala: float) -> list[OCRWort]:
        import numpy as np

        bild = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        if pix.n == 3:
            bild = bild[:, :, ::-1]  # RapidOCR expects BGR like OpenCV
        try:
            ergebnis = self._engine(bild)
        except Exception as e:
            # the language model is loaded lazily; unsupported or missing -> bundled default
            if self._standard:
                raise OCRFehler(f"RapidOCR fehlgeschlagen: {e}") from e
            self._engine = self._RapidOCR(params=self._params)
            self._standard = True
            ergebnis = self._engine(bild)
        boxen = getattr(ergebnis, "boxes", None)
        if boxen is None:
            return []
        woerter: list[OCRWort] = []
        for box, text, score in zip(boxen, ergebnis.txts, ergebnis.scores, strict=False):
            xs = [float(pt[0]) for pt in box]
            ys = [float(pt[1]) for pt in box]
            zeile = OCRWort(str(text).strip(), min(xs) / skala, min(ys) / skala, max(xs) / skala,
                            max(ys) / skala, float(score))
            woerter.extend(_zerlege_zeile(zeile))
        return woerter


def _zerlege_zeile(zeile: OCRWort) -> list[OCRWort]:
    """RapidOCR returns text lines; split into words with boxes by character share."""
    teile = zeile.text.split()
    if len(teile) <= 1:
        return [zeile] if zeile.text else []
    gesamt = len(zeile.text)
    breite = zeile.x1 - zeile.x0
    woerter: list[OCRWort] = []
    pos = 0
    for teil in teile:
        start = zeile.text.index(teil, pos)
        ende = start + len(teil)
        pos = ende
        woerter.append(OCRWort(teil, zeile.x0 + breite * start / gesamt, zeile.y0,
                               zeile.x0 + breite * ende / gesamt, zeile.y1, zeile.konfidenz))
    return woerter


# -- selection -----------------------------------------------------------------------------------
def backend(name: str = "auto") -> OCRBackend | None:
    """Return the requested backend, the first available one for "auto", or None for "aus"."""
    name = (name or "auto").lower()
    if name == "aus":
        return None
    if name == "rapidocr":
        return RapidOCRBackend()
    if name == "tesseract":
        return TesseractBackend()
    if name != "auto":
        raise OCRFehler(f"Unbekanntes OCR-Backend: {name!r} (auto, rapidocr, tesseract, aus).")
    if RapidOCRBackend.verfuegbar():
        return RapidOCRBackend()
    if TesseractBackend.verfuegbar():
        return TesseractBackend()
    return None


def verfuegbare_backends() -> list[str]:
    namen = []
    if RapidOCRBackend.verfuegbar():
        namen.append("rapidocr")
    if TesseractBackend.verfuegbar():
        namen.append("tesseract")
    return namen


# -- words -> Seitentext ---------------------------------------------------------------------------
def seitentext_aus_woerter(
    woerter: Iterable[OCRWort], nummer: int, breite: float, hoehe: float
) -> Seitentext:
    """Group words into lines by vertical overlap, order them, and build the character map."""
    seite = Seitentext(nummer=nummer, breite=breite, hoehe=hoehe, quelle="ocr")
    rest = sorted((w for w in woerter if w.text), key=lambda w: (w.y0, w.x0))
    zeilen: list[list[OCRWort]] = []
    for w in rest:
        for zeile in zeilen:
            ref = zeile[0]
            mitte = (w.y0 + w.y1) / 2
            if ref.y0 - 1 <= mitte <= ref.y1 + 1 and abs((w.y1 - w.y0) - (ref.y1 - ref.y0)) < \
                    max(w.y1 - w.y0, ref.y1 - ref.y0):
                zeile.append(w)
                break
        else:
            zeilen.append([w])
    zeilen.sort(key=lambda z: min(w.y0 for w in z))
    for nr, zeile in enumerate(zeilen, 1):
        zeile.sort(key=lambda w: w.x0)
        for i, w in enumerate(zeile):
            if i > 0:
                seite._fuege_an(" ", None, nr, w.y1 - w.y0, w.y1)
            n = max(len(w.text), 1)
            schritt = (w.x1 - w.x0) / n
            groesse = max(w.y1 - w.y0, 1.0) * 0.8
            for k, c in enumerate(w.text):
                box = (w.x0 + k * schritt, w.y0, w.x0 + (k + 1) * schritt, w.y1)
                seite._fuege_an(c, box, nr, groesse, w.y1 - 0.2 * (w.y1 - w.y0))
            seite.konfidenzen.append(w.konfidenz)
        seite._fuege_an("\n", None, nr, 0.0, 0.0)
    return seite


def erkenne_seite(page: pymupdf.Page, backend_: OCRBackend, nummer: int | None = None,
                  dpi: int = OCR_DPI) -> Seitentext:
    pix, skala = _rendere(page, dpi)
    woerter = backend_.erkenne(pix, skala)
    nummer = page.number if nummer is None else nummer
    return seitentext_aus_woerter(woerter, nummer, page.rect.width, page.rect.height)


def braucht_ocr(page: pymupdf.Page, textebene: Seitentext, mindestanteil: float = 0.2) -> bool:
    """True without a text layer, or when images cover a good part of the page
    (a scanned letter embedded in a PDF with a text layer)."""
    if not textebene.hat_textebene:
        return True
    flaeche = 0.0
    for info in page.get_image_info():
        r = pymupdf.Rect(info["bbox"]) & page.rect
        if r.width > 40 and r.height > 40:
            flaeche += r.width * r.height
    return flaeche >= mindestanteil * page.rect.width * page.rect.height
