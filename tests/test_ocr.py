"""Scans, photos, noise, HEIC and barcodes: read by OCR, blanked in the pixels, re-checked."""

from __future__ import annotations

import io
import random
from pathlib import Path

import pymupdf
import pytest

from anonymed.ablauf import Ablaufoptionen, anonymisiere
from anonymed.dokument import aufbereitung as aufmodul
from anonymed.dokument import ocr as ocrmodul
from anonymed.dokument.laden import oeffne
from anonymed.dokument.ocr import OCRWort, seitentext_aus_woerter
from anonymed.erkennung import codes as codemodul
from anonymed.kern.modell import Art, Patient
from anonymed.pruefung.bericht import als_text
from anonymed.pseudonyme.speicher import Pseudonymspeicher
from anonymed.schwaerzen.pdf import Schwaerzoptionen, rastere
from tests.synthetisch import datum_lang
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


def _wahre_boxen(befund: Path, patient: Patient) -> list[tuple[int, pymupdf.Rect]]:
    """Rectangles of the identifier words as the source text layer knows them."""
    geb = patient.geburtsdatum
    gesucht = {patient.nachname, patient.vorname, patient.nachname.upper(), patient.vorname.upper(),
               geb.strftime("%d.%m.%Y"), geb.strftime("%d.%m.%y"), patient.versichertennummer,
               patient.patienten_id, patient.plz, patient.ort, patient.emails[0],
               patient.angehoerige[0].vorname, patient.zuweiser[0].nachname,
               str(geb.day) + ".", str(geb.year)}
    gesucht |= set(datum_lang(geb).split())
    boxen = []
    with pymupdf.open(befund) as d:
        for i, page in enumerate(d):
            for begriff in gesucht:
                if len(begriff) < 3:
                    continue
                for rect in page.search_for(begriff):  # character-precise, no punctuation
                    boxen.append((i, rect))
    assert len(boxen) >= 15
    return boxen


def _tinte_in(doc: pymupdf.Document, seite: int, rect: pymupdf.Rect, dpi: int = 200) -> int:
    pix = doc[seite].get_pixmap(dpi=dpi, clip=rect, colorspace=pymupdf.csGRAY, alpha=False)
    return sum(1 for v in pix.samples if v < 100)


@braucht_backend
@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("stoerung", ["sauber", "verrauscht"])
def test_keine_tinte_unter_den_treffern(tmp_path: Path, befund: Path, patient: Patient,
                                        speicher: Pseudonymspeicher, backend: str,
                                        stoerung: str) -> None:
    """Pseudonyms are drawn in the fill colour here, so any dark pixel inside a
    known identifier box is a remnant of the original glyphs."""
    kw = dict(dpi=150, rauschen=0.01, drehung=0.0) if stoerung == "verrauscht" else {}
    scan = _scan(befund, tmp_path / "scan.pdf", **kw)
    opt = Ablaufoptionen(ocr=backend, schwaerzen=Schwaerzoptionen(textfarbe=(0.88, 0.88, 0.88)))
    erg = anonymisiere(scan, patient, opt, speicher=speicher)
    if not erg.freigegeben:
        # a block is a legitimate outcome on a noisy scan, as long as it is a block for cause
        assert stoerung == "verrauscht", als_text(erg.bericht)
        assert erg.bericht.gegenprobe is not None and erg.bericht.gegenprobe.resttreffer
        return
    reste = []
    with pymupdf.open(erg.ziel_pdf) as d:
        for seite, rect in _wahre_boxen(befund, patient):
            innen = pymupdf.Rect(rect.x0 + 0.5, rect.y0 + 2, rect.x1 - 0.5, rect.y1 - 2)
            tinte = _tinte_in(d, seite, innen)
            # an edge fringe or noise specks are allowed; a digit leaves 40+ dark pixels
            schwelle = 25 if stoerung == "verrauscht" else 8
            if tinte > schwelle:
                reste.append((seite + 1, [round(v) for v in rect], tinte))
    assert reste == [], reste


