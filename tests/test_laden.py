"""Images are opened on a sheet of paper: size, orientation, centring, "bild"."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from pseudonymed.dokument.laden import PAPIERFORMATE, oeffne, papierformat


def _bild(pfad: Path, breite: int, hoehe: int) -> Path:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, breite, hoehe), False)
    pix.clear_with(255)
    pix.save(pfad)
    return pfad


def test_papierformat_namen() -> None:
    assert papierformat("A4") == PAPIERFORMATE["a4"] and papierformat(None) == PAPIERFORMATE["a4"]
    assert papierformat("US Letter") == PAPIERFORMATE["letter"] and papierformat("bild") is None
    with pytest.raises(ValueError, match="Papierformat"):
        papierformat("b5")


def test_bild_auf_papier(tmp_path: Path) -> None:
    quer = _bild(tmp_path / "quer.png", 1000, 500)
    hoch = _bild(tmp_path / "hoch.png", 500, 1000)
    with oeffne(quer) as doc:  # A4 landscape, image fitted and centred
        page = doc[0]
        assert (round(page.rect.width), round(page.rect.height)) == (842, 595)
        bbox = pymupdf.Rect(page.get_image_info()[0]["bbox"])
        assert abs(bbox.width - 842) < 1 and abs(bbox.height - 421) < 1
        assert abs(bbox.y0 - (595 - 421) / 2) < 1
    with oeffne(hoch, "letter") as doc:
        assert (round(doc[0].rect.width), round(doc[0].rect.height)) == (612, 792)
    with oeffne(hoch, "bild") as doc, pymupdf.open(hoch) as roh:  # as large as the image
        assert doc[0].rect == roh[0].rect and doc[0].rect.height == 2 * doc[0].rect.width
    with pytest.raises(ValueError):
        oeffne(hoch, "b5")


def test_papier_in_der_pipeline(tmp_path: Path, befund: Path, patient) -> None:
    """A photo of the report keeps A4 through the whole run and the redaction reaches
    the image inside the sheet (otherwise the re-check would fail)."""
    from pseudonymed.ablauf import Ablaufoptionen, lade, pseudonymisiere
    from pseudonymed.dokument import ocr as ocrmodul
    from pseudonymed.pseudonyme.speicher import Pseudonymspeicher

    foto = tmp_path / "foto.png"
    with pymupdf.open(befund) as d:
        d[0].get_pixmap(dpi=120).save(foto)
    v = lade(foto, Ablaufoptionen(papierformat="a5"))
    assert (round(v.doc[0].rect.width), round(v.doc[0].rect.height)) == (420, 595)
    v.schliessen()
    if not ocrmodul.verfuegbare_backends():
        pytest.skip("kein OCR-Backend installiert")
    with Pseudonymspeicher(":memory:", geheimnis=bytes(range(32))) as sp:
        erg = pseudonymisiere(foto, patient, Ablaufoptionen(papierformat="letter"), speicher=sp)
    assert erg.freigegeben and erg.bericht.optionen["papierformat"] == "letter"
    with pymupdf.open(erg.ziel_pdf) as d:
        assert (round(d[0].rect.width), round(d[0].rect.height)) == (612, 792)
