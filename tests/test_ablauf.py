"""End-to-end: synthetic report in, anonymised PDF plus text and report out, check passes."""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

import pymupdf
import pytest

from anonymed import ablauf
from anonymed.ablauf import Ablaufoptionen, anonymisiere
from anonymed.kern.modell import Patient
from anonymed.pruefung.bericht import als_text
from anonymed.pseudonyme.kennung import kennung_gueltig
from anonymed.pseudonyme.speicher import Pseudonymspeicher
from anonymed.schwaerzen.pdf import Schwaerzoptionen, Seitenergebnis, rastere
from tests.synthetisch import datum_lang, erzeuge_befund


def klardaten(p: Patient) -> list[str]:
    geb = p.geburtsdatum
    assert geb is not None
    return [
        p.nachname, p.vorname, p.nachname.upper(), p.strasse, p.ort, p.plz + " " + p.ort,
        geb.strftime("%d.%m.%Y"), geb.strftime("%d.%m.%y"), datum_lang(geb),
        p.versichertennummer, p.patienten_id, p.emails[0], p.aktenzeichen[0],
        re.sub(r"\D", "", p.telefone[0])[-6:],
        p.angehoerige[0].vorname, p.zuweiser[0].nachname, p.zuweiser[0].strasse,
    ]


def gefundene_klardaten(text: str, p: Patient) -> list[str]:
    flach = re.sub(r"[\s\-/().]+", "", text).casefold()
    return [k for k in klardaten(p) if re.sub(r"[\s\-/().]+", "", k).casefold() in flach]


def test_rundlauf(befund: Path, patient: Patient, speicher: Pseudonymspeicher) -> None:
    erg = anonymisiere(befund, patient, Ablaufoptionen(), speicher=speicher)
    assert erg.freigegeben, als_text(erg.bericht)
    assert erg.bericht.bestanden
    k = erg.kennung
    assert len(k) == 6 and kennung_gueltig(k)
    assert erg.ziel_pdf and erg.ziel_pdf.exists()
    assert erg.ziel_txt and erg.ziel_txt.exists()
    assert erg.ziel_bericht and erg.ziel_bericht.exists()

    with pymupdf.open(erg.ziel_pdf) as d:
        pdf_text = "\n".join(p.get_text("text") for p in d)
        assert d.metadata["title"] == "" and d.metadata["author"] == ""
        assert d.metadata["subject"].startswith("anonymisiert mit AnonyMed")
        assert d.get_toc() == []
        assert d.embfile_count() == 0
        assert all(len(list(p.annots())) == 0 for p in d)
    assert gefundene_klardaten(pdf_text, patient) == []
    txt = erg.ziel_txt.read_text(encoding="utf-8")
    assert gefundene_klardaten(txt, patient) == []
    # the actual content stays untouched
    assert re.search(r"\[Straße [A-Z2-9]{6}\]", txt) and re.search(r"\[PLZ Ort [A-Z2-9]{6}\]", txt)
    assert "[Wohnort]" not in txt
    for erwartet in (f"Patient {k}", f"[geb. {patient.geburtsdatum.year}]", "Dr. Z1",
                     f"Angehörige {k}-1", "[Versichertennummer]", "max. 3 Tabletten",
                     "Ramipril 5 mg",
                     "Sehr geehrte Frau Kollegin Dr. Z1,", "=== Seite 2 ==="):
        assert erwartet in txt, erwartet
    assert f"Patient {k}" in pdf_text and "Ramipril 5 mg" in pdf_text
    # the report carries no clear data
    bericht = erg.ziel_bericht.read_text(encoding="utf-8")
    assert gefundene_klardaten(bericht, patient) == []
    daten = json.loads(bericht)
    assert daten["bestanden"] is True and daten["seiten"] == 2
    assert len(daten["fundstellen"]) >= 15
    # the key is in the local store only
    z = speicher.zuordnung(patient.schluessel())
    assert z[f"Patient {k}"] == patient.vollname
    assert z["[E-Mail]"] == patient.emails[0]
    assert patient.strasse in z.values() and f"{patient.plz} {patient.ort}" in z.values()
    # the referrer's practice address has its own street code
    assert sum(1 for p in z if p.startswith("[Straße ")) == 2


