"""Redaction keeps neighbours, detects shifted layout and can fall back to an image."""

from __future__ import annotations

import pymupdf

from anonymed.dokument.textebene import lese_seite
from anonymed.kern.modell import Art, Fundstelle
from anonymed.schwaerzen.pdf import (
    Schwaerzoptionen,
    bereinige,
    lage_intakt,
    rastere_seite,
    schwaerze_seite,
)

ZEILE = "Herrn Flantz, geb. heute   Pat.-ID 123"


def _dokument(leading: float = 1.2) -> pymupdf.Document:
    doc = pymupdf.open()
    page = doc.new_page()
    y = 100.0
    for text in (ZEILE, "Zweite Zeile bleibt unberührt", "Dritte Zeile bleibt auch"):
        page.insert_text((56, y), text, fontsize=10.5, fontname="helv")
        y += 10.5 * leading
    return doc


def _treffer(page: pymupdf.Page, wort: str, pseudonym: str = "Patient A") -> Fundstelle:
    s = lese_seite(page)
    a = s.text.index(wort)
    b = a + len(wort)
    return Fundstelle(0, a, b, wort, Art.NACHNAME, "test", pseudonym,
                      rects=[tuple(r) for r in s.rects(a, b)],
                      schriftgroesse=s.schriftgroesse(a, b))


def test_nachbarn_bleiben_auch_bei_engem_zeilenabstand() -> None:
    for leading in (1.2, 1.0):
        doc = _dokument(leading)
        page = doc[0]
        f = _treffer(page, "Flantz")
        erg = schwaerze_seite(page, lese_seite(page), [f], Schwaerzoptionen())
        assert erg.anzahl == 1 and erg.lage_intakt
        text = page.get_text("text")
        assert "Flantz" not in text
        assert "Zweite Zeile bleibt unberührt" in text
        assert "Dritte Zeile bleibt auch" in text
        assert ", geb. heute" in text and "Pat.-ID 123" in text
        assert "Patient A" in text


def test_pseudonym_wird_sichtbar_gezeichnet() -> None:
    doc = _dokument()
    page = doc[0]
    f = _treffer(page, "Flantz", "[Versichertennummer]")
    schwaerze_seite(page, lese_seite(page), [f], Schwaerzoptionen())
    zeichnungen = page.get_drawings()
    assert any(d.get("fill") for d in zeichnungen)
    # the comma after the name is still rendered to the right of the box
    s = lese_seite(page)
    komma = s.boxen[s.text.index(",")]
    assert komma is not None and komma[0] >= f.rects[0][2] - 0.5


def test_lage_intakt_erkennt_verschiebung() -> None:
    doc = pymupdf.open()
    vorher = doc.new_page()
    vorher.insert_text((56, 100), "Herrn Flantz, geb.", fontsize=10.5, fontname="helv")
    sv = lese_seite(vorher)
    a = sv.text.index("Flantz")
    rect = tuple(sv.rects(a, a + 6)[0])
    f = Fundstelle(0, a, a + 6, "Flantz", Art.NACHNAME, "t", "P", rects=[rect])
    x_komma = sv.boxen[a + 6][0]
    richtig = doc.new_page()
    richtig.insert_text((56, 100), "Herrn ", fontsize=10.5, fontname="helv")
    richtig.insert_text((x_komma, 100), ", geb.", fontsize=10.5, fontname="helv")
    assert lage_intakt(sv, lese_seite(richtig), [f])
    falsch = doc.new_page()
    falsch.insert_text((56, 100), "Herrn ", fontsize=10.5, fontname="helv")
    falsch.insert_text((x_komma - 6.4, 100), ", geb.", fontsize=10.5, fontname="helv")
    assert not lage_intakt(sv, lese_seite(falsch), [f])


def test_rastere_seite_ersetzt_seite_durch_bild() -> None:
    doc = _dokument()
    original = pymupdf.open("pdf", doc.tobytes())
    page = doc[0]
    s = lese_seite(page)
    f = _treffer(page, "Flantz")
    rastere_seite(doc, 0, original, s, [f], Schwaerzoptionen(dpi=72))
    assert len(doc) == 1
    assert doc[0].get_text("text").strip() == ""
    assert doc[0].get_images()
    assert abs(doc[0].rect.width - original[0].rect.width) < 0.1


def test_bereinige_entfernt_struktur() -> None:
    doc = _dokument()
    doc.set_metadata({"title": "geheim", "author": "jemand"})
    doc.set_toc([[1, "Kapitel", 1]])
    doc[0].add_text_annot((300, 60), "Notiz")
    doc.embfile_add("a.txt", b"x")
    bereinige(doc)
    assert doc.metadata["title"] == "" and doc.metadata["author"] == ""
    assert doc.metadata["producer"].startswith("AnonyMed")
    assert doc.get_toc() == [] and doc.embfile_count() == 0
    assert list(doc[0].annots()) == []
