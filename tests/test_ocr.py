"""Scans, photos, noise, HEIC and barcodes: read by OCR, blanked in the pixels, re-checked."""

from __future__ import annotations

import io
import random
from pathlib import Path

import pymupdf
import pytest

from anonymed.ablauf import Ablaufoptionen, anonymisiere
from anonymed.dokument import ocr as ocrmodul
from anonymed.dokument.laden import oeffne
from anonymed.dokument.ocr import OCRWort, seitentext_aus_woerter
from anonymed.erkennung import codes as codemodul
from anonymed.kern.modell import Patient
from anonymed.pruefung.bericht import als_text
from anonymed.pseudonyme.speicher import Pseudonymspeicher
from anonymed.schwaerzen.pdf import rastere
from tests.test_ablauf import gefundene_klardaten

BACKENDS = ocrmodul.verfuegbare_backends()
braucht_backend = pytest.mark.skipif(not BACKENDS, reason="kein OCR-Backend installiert")


def _scan(befund: Path, ziel: Path, dpi: int = 200, rauschen: float = 0.0,
          drehung: float = 0.0) -> Path:
    """Image-only PDF of the synthetic report, optionally with salt-and-pepper noise and a tilt."""
    with pymupdf.open(befund) as d:
        scan = rastere(d, dpi=dpi)
    if rauschen or drehung:
        from PIL import Image

        neu = pymupdf.open()
        rng = random.Random(1)
        for page in scan:
            pix = page.get_pixmap(dpi=dpi, alpha=False)
            bild = Image.open(io.BytesIO(pix.tobytes("png"))).convert("L")
            px = bild.load()
            if rauschen:
                for _ in range(int(rauschen * bild.width * bild.height)):
                    px[rng.randrange(bild.width), rng.randrange(bild.height)] = rng.choice((0, 255))
            if drehung:
                bild = bild.rotate(drehung, expand=False, fillcolor=255)
            puffer = io.BytesIO()
            bild.save(puffer, format="PNG")
            seite = neu.new_page(width=page.rect.width, height=page.rect.height)
            seite.insert_image(seite.rect, stream=puffer.getvalue())
        scan.close()
        scan = neu
    scan.save(ziel)
    scan.close()
    return ziel


def test_woerter_zu_seitentext() -> None:
    woerter = [OCRWort("Max", 100, 100, 130, 112), OCRWort("Herr", 60, 101, 90, 113),
               OCRWort("Zweite", 60, 130, 110, 142, 0.4)]
    s = seitentext_aus_woerter(woerter, 0, 595, 842)
    assert s.text.splitlines()[0] == "Herr Max"
    assert s.text.splitlines()[1] == "Zweite"
    assert s.quelle == "ocr" and s.unsichere_woerter == 1
    a = s.text.index("Max")
    r = s.rects(a, a + 3)[0]
    assert abs(r.x0 - 100) < 0.01 and abs(r.x1 - 130) < 0.01
    assert s.zeile_von(a) != s.zeile_von(s.text.index("Zweite"))


@braucht_backend
@pytest.mark.parametrize("backend", BACKENDS)
def test_scan_wird_gelesen_und_geschwaerzt(tmp_path: Path, befund: Path, patient: Patient,
                                           speicher: Pseudonymspeicher, backend: str) -> None:
    scan = _scan(befund, tmp_path / "scan.pdf")
    erg = anonymisiere(scan, patient, Ablaufoptionen(ocr=backend), speicher=speicher)
    assert erg.freigegeben, als_text(erg.bericht)
    assert not erg.bericht.textebene
    assert any("per OCR gelesen" in w for w in erg.bericht.warnungen)
    assert sum(1 for f in erg.fundstellen if f.quelle == "ocr") >= 10
    assert erg.vorschau and all(p.exists() for p in erg.vorschau)
    assert "--- Bildtext (OCR" in erg.text
    assert gefundene_klardaten(erg.text, patient) == []
    # the output has no text layer with clear data, and OCR of the output finds none either
    with pymupdf.open(erg.ziel_pdf) as d:
        assert gefundene_klardaten("\n".join(p.get_text() for p in d), patient) == []
        b = ocrmodul.backend(backend)
        nachher = "\n".join(ocrmodul.erkenne_seite(p, b, i).text for i, p in enumerate(d))
    assert gefundene_klardaten(nachher, patient) == []
    assert len(nachher) > 400 and ("Befund" in nachher or "Kontrolle" in nachher)