def test_unscharfe_suche() -> None:
    from anonymed.erkennung.unscharf import distanz, finde_unscharf, ziele_fuer
    from anonymed.pseudonyme.vergabe import Pseudonymvergabe
    from tests.synthetisch import synthetischer_patient

    assert distanz("Juli", "Jull", 1) == 1 and distanz("Flantz", "Fiantz", 1) == 1
    assert distanz("Flantz", "Zahn", 2) > 2
    p = synthetischer_patient(7)
    v = Pseudonymvergabe(p, "K7Q4MX", geheimnis=bytes(32))
    ziele = ziele_fuer(p, v)
    woerter = [OCRWort(w, 50 + 40 * i, 100, 85 + 40 * i, 112)
               for i, w in enumerate(["Geboren", "am", "21.", "Jull", "1959", "in", "Rlesa."])]
    woerter += [OCRWort(w, 50 + 40 * i, 130, 85 + 40 * i, 142)
                for i, w in enumerate(["Herr", "Fiantz,", "Gotthard", "und", "Zahn"])]
    s = seitentext_aus_woerter(woerter, 0, 595, 842)
    treffer = finde_unscharf(s, ziele, [])
    texte = {f.text: f.regel for f in treffer}
    assert texte["21. Jull 1959"] == "unscharf.datum.geburtsdatum"
    assert texte["Rlesa"] == "unscharf.adresse.ort"
    assert "Fiantz, Gotthard" in texte or "Fiantz" in texte
    assert "Zahn" not in texte and "Geboren" not in texte
    # a long token split by OCR is found across the pieces
    geteilt = [OCRWort(w, 50 + 60 * i, 160, 100 + 60 * i, 172)
               for i, w in enumerate(["Mail", "gotthard.", "flantz@example.", "org", "Ende"])]
    s2 = seitentext_aus_woerter(geteilt, 0, 595, 842)
    unscharf = [f.text for f in finde_unscharf(s2, ziele, [])]
    assert "gotthard. flantz@example. org" in unscharf


def test_unlesbare_zeile_wird_geschwaerzt() -> None:
    from anonymed.dokument.ocr import unlesbare_regionen

    fragmente = ["G6ot", "n", "e", "n", "e", "i", "l", "e", "5g"]
    woerter = [OCRWort(w, 50 + 30 * i, 100, 70 + 30 * i, 112, k)
               for i, (w, k) in enumerate(zip(fragmente, [0.87, 0.5, 0.99, 0.9, 0.25, 0.5, 0.47,
                                                           0.97, 0.81], strict=True))]
    woerter += [OCRWort(w, 50 + 40 * i, 130, 85 + 40 * i, 142, 0.99)
                for i, w in enumerate(["in", "Riesa.", "Flantz,", "berichtet"])]
    s = seitentext_aus_woerter(woerter, 0, 595, 842)
    regionen = unlesbare_regionen(s)
    assert len(regionen) == 1
    r = regionen[0]
    assert r.pseudonym == "[unlesbar]" and r.quelle == "ocr" and r.regel == "ocr.unlesbar"
    assert s.text[r.start:r.ende] == "G6ot n e n e i l e 5g"
    assert "Riesa" not in s.text[r.start:r.ende]


@braucht_backend
def test_scan_bekommt_textebene(tmp_path: Path, befund: Path, patient: Patient,
                                speicher: Pseudonymspeicher) -> None:
    """The anonymised scan carries an invisible text layer with the OCR words minus
    the hits; without the option only the drawn pseudonyms are text."""
    scan = _scan(befund, tmp_path / "scan.pdf")
    erg = anonymisiere(scan, patient, Ablaufoptionen(), speicher=speicher)
    assert erg.freigegeben, als_text(erg.bericht)
    with pymupdf.open(erg.ziel_pdf) as d:
        text = "\n".join(p.get_text("text") for p in d)
    assert "Ramipril" in text and "Diagnosen" in text and "Atorvastatin" in text
    assert f"Patient {erg.kennung}" in text  # the visible pseudonyms are text too
    assert gefundene_klardaten(text, patient) == []
    assert not any(k in text for k in (patient.nachname, patient.vorname))
    ohne = anonymisiere(scan, patient, Ablaufoptionen(
        suffix="_ohne", schwaerzen=Schwaerzoptionen(textebene=False)), speicher=speicher)
    with pymupdf.open(ohne.ziel_pdf) as d:
        text2 = "\n".join(p.get_text("text") for p in d)
    assert "Ramipril" not in text2 and f"Patient {erg.kennung}" in text2


