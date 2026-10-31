"""Image clean-up: tilt estimate, sheet detection, upscaling, background, masks."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from anonymed.dokument import aufbereitung as auf

pytestmark = pytest.mark.skipif(not auf.verfuegbar(), reason="OpenCV/NumPy fehlen (Extra ocr)")


def _seite(befund: Path) -> pymupdf.Page:
    doc = pymupdf.open(befund)
    return doc[0]


def test_winkel_wird_geschaetzt(befund: Path) -> None:
    import cv2

    bild, _ = auf.rendere(_seite(befund), 150)
    assert abs(auf.schaetze_winkel(bild)) < 0.3  # straight page
    for soll in (2.0, -3.5):
        h, w = bild.shape[:2]
        m = cv2.getRotationMatrix2D((w / 2, h / 2), -soll, 1.0)  # clockwise = downhill
        schief = cv2.warpAffine(bild, m, (w, h), borderMode=cv2.BORDER_CONSTANT,
                                borderValue=(255, 255, 255))
        geschaetzt = auf.schaetze_winkel(schief)
        assert abs(geschaetzt - soll) <= 0.35, (soll, geschaetzt)
        gerade = auf.wende_an(schief, auf.Aufbereitung(begradigen=True, winkel=geschaetzt))
        assert abs(auf.schaetze_winkel(gerade)) < 0.35


def test_blatt_im_foto_wird_gefunden(befund: Path) -> None:
    import cv2
    import numpy as np

    bild, _ = auf.rendere(_seite(befund), 100)
    h, w = bild.shape[:2]
    # a dark table with the page photographed at an angle
    foto = np.full((h + 300, w + 300, 3), (60, 70, 80), dtype=np.uint8)
    ziel = np.array([[180, 120], [w + 90, 170], [w + 150, h + 200], [120, h + 240]],
                    dtype=np.float32)
    m = cv2.getPerspectiveTransform(np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=np.float32),
                                    ziel)
    verzerrt = cv2.warpPerspective(bild, m, (w + 300, h + 300), dst=foto,
                                   borderMode=cv2.BORDER_TRANSPARENT)
    ecken = auf.finde_ecken(verzerrt)  # skala 1: corners in pixels
    assert ecken is not None and len(ecken) == 4
    for (x, y), (sx, sy) in zip(ecken, ziel, strict=True):
        assert abs(x - sx) < 12 and abs(y - sy) < 12, (ecken, ziel)
    entzerrt = auf.wende_an(verzerrt, auf.Aufbereitung(entzerren=True, ecken=ecken))
    assert abs(entzerrt.shape[1] / entzerrt.shape[0] - w / h) < 0.08
    assert abs(auf.schaetze_winkel(entzerrt)) < 0.6
    assert auf.finde_ecken(bild) is None  # a page that fills the image needs nothing
    v = auf.vorschlag(verzerrt, foto=True)
    assert v.entzerren and v.hintergrund and v.ecken is not None
    assert "entzerrt" in auf.beschreibung(v) and "Hintergrund" in auf.beschreibung(v)


def test_hochskalieren_hintergrund_masken(befund: Path) -> None:
    import numpy as np

    bild, skala = auf.rendere(_seite(befund), 72)
    h, w = bild.shape[:2]
    gross = auf.wende_an(bild, auf.Aufbereitung(hochskalieren=2))
    assert gross.shape[0] == 2 * h and gross.shape[1] == 2 * w
    schatten = bild.astype(np.int16) - np.linspace(0, 90, w, dtype=np.int16)[None, :, None]
    schatten = np.clip(schatten, 0, 255).astype(np.uint8)
    assert auf.hintergrund_ungleich(schatten) and not auf.hintergrund_ungleich(bild)
    sauber = auf.wende_an(schatten, auf.Aufbereitung(hintergrund=True))
    assert sauber.ndim == 2 and np.median(sauber[:, -40:]) > 240  # the shadow is gone
    sw = auf.wende_an(bild, auf.Aufbereitung(schwarzweiss=True))
    assert set(np.unique(sw)) <= {0, 255}
    maskiert = auf.wende_an(bild, auf.Aufbereitung(masken=[(10, 10, 60, 30)]), skala)
    assert maskiert[15:25, 15:55].max() == 0 and maskiert[100:, 100:].max() == 255
    v = auf.vorschlag(bild, aufloesung=120)
    assert v.hochskalieren == 2 and not v.entzerren and not v.begradigen
    assert v.als_dict()["hochskalieren"] == 2
    assert auf.vorschlag(bild, aufloesung=300).hochskalieren == 1
    assert auf.beschreibung(auf.Aufbereitung()) == "unverändert"


def test_seite_ersetzen(befund: Path, tmp_path: Path) -> None:
    doc = pymupdf.open(befund)
    breite = doc[0].rect.width
    skala = auf.seite_ersetzen(doc, 0, auf.Aufbereitung(begradigen=True, winkel=1.0,
                                                        masken=[(56, 60, 300, 90)]), dpi=100)
    assert skala > 0 and len(doc) == 2 and abs(doc[0].rect.width - breite) < 0.01
    assert doc[0].get_images() and not doc[0].get_text("text").strip()  # an image page now
    pix = doc[0].get_pixmap(clip=pymupdf.Rect(60, 65, 290, 85), colorspace=pymupdf.csGRAY)
    assert max(pix.samples) < 30  # the mask is black on the new page
    png = auf.vorschau_png(doc[1], auf.Aufbereitung(drehung=90), dpi=50)
    assert png.startswith(b"\x89PNG")
    doc.close()