@braucht_backend
def test_verrauschter_schiefer_scan(tmp_path: Path, befund: Path, patient: Patient,
                                    speicher: Pseudonymspeicher) -> None:
    scan = _scan(befund, tmp_path / "scan.pdf", dpi=150, rauschen=0.01, drehung=0.7)
    erg = anonymisiere(scan, patient, speicher=speicher)
    assert erg.freigegeben, als_text(erg.bericht)
    assert gefundene_klardaten(erg.text, patient) == []
    assert sum(1 for f in erg.fundstellen if f.quelle == "ocr") >= 6


@braucht_backend
def test_foto_als_png(tmp_path: Path, befund: Path, patient: Patient,
                      speicher: Pseudonymspeicher) -> None:
    with pymupdf.open(befund) as d:
        bild = tmp_path / "foto.png"
        d[0].get_pixmap(dpi=150).save(bild)
    erg = anonymisiere(bild, patient, speicher=speicher)
    assert erg.freigegeben, als_text(erg.bericht)
    assert erg.bericht.seiten == 1 and gefundene_klardaten(erg.text, patient) == []


def test_ohne_backend_keine_freigabe(tmp_path: Path, befund: Path, patient: Patient,
                                     speicher: Pseudonymspeicher) -> None:
    scan = _scan(befund, tmp_path / "scan.pdf")
    erg = anonymisiere(scan, patient, Ablaufoptionen(ocr="aus"), speicher=speicher)
    assert not erg.freigegeben
    assert any("ohne OCR" in w for w in erg.bericht.warnungen)


def test_unbekanntes_backend() -> None:
    with pytest.raises(ocrmodul.OCRFehler):
        ocrmodul.backend("zauber")


@pytest.mark.skipif(not codemodul.verfuegbar(), reason="zxing-cpp nicht installiert")
def test_barcode_wird_geschwaerzt(tmp_path: Path, befund: Path, patient: Patient,
                                  speicher: Pseudonymspeicher) -> None:
    import zxingcpp
    from PIL import Image

    with pymupdf.open(befund) as d:
        qr = zxingcpp.write_barcode(zxingcpp.BarcodeFormat.QRCode, f"PAT {patient.patienten_id}",
                                    width=300, height=300)
        puffer = io.BytesIO()
        Image.fromarray(qr).save(puffer, format="PNG")
        d[0].insert_image(pymupdf.Rect(400, 600, 520, 720), stream=puffer.getvalue())
        mit_code = tmp_path / "mit_code.pdf"
        d.save(mit_code)
    with pymupdf.open(mit_code) as d:
        assert len(codemodul.finde_codes(d[0])) == 1
    erg = anonymisiere(mit_code, patient, speicher=speicher)
    assert erg.freigegeben, als_text(erg.bericht)
    assert any(f.quelle == "code" and f.pseudonym == "[Code]" for f in erg.fundstellen)
    with pymupdf.open(erg.ziel_pdf) as d:
        assert codemodul.finde_codes(d[0]) == []
    erg2 = anonymisiere(mit_code, patient, Ablaufoptionen(codes=False), speicher=speicher)
    assert not any(f.quelle == "code" for f in erg2.fundstellen)


def test_heic_laden(tmp_path: Path, befund: Path) -> None:
    pillow_heif = pytest.importorskip("pillow_heif")
    from PIL import Image

    pillow_heif.register_heif_opener()
    with pymupdf.open(befund) as d:
        pix = d[0].get_pixmap(dpi=100, alpha=False)
    bild = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    heic = tmp_path / "foto.heic"
    bild.save(heic, format="HEIF")
    doc = oeffne(heic)
    assert len(doc) == 1 and doc[0].get_images()
    doc.close()