def test_kennungen_je_person(tmp_path: Path, patient: Patient, patientin: Patient,
                             speicher: Pseudonymspeicher) -> None:
    a = anonymisiere(erzeuge_befund(tmp_path / "a.pdf", patient), patient, speicher=speicher)
    b = anonymisiere(erzeuge_befund(tmp_path / "b.pdf", patientin), patientin, speicher=speicher)
    a2 = anonymisiere(erzeuge_befund(tmp_path / "a2.pdf", patient), patient, speicher=speicher)
    assert a.kennung == a2.kennung != b.kennung
    assert f"Patientin {b.kennung}" in b.text and f"Patient {a.kennung}" in a2.text


def test_buchstabenschema_im_ablauf(befund: Path, patient: Patient,
                                    buchstabenspeicher: Pseudonymspeicher) -> None:
    erg = anonymisiere(befund, patient, speicher=buchstabenspeicher)
    assert erg.kennung == "A" and "Patient A" in erg.text and "Angehörige A-1" in erg.text
    assert "[Straße " in erg.text  # address codes do not depend on the identifier scheme


def test_schreibvariante_bekommt_gleiche_kennung(tmp_path: Path, patient: Patient,
                                                 speicher: Pseudonymspeicher) -> None:
    import dataclasses

    erg = anonymisiere(erzeuge_befund(tmp_path / "a.pdf", patient), patient, speicher=speicher)
    variante = dataclasses.replace(patient, nachname=patient.nachname + "z",
                                   vorname=patient.vorname.upper())
    erg2 = anonymisiere(erzeuge_befund(tmp_path / "b.pdf", variante), variante, speicher=speicher)
    assert erg2.kennung == erg.kennung
    assert any("zugeordnet" in w for w in erg2.bericht.warnungen)


def test_alter_statt_jahr(befund: Path, patient: Patient, speicher: Pseudonymspeicher) -> None:
    erg = anonymisiere(befund, patient, Ablaufoptionen(datumsform="alter"), speicher=speicher)
    assert erg.freigegeben
    heute = dt.date.today()
    alter = heute.year - patient.geburtsdatum.year - (
        (heute.month, heute.day) < (patient.geburtsdatum.month, patient.geburtsdatum.day))
    assert f"[Alter {alter}]" in erg.text


def test_rastern(befund: Path, patient: Patient, speicher: Pseudonymspeicher) -> None:
    opt = Ablaufoptionen(schwaerzen=Schwaerzoptionen(rastern=True, dpi=72))
    erg = anonymisiere(befund, patient, opt, speicher=speicher)
    assert erg.freigegeben
    with pymupdf.open(erg.ziel_pdf) as d:
        assert all(p.get_text("text").strip() == "" for p in d)
        assert len(d) == 2
    assert any("gerastert" in w for w in erg.bericht.warnungen)


def test_gegenprobe_blockiert(befund: Path, patient: Patient, speicher: Pseudonymspeicher,
                              monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ablauf, "schwaerze_seite", lambda *a, **k: Seitenergebnis(0, True))
    erg = anonymisiere(befund, patient, speicher=speicher)
    assert not erg.freigegeben
    assert erg.ziel_pdf is None and erg.ziel_txt is None
    assert not (befund.parent / "befund_anonym.pdf").exists()
    assert erg.bericht.gegenprobe is not None and erg.bericht.gegenprobe.resttreffer
    assert any("Nicht freigegeben" in w for w in erg.bericht.warnungen)
    assert "NICHT bestanden" in als_text(erg.bericht)


def test_scan_ohne_textebene_wird_nicht_freigegeben(tmp_path: Path, befund: Path, patient: Patient,
                                                     speicher: Pseudonymspeicher) -> None:
    with pymupdf.open(befund) as d:
        scan = rastere(d, dpi=72)
        scan_pfad = tmp_path / "scan.pdf"
        scan.save(scan_pfad)
        scan.close()
    erg = anonymisiere(scan_pfad, patient, speicher=speicher)
    assert not erg.freigegeben
    assert not erg.bericht.textebene
    assert any("ohne Textebene" in w for w in erg.bericht.warnungen)


def test_bild_als_eingabe(tmp_path: Path, befund: Path, patient: Patient,
                          speicher: Pseudonymspeicher) -> None:
    with pymupdf.open(befund) as d:
        bild = tmp_path / "foto.png"
        d[0].get_pixmap(dpi=72).save(bild)
    erg = anonymisiere(bild, patient, speicher=speicher)
    assert not erg.freigegeben  # no OCR yet (M3)
    assert erg.bericht.seiten == 1
