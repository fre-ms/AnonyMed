"""Character map of a page: offsets map back to rectangles."""

from __future__ import annotations

import pymupdf

from pseudonymed.dokument.textebene import lese_seite


def _seite() -> pymupdf.Page:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Erste Zeile mit Mustermann, Komma", fontsize=11, fontname="helv")
    page.insert_text((72, 116), "Zweite Zeile", fontsize=11, fontname="helv")
    return page


def test_text_und_zeilen() -> None:
    s = lese_seite(_seite())
    assert "Erste Zeile mit Mustermann, Komma" in s.text
    assert "Zweite Zeile" in s.text
    assert s.hat_textebene
    a = s.text.index("Mustermann")
    b = a + len("Mustermann")
    assert s.zeile_von(a) == s.zeile_von(b - 1)
    assert s.zeile_von(a) != s.zeile_von(s.text.index("Zweite"))


def test_rects_und_freier_raum() -> None:
    s = lese_seite(_seite())
    a = s.text.index("Mustermann")
    b = a + len("Mustermann")
    rects = s.rects(a, b)
    assert len(rects) == 1
    r = rects[0]
    assert 72 < r.x0 < r.x1 < s.breite
    assert 85 < r.y0 < r.y1 < 110
    # the comma follows immediately, so there is no free space to the right
    assert abs(s.freier_raum_rechts(b) - r.x1) < 0.5
    assert s.schriftgroesse(a, b) == 11.0
    assert s.basislinie(a, b) is not None and abs(s.basislinie(a, b) - 100) < 0.5


def test_leere_seite() -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    s = lese_seite(page)
    assert s.text.strip() == ""
    assert not s.hat_textebene