@braucht_backend
def test_ocr_text_korrigieren(tmp_path: Path, befund: Path, patient: Patient,
                              speicher: Pseudonymspeicher) -> None:
    """The user corrects the recognised text; the correction feeds the text file and
    the text layer and is searched again, the image redaction keeps the OCR boxes."""
    from anonymed.ablauf import abschliessen, vorbereiten

    scan = _scan(befund, tmp_path / "scan.pdf")
    v = vorbereiten(scan, patient, Ablaufoptionen(), speicher=speicher)
    assert v.seiten[0].ocr is not None
    ocr = v.ocr_text(0)
    assert "Diagnosen" in ocr
    neu = ocr.rstrip("\n") + f"\nZusatz: {patient.vorname} {patient.nachname} aus {patient.ort}.\n"
    treffer = v.setze_ocr_text(0, neu)
    assert treffer and all(f.quelle == "korrektur" and not f.rects for f in treffer)
    assert {f.art for f in treffer} >= {Art.NAME, Art.ORT}
    assert v.ocr_text(0) == neu and v.korrektur_treffer
    assert v.setze_ocr_text(0, ocr) == [] and v.seiten[0].korrektur is None  # back to OCR
    v.setze_ocr_text(0, neu)
    erg = abschliessen(v)
    assert erg.freigegeben, als_text(erg.bericht)
    txt = erg.ziel_txt.read_text(encoding="utf-8")
    assert f"Zusatz: Patient {erg.kennung} aus [Ort " in txt, txt
    assert gefundene_klardaten(txt, patient) == []
    with pymupdf.open(erg.ziel_pdf) as d:
        text = d[0].get_text("text")
    assert "Zusatz:" in text and patient.ort not in text  # the invisible layer is the correction
    assert any(f["quelle"] == "korrektur" for f in erg.bericht.fundstellen)


@braucht_backend
@pytest.mark.skipif(not aufmodul.verfuegbar(), reason="OpenCV fehlt")
def test_aufbereitung_vor_der_ocr(tmp_path: Path, befund: Path, patient: Patient,
                                  speicher: Pseudonymspeicher) -> None:
    """A tilted, shadowed scan: the loading phase suggests straightening and background
    clean-up, the pipeline applies it before OCR and records it; the hits are found."""
    from anonymed.ablauf import lade

    scan = _scan(befund, tmp_path / "scan.pdf", drehung=2.5)
    vorlage = lade(scan)
    assert vorlage.kandidaten == [0, 1] and not vorlage.foto
    a = vorlage.vorschlaege[0]
    assert a.begradigen and abs(abs(a.winkel) - 2.5) < 0.5, a
    assert vorlage.vorschau(0, a)[0].startswith(b"\x89PNG")
    vorlage.schliessen()
    assert lade(befund).kandidaten == []  # a text layer needs no clean-up
    erg = anonymisiere(scan, patient, Ablaufoptionen(), speicher=speicher)
    assert erg.freigegeben, als_text(erg.bericht)
    assert any("aufbereitet" in w and "begradigt" in w for w in erg.bericht.warnungen)
    assert erg.bericht.optionen["aufbereitung"]["1"]["begradigen"] is True
    assert gefundene_klardaten(erg.text, patient) == [] and "Ramipril" in erg.text
    ohne = anonymisiere(scan, patient, Ablaufoptionen(aufbereiten="aus", suffix="_roh"),
                        speicher=speicher)
    assert not any("aufbereitet" in w for w in ohne.bericht.warnungen)
    assert "aufbereitung" not in ohne.bericht.optionen
    # an explicit choice wins over the suggestion
    from anonymed.ablauf import abschliessen, vorbereiten
    from anonymed.dokument.aufbereitung import Aufbereitung

    v = vorbereiten(scan, patient, Ablaufoptionen(suffix="_eigen"), speicher=speicher,
                    aufbereitung={0: Aufbereitung(masken=[(56, 60, 400, 95)])})
    assert set(v.aufbereitet) == {0} and v.aufbereitet[0].masken
    erg3 = abschliessen(v)
    assert erg3.freigegeben


