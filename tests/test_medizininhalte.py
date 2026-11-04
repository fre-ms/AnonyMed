"""Medications and lab values: off by default, redacted on request, restored afterwards."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pymupdf
import pytest
from PySide6.QtWidgets import QApplication
from typer.testing import CliRunner

from pseudonymed.ablauf import Ablaufoptionen, abschliessen, pseudonymisiere, vorbereiten
from pseudonymed.cli import app
from pseudonymed.erkennung import fachbegriffe as fachmodul
from pseudonymed.erkennung import lexikon
from pseudonymed.erkennung import medizininhalte as medmodul
from pseudonymed.erkennung.vokabular import Vokabular
from pseudonymed.kern.modell import Art, Patient
from pseudonymed.pruefung.bericht import als_text
from pseudonymed.pseudonyme.rueckersetzung import rueckersetzen
from pseudonymed.pseudonyme.speicher import Pseudonymspeicher
from tests.test_cli import _patientendatei
from tests.test_gui import qapp  # noqa: F401

ZEILEN = [  # begriff, klasse, quelle
    ("Levetiracetam", "Medikament", "Wikidata"),
    ("Lamotrigin", "Medikament", "Wikidata"),
    ("Sauerstoff", "Medikament", "Wikidata"),
    ("ASS", "Medikament", "Wikidata"), ("ASS", "Erkrankung", "Wikidata"),  # also autism
    ("TSH", "Medikament", "Wikidata"), ("TSH", "Laborwert", "Laborwerte"),
    ("Hb", "Laborwert", "Laborwerte"), ("HbA1c", "Laborwert", "Laborwerte"),
    ("Kreatinin", "Laborwert", "Laborwerte"),
    ("Zuname", "Laborwert", "Wikidata"),  # the noisy Wikidata lab rows stay unused
]


def _caches_leeren() -> None:
    medmodul.standard.cache_clear()
    fachmodul.standard.cache_clear()


@pytest.fixture
def lexikon_klein(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv("PSEUDONYMED_DATENORDNER", str(tmp_path / "daten"))
    _caches_leeren()
    with Vokabular() as v:
        v.verbindung.executescript(lexikon.SCHEMA)
        v.verbindung.executemany(
            "INSERT INTO lexikon (begriff, klasse, rolle, quelle) VALUES (?, ?, 'fach', ?)",
            ZEILEN)
        lexikon.erhoehe_version(v.verbindung)
        v.verbindung.commit()
        v.fuege_hinzu(["Marcumar"], "Medikament")  # a trade name of the user's own
        v.fuege_hinzu(["Levetiracetam"], "Nutzervokabular")
    yield tmp_path / "daten"
    _caches_leeren()


def _gefunden(text: str, medikamente: bool = True, laborwerte: bool = True) -> list[str]:
    e = medmodul.standard(medikamente, laborwerte)
    assert e is not None
    aus = []
    for k in e.kandidaten(text):
        ende = medmodul.messwert_ende(text, k.ende) if k.klasse == "Laborwert" else k.ende
        if ende is not None:
            aus.append(text[k.start:ende])
    return aus


def test_auswahl_der_listen(lexikon_klein: Path) -> None:
    assert medmodul.standard(False, False) is None
    e = medmodul.standard(True, True)
    assert e is not None and e.je_klasse == {"Medikament": 3, "Laborwert": 4}
    text = ("Levetiracetams Wirkung, Lamotrigin 50 mg, ASS 100 mg, Marcumar nach INR,"
            " Sauerstoff 2 l/min, TSH basal.")
    assert _gefunden(text) == ["Levetiracetams", "Lamotrigin", "Marcumar"]
    assert _gefunden("Lamotrigin, TSH 2,3 mU/l", medikamente=False) == ["TSH 2,3 mU/l"]
    assert _gefunden("Lamotrigin, TSH 2,3 mU/l", laborwerte=False) == ["Lamotrigin"]


def test_laborwerte_nur_mit_messwert(lexikon_klein: Path) -> None:
    text = ("TSH 2,3 mU/l, Hb: 12,4 g/dl, HbA1c von 6,2 %, Kreatinin < 1,1 mg/dl, TSH 4."
            " Kontrolle des TSH am 03.11.2026, Kreatinin erhöht, HB 12 ohne Einheit, hb 12,"
            " Zuname 12 mg/l.")
    assert _gefunden(text) == ["TSH 2,3 mU/l", "Hb: 12,4 g/dl", "HbA1c von 6,2 %",
                               "Kreatinin < 1,1 mg/dl", "TSH 4"]
    assert medmodul.messwert_ende("TSH 12.03.2026", 3) is None  # a date is no value
    assert medmodul.messwert_ende("TSH 2,3 im Normbereich", 3) == len("TSH 2,3")


def test_standardmaessig_bleibt_alles(lexikon_klein: Path, tmp_path: Path, patient: Patient,
                                      speicher: Pseudonymspeicher) -> None:
    quelle = tmp_path / "notiz.txt"
    quelle.write_text(f"{patient.vorname} {patient.nachname}: Levetiracetam 500 mg 1-0-1,"
                      " TSH 2,3 mU/l.\n", encoding="utf-8")
    erg = pseudonymisiere(quelle, patient, Ablaufoptionen(), speicher=speicher)
    text = erg.ziel_txt.read_text(encoding="utf-8")
    assert erg.freigegeben and "Levetiracetam 500 mg" in text and "TSH 2,3 mU/l" in text
    assert erg.bericht.optionen["medikamente_schwaerzen"] is False
    assert erg.bericht.optionen["laborwerte_schwaerzen"] is False


def test_textdatei_auf_wunsch(lexikon_klein: Path, tmp_path: Path, patient: Patient,
                              speicher: Pseudonymspeicher) -> None:
    quelle = tmp_path / "notiz.txt"
    quelle.write_text(
        f"{patient.vorname} {patient.nachname} nimmt Levetiracetam 500 mg 1-0-1 und Marcumar."
        " Labor: TSH 2,3 mU/l, Hb 12,4 g/dl. Levetiracetam wird fortgeführt.\n",
        encoding="utf-8")
    opt = Ablaufoptionen(medikamente_schwaerzen=True, laborwerte_schwaerzen=True)
    erg = pseudonymisiere(quelle, patient, opt, speicher=speicher)
    assert erg.freigegeben, als_text(erg.bericht)
    text = erg.ziel_txt.read_text(encoding="utf-8")
    for klartext in ("Levetiracetam", "Marcumar", "TSH", "2,3", "Hb 12"):
        assert klartext not in text, text
    assert "500 mg 1-0-1" in text  # the dose stays
    codes = [w for w in text.split("[")[1:] if w.startswith("Medikament ")]
    assert len(codes) == 3 and codes[0][:17] == codes[2][:17]  # same drug, same code
    assert text.count("[Laborwert ") == 2
    assert any(h.startswith("Medizinischer Inhalt: 5 Treffer") for h in erg.bericht.hinweise)
    z = speicher.zuordnung(patient.schluessel())
    zurueck = rueckersetzen(text, z, bekannte_kennungen=speicher.kennungen())
    assert "Levetiracetam 500 mg" in zurueck.text and "TSH 2,3 mU/l" in zurueck.text


def test_pdf_ohne_gruene_marke_unter_dem_treffer(lexikon_klein: Path, tmp_path: Path,
                                                 patient: Patient,
                                                 speicher: Pseudonymspeicher) -> None:
    pdf = tmp_path / "brief.pdf"
    doc = pymupdf.open()
    seite = doc.new_page()
    seite.insert_text((72, 100), f"Patient: {patient.vorname} {patient.nachname}", fontsize=11)
    seite.insert_text((72, 120), "Medikation: Levetiracetam 500 mg", fontsize=11)
    seite.insert_text((72, 140), "Levetiracetam gut vertragen", fontsize=11)
    doc.save(pdf)
    doc.close()
    opt = Ablaufoptionen(medikamente_schwaerzen=True)
    v = vorbereiten(pdf, patient, opt, speicher=speicher)
    treffer = [f for f in v.fundstellen if f.art == Art.MEDIZIN]
    assert [f.regel for f in treffer] == ["medizin.medikament"] * 2
    assert all(f.rects for f in treffer)

    def unter_treffern() -> int:
        return sum(1 for r in v.fachbegriffe_rects(0) for f in treffer if f in v.fundstellen
                   for b in f.rects if r[0] < b[2] and b[0] < r[2] and r[1] < b[3] and b[1] < r[3])

    gruen = len(v.fachbegriffe_rects(0))  # "Medikation" stays green
    assert gruen >= 1 and unter_treffern() == 0
    v.verwerfe(treffer[1])  # discarded: the term is protected again and green
    assert len(v.fachbegriffe_rects(0)) == gruen + 1 and unter_treffern() == 0
    erg = abschliessen(v)
    assert erg.freigegeben, als_text(erg.bericht)
    with pymupdf.open(erg.ziel_pdf) as aus:
        rest = aus[0].get_text()
    assert rest.count("Levetiracetam") == 1 and "[Medikament " in erg.text


def test_warnung_bei_leerer_liste(tmp_path: Path, patient: Patient, speicher: Pseudonymspeicher,
                                  monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PSEUDONYMED_DATENORDNER", str(tmp_path / "leer"))
    _caches_leeren()
    quelle = tmp_path / "notiz.txt"
    quelle.write_text("Levetiracetam 500 mg\n", encoding="utf-8")
    erg = pseudonymisiere(quelle, patient, Ablaufoptionen(medikamente_schwaerzen=True),
                          speicher=speicher)
    assert any("Medikamente schwärzen gewählt, aber die Liste ist leer" in w
               for w in erg.bericht.warnungen)
    _caches_leeren()


def test_oberflaeche(qapp: QApplication, lexikon_klein: Path) -> None:  # noqa: F811
    from pseudonymed.gui.hauptfenster import Hauptfenster
    from pseudonymed.gui.sitzung import Sitzung

    fenster = Hauptfenster(Sitzung())
    seite = fenster.analysieren
    assert not seite.medikamente.isChecked() and not seite.laborwerte.isChecked()
    seite.medikamente.setChecked(True)
    seite._optionen_uebernehmen()
    assert fenster.sitzung.optionen.medikamente_schwaerzen is True
    assert fenster.sitzung.optionen.laborwerte_schwaerzen is False
    fenster.close()


def test_kommandozeile(lexikon_klein: Path, tmp_path: Path, patient: Patient) -> None:
    quelle = tmp_path / "notiz.txt"
    quelle.write_text("Levetiracetam 500 mg, TSH 2,3 mU/l.\n", encoding="utf-8")
    r = CliRunner().invoke(app, [
        "pseudonymisieren", str(quelle), "--patient", str(_patientendatei(tmp_path, patient)),
        "--speicher", str(tmp_path / "s.sqlite"), "--laborwerte-schwaerzen"])
    assert r.exit_code == 0, r.output
    text = (tmp_path / "notiz_pseudonym.txt").read_text(encoding="utf-8")
    assert "Levetiracetam 500 mg" in text and "[Laborwert " in text and "TSH" not in text
