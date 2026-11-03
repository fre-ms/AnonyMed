"""Markdown structure of a recognised page from the geometry of its lines."""

from __future__ import annotations

import pymupdf

from pseudonymed.dokument import gliederung as gl
from pseudonymed.dokument.ocr import OCRWort, seitentext_aus_woerter
from pseudonymed.dokument.textebene import lese_seite


def _zeile(text: str, x: float, y: float, h: float = 10.0, zeichen: float = 5.0,
           luecke: float = 3.0) -> list[OCRWort]:
    """Words of one line with boxes: ``zeichen`` points per character, ``luecke`` between."""
    woerter = []
    for w in text.split(" "):
        if not w:
            continue
        b = len(w) * zeichen
        woerter.append(OCRWort(w, x, y, x + b, y + h))
        x += b + luecke
    return woerter


VOLL1 = "Die Patientin berichtet über seit drei Wochen bestehende Kopfschmerzen und einen Blut-"
VOLL2 = "druck von 160 zu 95 mmHg, dazu Schwindel beim Aufstehen, ohne Sturz, ohne Doppelbilder."
VOLL3 = "Die Beschwerden begannen nach einer fieberhaften Infektion und haben seither zugenommen,"
KURZ = "weshalb sie sich heute vorstellt."


def _seite():
    woerter = _zeile("Arztbrief Neurologie", 60, 60, h=18, zeichen=9)
    woerter += _zeile(VOLL1, 60, 100) + _zeile(VOLL2, 60, 114) + _zeile(VOLL3, 60, 128)
    woerter += _zeile(KURZ, 60, 142)
    woerter += _zeile("Diagnosen:", 60, 170)
    woerter += _zeile("- Kopfschmerz seit drei Wochen", 60, 190)
    woerter += _zeile("- Schwindel beim Aufstehen", 60, 204)
    woerter += _zeile("Medikament", 60, 240) + _zeile("Dosis", 260, 240)
    woerter += _zeile("morgens", 360, 240)
    woerter += _zeile("Ramipril", 60, 254) + _zeile("5 mg", 260, 254) + _zeile("1", 360, 254)
    woerter += _zeile("Amlodipin", 60, 268) + _zeile("5 mg", 260, 268) + _zeile("0", 360, 268)
    return seitentext_aus_woerter(woerter, 0, 595, 842)


def test_tabs_fuer_zellgrenzen() -> None:
    s = _seite()
    zeilen = s.text.splitlines()
    assert zeilen[-3] == "Medikament\tDosis\tmorgens" and zeilen[-1] == "Amlodipin\t5 mg\t0"
    assert "\t" not in zeilen[1]  # ordinary word gaps stay spaces


def test_gliederung_als_markdown() -> None:
    s = _seite()
    diagnosen = s.text.splitlines().index("Diagnosen:")
    g = gl.gliedere(s, fett={diagnosen})
    md = g.rendere(s.text)
    assert md.startswith("### Arztbrief Neurologie\n\n")
    assert "Kopfschmerzen und einen Blutdruck von 160" in md  # hyphenation undone
    assert "ohne Doppelbilder. Die Beschwerden" in md  # full lines joined with a space
    assert "zugenommen, weshalb sie sich heute vorstellt." in md  # short last line joined
    assert "\n\n#### Diagnosen:\n\n" in md  # short bold line on its own: a heading
    assert "- Kopfschmerz seit drei Wochen\n- Schwindel beim Aufstehen\n\n" in md
    assert "| Medikament | Dosis | morgens |\n| --- | --- | --- |\n| Ramipril | 5 mg | 1 |" in md
    assert g.tabellen == 1 and g.ueberschriften == 2
    # the same structure over the pseudonymised text (same lines, other words)
    anonym = s.text.replace("Die Patientin", "Patient K7Q4MX")
    md2 = g.rendere(anonym)
    assert md2.startswith("### Arztbrief Neurologie") and "Patient K7Q4MX berichtet" in md2
    assert "| Ramipril | 5 mg | 1 |" in md2
    assert g.rendere("ganz anderer Text") == "ganz anderer Text"  # lines do not match: as is


def test_absatz_ohne_fett_und_zeilen() -> None:
    """Short lines stay lines of one block; a colon line before a long one opens a block."""
    woerter = _zeile("Konzentration: gut.", 60, 100) + _zeile("Haltung: aufrecht.", 60, 114)
    woerter += _zeile("Gangbild: sicher.", 60, 128) + _zeile("Beurteilung:", 60, 150)
    woerter += _zeile(VOLL3, 60, 164) + _zeile(KURZ, 60, 178)
    woerter += _zeile(VOLL2, 60, 192) + _zeile("Medikation: Ramipril 5 mg morgens.", 60, 206)
    s = seitentext_aus_woerter(woerter, 0, 595, 842)
    md = gl.gliedere(s).rendere(s.text)
    assert "Konzentration: gut.\nHaltung: aufrecht.\nGangbild: sicher.\n\n" in md
    assert "Beurteilung:\n\nDie Beschwerden" in md and "zugenommen, weshalb" in md
    # a labelled line opens a block even after a line that ran to the margin
    assert "ohne Doppelbilder.\n\nMedikation: Ramipril" in md


def test_fette_zeilen_am_bild() -> None:
    """Ink coverage finds the bold line of a rendered page."""
    doc = pymupdf.open()
    p = doc.new_page(width=595, height=842)
    texte = ["Sehr geehrte Frau Kollegin,", "wir berichten über die Patientin.",
             "Die Untersuchung ergab einen regelrechten Befund.", "Diagnosen und Befunde",
             "Die Medikation wurde unverändert fortgeführt.", "Mit freundlichen Grüßen",
             "Die Wiedervorstellung ist in drei Monaten geplant."]
    for i, t in enumerate(texte):
        p.insert_text((60, 100 + 20 * i), t, fontsize=11, fontname="hebo" if i == 3 else "helv")
    seite = lese_seite(p, 0)
    zeilen = gl.zeilen_aus(seite)
    import numpy as np

    skala = 100 / 72.0
    pix = p.get_pixmap(matrix=pymupdf.Matrix(skala, skala), colorspace=pymupdf.csGRAY, alpha=False)
    grau = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
    fett = gl.fette_zeilen(zeilen, grau, skala)
    assert fett == {next(i for i, z in enumerate(zeilen) if "Diagnosen" in z.text)}
    md = gl.gliedere_seite(p, seite).rendere(seite.text)
    assert "#### Diagnosen und Befunde" in md
    doc.close()