@braucht_backend
def test_lesen_dann_analysieren(tmp_path: Path, befund: Path, patient: Patient,
                                speicher: Pseudonymspeicher) -> None:
    """Reading and detection as two phases: the text can be corrected in between, the
    detection then runs over the corrected text as well."""
    from anonymed.ablauf import abschliessen, analysiere, lese

    scan = _scan(befund, tmp_path / "scan.pdf")
    lesung = lese(scan, Ablaufoptionen())
    assert lesung.ocr_seiten == [1, 2] and lesung.hat_ocr(0) and not lesung.verbraucht
    assert "Diagnosen" in lesung.text(0) and lesung.seitenbild(0).startswith(b"\x89PNG")
    lesung.setze_ocr_text(0, lesung.ocr_text(0).rstrip("\n")
                          + f"\nZusatz: {patient.vorname} {patient.nachname}.\n")
    assert lesung.seiten[0].korrektur is not None
    lesung.setze_ocr_text(1, lesung.ocr_text(1))  # unchanged text: no correction
    assert lesung.seiten[1].korrektur is None
    v = analysiere(lesung, patient, Ablaufoptionen(), speicher=speicher)
    assert lesung.verbraucht and v.seiten[0].korrektur_treffer
    assert any(f.quelle == "korrektur" for f in v.korrektur_treffer)
    with pytest.raises(RuntimeError):
        analysiere(lesung, patient, Ablaufoptionen(), speicher=speicher)
    erg = abschliessen(v)
    assert erg.freigegeben, als_text(erg.bericht)
    txt = erg.ziel_txt.read_text(encoding="utf-8")
    assert f"Zusatz: Patient {erg.kennung}." in txt and gefundene_klardaten(txt, patient) == []


@braucht_backend
@pytest.mark.skipif(not aufmodul.verfuegbar(), reason="OpenCV fehlt")
def test_vorschlag_schadet_der_ocr_nicht(tmp_path: Path, befund: Path) -> None:
    """Guard for the automatic clean-up: on a noisy, slightly tilted scan the OCR text
    after the suggested steps must be about as close to the truth as without them."""
    import difflib

    from anonymed.ablauf import lade, lese
    from tests.synthetisch import befund_text

    wahr = " ".join("\n".join(befund_text(_patient_aus(befund))[0]).split())
    scan = _scan(befund, tmp_path / "scan.pdf", dpi=150, rauschen=0.01, drehung=1.0)
    roh = lese(scan, Ablaufoptionen(aufbereiten="aus"))
    vorschlag = lade(scan).vorschlaege[0]
    auto = lese(scan, Ablaufoptionen(aufbereiten="aus"), {0: vorschlag})

    def guete(text: str) -> float:
        return difflib.SequenceMatcher(None, wahr, " ".join(text.split())).ratio()

    g_roh, g_auto = guete(roh.ocr_text(0)), guete(auto.ocr_text(0))
    roh.schliessen()
    auto.schliessen()
    assert g_roh > 0.95, g_roh
    assert g_auto >= g_roh - 0.03, (g_roh, g_auto, vorschlag)
    text = lese(scan, Ablaufoptionen(aufbereiten="aus"),
                {0: aufmodul.Aufbereitung(hochskalieren=2, textmodus=True)})
    g_text = guete(text.ocr_text(0))
    text.schliessen()
    assert g_text >= g_roh - 0.03, (g_roh, g_text)  # the letter mode must not hurt


def _patient_aus(befund: Path):
    from tests.synthetisch import synthetischer_patient

    return synthetischer_patient(7, "m")
