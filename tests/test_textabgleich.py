"""Corrected or restructured OCR text gets the boxes of the OCR words."""

from __future__ import annotations

import pymupdf

from anonymed.dokument.ocr import seitentext_aus_woerter
from anonymed.dokument.textabgleich import textebene_aus_korrektur
from tests.test_gliederung import _zeile


def _ocr():
    woerter = _zeile("Herr Max Mustermann wohnt in Dresden", 60, 100)
    woerter += _zeile("Diagnosen: Mlgraene seit Jahren", 60, 120)
    return seitentext_aus_woerter(woerter, 0, 595, 842)


def _box(seite, wort: str) -> pymupdf.Rect:
    a = seite.text.index(wort)
    return seite.rects(a, a + len(wort))[0]


def test_woerter_erhalten_ihre_boxen() -> None:
    ocr = _ocr()
    text = ("**Herr Max Mustermann** wohnt heute in Dresden\n\n"
            "#### Diagnosen: Migräne seit Jahren\n\nZusatz: neu\n")
    ebene = textebene_aus_korrektur(text, ocr)
    assert ebene.text == text
    for wort in ("Herr", "Mustermann", "Dresden", "Jahren"):
        assert abs(_box(ebene, wort).x0 - _box(ocr, wort).x0) < 0.01
        assert abs(_box(ebene, wort).x1 - _box(ocr, wort).x1) < 0.01
    # a corrected word keeps the box of the word it replaces
    assert abs(_box(ebene, "Migräne").x0 - _box(ocr, "Mlgraene").x0) < 0.01
    assert abs(_box(ebene, "Migräne").x1 - _box(ocr, "Mlgraene").x1) < 0.01
    # an inserted word sits between its neighbours on the same line
    heute = _box(ebene, "heute")
    assert _box(ocr, "wohnt").x1 <= heute.x0 and heute.x1 <= _box(ocr, "in").x0
    assert abs(heute.y0 - _box(ocr, "wohnt").y0) < 0.01
    # markup has no box and is no word of the layer
    woerter = {text[a:b] for a, b in ebene.wortspannen}
    assert "**" not in woerter and "####" not in woerter and "Herr" in woerter
    # text without a counterpart continues after the last word
    zusatz = _box(ebene, "Zusatz:")
    assert zusatz.x0 > _box(ocr, "Jahren").x1 and abs(zusatz.y0 - _box(ocr, "Jahren").y0) < 0.01


def test_zusammengezogene_zeilen_und_leere_ocr() -> None:
    ocr = _ocr()
    text = "Herr Max Mustermann wohnt in Dresden Diagnosen: Mlgraene seit Jahren\n"
    ebene = textebene_aus_korrektur(text, ocr)  # joined into one paragraph line
    assert abs(_box(ebene, "Diagnosen:").y0 - _box(ocr, "Diagnosen:").y0) < 0.01
    assert abs(_box(ebene, "Herr").y0 - _box(ocr, "Herr").y0) < 0.01
    leer = seitentext_aus_woerter([], 0, 595, 842)
    ebene = textebene_aus_korrektur("Nur Korrektur ohne OCR\n", leer)
    assert len(ebene.wortspannen) == 4 and all(b is not None for b in ebene.boxen[:3])
