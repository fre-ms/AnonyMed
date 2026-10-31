"""Text files (transcripts, notes, subtitles): rules, fuzzy search, marking, output."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from anonymed.ablauf import (
    Ablaufoptionen,
    Textvorbereitung,
    abschliessen,
    anonymisiere,
    vorbereiten,
)
from anonymed.cli import app
from anonymed.dokument.textdatei import analysetext, ist_textdatei, lese_textdatei
from anonymed.erkennung import ner as nermodul
from anonymed.kern.modell import Patient
from anonymed.pruefung.bericht import als_text
from anonymed.pseudonyme.speicher import Pseudonymspeicher
from tests.synthetisch import erzeuge_transkript, verschrieben
from tests.test_ablauf import gefundene_klardaten

_TIMING = re.compile(r"^\d\d:\d\d:\d\d[,.]\d{3} --> .*$", re.MULTILINE)


@pytest.fixture
def transkript(tmp_path: Path, patient: Patient) -> Path:
    return erzeuge_transkript(tmp_path / "gespraech.txt", patient)


def test_textdatei_rundlauf(transkript: Path, patient: Patient,
                            speicher: Pseudonymspeicher) -> None:
    erg = anonymisiere(transkript, patient, Ablaufoptionen(), speicher=speicher)
    assert erg.freigegeben and erg.ist_text and erg.ziel_pdf is None, als_text(erg.bericht)
    assert erg.ziel == erg.ziel_txt == transkript.with_name("gespraech_anonym.txt")
    aus = erg.ziel.read_text(encoding="utf-8")
    assert gefundene_klardaten(aus, patient) == []
    assert verschrieben(patient.nachname) not in aus  # the misspelt surname: fuzzy search
    assert any(f.quelle == "unscharf" for f in erg.fundstellen)
    for behalten in ("Ramipril", "Atorvastatin", "Arterielle Hypertonie", "MoCA ergab 22 von 30"):
        assert behalten in aus
    assert f"Patient {erg.kennung}" in aus and f"Angehörige {erg.kennung}-1" in aus
    assert "[geb. " in aus and "[Straße " in aus and "[Ort " in aus
    assert "[Telefon] oder [E-Mail]." in aus  # sentence end after an address is kept
    assert aus.count("\n") == transkript.read_text(encoding="utf-8").count("\n")
    bericht = json.loads(erg.ziel_bericht.read_text(encoding="utf-8"))
    assert bericht["bestanden"] and bericht["optionen"]["unscharf_text"] is True
    assert gefundene_klardaten(json.dumps(bericht), patient) == []


@pytest.mark.parametrize("format_", ["srt", "vtt", "md"])
def test_untertitel_und_markdown_bleiben_intakt(tmp_path: Path, patient: Patient,
                                                speicher: Pseudonymspeicher, format_: str) -> None:
    quelle = erzeuge_transkript(tmp_path / f"gespraech.{format_}", patient, format_)
    erg = anonymisiere(quelle, patient, Ablaufoptionen(), speicher=speicher)
    assert erg.freigegeben, als_text(erg.bericht)
    assert erg.ziel.suffix == f".{format_}"
    vorher = quelle.read_text(encoding="utf-8")
    aus = erg.ziel.read_text(encoding="utf-8")
    assert gefundene_klardaten(aus, patient) == []
    assert _TIMING.findall(vorher) == _TIMING.findall(aus)
    if format_ == "srt":
        assert re.findall(r"^\d+$", vorher, re.MULTILINE) == re.findall(r"^\d+$", aus, re.MULTILINE)
    if format_ == "vtt":
        assert aus.startswith("WEBVTT")
    if format_ == "md":
        assert aus.startswith("# Gespräch") and aus.count("\n- ") == vorher.count("\n- ")


def test_analysetext_blendet_nur_zeitmarken_aus() -> None:
    srt = ("1\n00:00:01,000 --> 00:00:03,500 X:10\nHallo Herr Flantz.\n\n"
           "2\n00:00:04,000 --> 00:00:05,000\n")
    a = analysetext(srt, ".srt")
    assert len(a) == len(srt) and a.count("\n") == srt.count("\n")
    assert "Hallo Herr Flantz." in a and "-->" not in a and a.split("\n")[0].strip() == ""
    txt = "[00:00.000 --> 00:04.000]  Guten Tag"
    assert analysetext(txt, ".txt").strip() == "Guten Tag"
    assert analysetext("3\nZeile 3\n", ".txt") == "3\nZeile 3\n"  # numbers stay outside subtitles
    assert ist_textdatei("a.TXT") and ist_textdatei("b.vtt") and not ist_textdatei("c.pdf")


def test_genau_laesst_verschriebenes_stehen(transkript: Path, patient: Patient,
                                            speicher: Pseudonymspeicher) -> None:
    erg = anonymisiere(transkript, patient, Ablaufoptionen(unscharf_text=False), speicher=speicher)
    assert erg.freigegeben
    aus = erg.ziel.read_text(encoding="utf-8")
    assert verschrieben(patient.nachname) in aus and patient.nachname not in aus
    assert not any(f.quelle == "unscharf" for f in erg.fundstellen)


def test_grossschreibung_schuetzt_kleine_woerter(tmp_path: Path,
                                                 speicher: Pseudonymspeicher) -> None:
    p = Patient(nachname="Lang", vorname="Erwin", geschlecht="m")
    quelle = tmp_path / "n.txt"
    quelle.write_text("Herr Lanq wartet schon lang. Land und Leute. Herr Lang kommt.\n",
                      encoding="utf-8")
    erg = anonymisiere(quelle, p, Ablaufoptionen(), speicher=speicher)
    aus = erg.ziel.read_text(encoding="utf-8")
    assert "lang." in aus and "Lanq" not in aus and "Herr Lang" not in aus
    # "Land" is capitalised and within tolerance: a guess the review step shows in orange
    assert any(f.quelle == "unscharf" and f.text == "Land" for f in erg.fundstellen)


def test_verwerfen_markieren_und_gegenprobe(transkript: Path, patient: Patient,
                                            speicher: Pseudonymspeicher) -> None:
    v = vorbereiten(transkript, patient, Ablaufoptionen(), speicher=speicher)
    assert isinstance(v, Textvorbereitung) and v.ist_text and v.text
    assert v.fachbegriffe_anzahl >= 3 and v.fachbegriffe_spannen(0)
    assert v.fachbegriffe_rects(0) == [] and v.ocr_seiten == []
    zuweiser = next(f for f in v.fundstellen if f.regel == "zuweiser1.name")
    v.verwerfe(zuweiser)
    assert zuweiser.text not in [f.text for f in v.fundstellen]
    a = v.text.index("Wörter vergesse")
    f = v.markiere(0, a, a + len("Wörter vergesse"), "[Symptom]")
    assert f.quelle == "manuell" and f.text == "Wörter vergesse"
    with pytest.raises(ValueError):
        v.markiere(0, 0, 0)
    erg = abschliessen(v)
    assert erg.freigegeben, als_text(erg.bericht)
    aus = erg.ziel.read_text(encoding="utf-8")
    assert zuweiser.text in aus and "[Symptom]" in aus and "Wörter vergesse" not in aus
    assert gefundene_klardaten(aus.replace(zuweiser.text, ""), patient) == []


def test_markierung_ersetzt_ueberlappende_treffer(transkript: Path, patient: Patient,
                                                   speicher: Pseudonymspeicher) -> None:
    v = vorbereiten(transkript, patient, Ablaufoptionen(), speicher=speicher)
    a = v.text.index("Meine Frau")
    b = v.text.index("hat gemerkt")
    v.markiere(0, a, b, "[Angabe]")
    assert not any(f.regel == "angehoerige1.vollname" for f in v.fundstellen)
    erg = abschliessen(v)
    assert erg.freigegeben and "[Angabe]" in erg.text


def test_kodierung_windows(tmp_path: Path, patient: Patient, speicher: Pseudonymspeicher) -> None:
    quelle = tmp_path / "notiz.txt"
    quelle.write_bytes(f"Herr {patient.nachname} klagt über Schwäche.\r\n".encode("cp1252"))
    assert lese_textdatei(quelle) == f"Herr {patient.nachname} klagt über Schwäche.\n"
    erg = anonymisiere(quelle, patient, Ablaufoptionen(), speicher=speicher)
    assert erg.freigegeben and "Schwäche" in erg.ziel.read_text(encoding="utf-8")


@pytest.mark.skipif(not nermodul.verfuegbar(), reason="Extra 'ner' nicht installiert")
def test_ner_in_textdatei(tmp_path: Path, patient: Patient, speicher: Pseudonymspeicher) -> None:
    quelle = tmp_path / "n.txt"
    quelle.write_text("Die Nachbarin Erika Hoffmann brachte ihn. Verdacht auf Morbus Parkinson,"
                      " Babinski negativ. Er lebt in Dresden.\n", encoding="utf-8")
    erg = anonymisiere(quelle, patient, Ablaufoptionen(ner=True), speicher=speicher)
    aus = erg.ziel.read_text(encoding="utf-8")
    assert "Erika Hoffmann" not in aus and "Dresden" not in aus
    assert "Morbus Parkinson" in aus and "Babinski" in aus
    assert any(f.regel == "ner.person" for f in erg.fundstellen)


def test_cli_textdatei(tmp_path: Path, patient: Patient) -> None:
    from tests.test_cli import _patientendatei

    runner = CliRunner()
    pdatei = _patientendatei(tmp_path, patient)
    quelle = erzeuge_transkript(tmp_path / "gespraech.txt", patient)
    sp = str(tmp_path / "p.sqlite")
    r = runner.invoke(app, ["anonymisieren", str(quelle), "--patient", str(pdatei),
                            "--speicher", sp])
    assert r.exit_code == 0, r.output
    ziel = tmp_path / "gespraech_anonym.txt"
    assert ziel.exists() and "Freigegeben" in r.output and "Text:" not in r.output
    assert gefundene_klardaten(r.output, patient) == []
    r = runner.invoke(app, ["pruefen", str(ziel), "--patient", str(pdatei), "--speicher", sp])
    assert r.exit_code == 0 and "bestanden" in r.output, r.output
    r = runner.invoke(app, ["pruefen", str(quelle), "--patient", str(pdatei), "--speicher", sp])
    assert r.exit_code == 2 and "NICHT" in r.output
    r = runner.invoke(app, ["anonymisieren", str(quelle), "--patient", str(pdatei),
                            "--speicher", sp, "--genau"])
    assert r.exit_code == 0 and verschrieben(patient.nachname) in ziel.read_text(encoding="utf-8")
