"""Character-level view of a page's text layer.

PyMuPDF's ``rawdict`` gives every character with its bounding box and
baseline.  We flatten that into one string per page plus parallel lists (box,
line id, font size, baseline per character) so that ordinary regular
expressions can be run over the page and every match can be mapped back to
rectangles for redaction.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pymupdf

Box = tuple[float, float, float, float]

# Expand ligatures ("ﬁ" -> "f", "i") so that names match letter by letter.
_FLAGS = pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_LIGATURES


@dataclass
class Seitentext:
    """Reconstructed text of one page with a box for every character."""

    nummer: int
    breite: float
    hoehe: float
    text: str = ""
    boxen: list[Box | None] = field(default_factory=list)
    zeilen: list[int] = field(default_factory=list)
    groessen: list[float] = field(default_factory=list)
    basislinien: list[float] = field(default_factory=list)
    zeilenboxen: dict[int, Box] = field(default_factory=dict)

    MIN_ZEICHEN_TEXTEBENE = 20

    @property
    def hat_textebene(self) -> bool:
        """True when the page carries enough real text to work without OCR."""
        return len("".join(self.text.split())) >= self.MIN_ZEICHEN_TEXTEBENE

    def rects(self, start: int, ende: int) -> list[pymupdf.Rect]:
        """Union of character boxes in ``[start, ende)``, one rectangle per line."""
        je_zeile: dict[int, pymupdf.Rect] = {}
        for i in range(start, min(ende, len(self.text))):
            box = self.boxen[i]
            if box is None:
                continue
            r = pymupdf.Rect(box)
            z = self.zeilen[i]
            if z in je_zeile:
                je_zeile[z] |= r
            else:
                je_zeile[z] = r
        return [r for _, r in sorted(je_zeile.items())]

    def zeile_von(self, index: int) -> int:
        return self.zeilen[index] if 0 <= index < len(self.zeilen) else -1

    def schriftgroesse(self, start: int, ende: int) -> float:
        groessen = sorted(
            self.groessen[i]
            for i in range(start, min(ende, len(self.text)))
            if self.boxen[i] is not None
        )
        return groessen[len(groessen) // 2] if groessen else 10.0

    def basislinie(self, start: int, ende: int) -> float | None:
        """Baseline (y) of the first printed character of the match."""
        for i in range(start, min(ende, len(self.text))):
            if self.boxen[i] is not None:
                return self.basislinien[i]
        return None

    def freier_raum_rechts(self, ende: int) -> float:
        """x coordinate of the next printed character on the same line after ``ende``.

        Used to widen a redaction box so that the pseudonym stays legible when
        it is longer than the text it replaces.  Spaces count as free.
        """
        if ende <= 0 or ende > len(self.text):
            return self.breite
        zeile = self.zeilen[ende - 1]
        for k in range(ende, len(self.text)):
            if self.zeilen[k] != zeile:
                break
            box = self.boxen[k]
            if box is None or self.text[k].isspace():
                continue
            return box[0]
        return self.breite - 18.0

    def _fuege_an(
        self, zeichen: str, box: Box | None, zeile: int, groesse: float, basislinie: float
    ) -> None:
        for c in zeichen:
            self.text += c
            self.boxen.append(box)
            self.zeilen.append(zeile)
            self.groessen.append(groesse)
            self.basislinien.append(basislinie)
        if box is not None:
            alt = self.zeilenboxen.get(zeile)
            if alt is None:
                self.zeilenboxen[zeile] = box
            else:
                self.zeilenboxen[zeile] = (
                    min(alt[0], box[0]), min(alt[1], box[1]),
                    max(alt[2], box[2]), max(alt[3], box[3]),
                )


def lese_seite(page: pymupdf.Page, nummer: int | None = None) -> Seitentext:
    """Flatten the page's text layer into a :class:`Seitentext`."""
    nummer = page.number if nummer is None else nummer
    seite = Seitentext(nummer=nummer, breite=page.rect.width, hoehe=page.rect.height)
    roh = page.get_text("rawdict", flags=_FLAGS, sort=True)
    zeile = 0
    for block in roh.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            zeile += 1
            letzte_box: Box | None = None
            for span in line.get("spans", []):
                groesse = float(span.get("size", 10.0))
                span_basis = float(span.get("origin", (0.0, 0.0))[1])
                for ch in span.get("chars", []):
                    box = tuple(float(v) for v in ch["bbox"])
                    basis = float(ch.get("origin", (0.0, span_basis))[1]) or span_basis
                    if letzte_box is not None:
                        luecke = box[0] - letzte_box[2]
                        getrennt = not seite.text.endswith(" ")
                        if luecke > 0.25 * groesse and getrennt:
                            seite._fuege_an(" ", None, zeile, groesse, basis)
                    seite._fuege_an(ch["c"], box, zeile, groesse, basis)
                    letzte_box = box
            seite._fuege_an("\n", None, zeile, 0.0, 0.0)
        seite._fuege_an("\n", None, zeile, 0.0, 0.0)
    return seite


def lese_dokument(doc: pymupdf.Document) -> list[Seitentext]:
    return [lese_seite(page, i) for i, page in enumerate(doc)]
