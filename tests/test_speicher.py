"""Letters are stable per patient; the key survives reopening the file."""

from __future__ import annotations

from pathlib import Path

from anonymed.kern.modell import Patient
from anonymed.pseudonyme.speicher import Pseudonymspeicher, buchstabe
from anonymed.pseudonyme.vergabe import Pseudonymvergabe


def test_buchstabenfolge() -> None:
    folge = [buchstabe(i) for i in (0, 1, 25, 26, 27, 51, 52)]
    assert folge == ["A", "B", "Z", "AA", "AB", "AZ", "BA"]


def test_buchstabe_stabil(
    speicher: Pseudonymspeicher, patient: Patient, patientin: Patient
) -> None:
    assert speicher.buchstabe_fuer(patient) == "A"
    assert speicher.buchstabe_fuer(patientin) == "B"
    assert speicher.buchstabe_fuer(patient) == "A"
    assert speicher.finde(buchstabe_="b")[0]["nachname"] == patientin.nachname
    assert speicher.finde(suche=patient.nachname[:4].lower())[0]["buchstabe"] == "A"


def test_zuordnung_und_datei(tmp_path: Path, patient: Patient) -> None:
    pfad = tmp_path / "pseudonyme.sqlite"
    with Pseudonymspeicher(pfad) as s:
        b = s.buchstabe_fuer(patient)
        vergabe = Pseudonymvergabe(patient, b)
        vergabe.hauptperson()
        vergabe.geburtsdatum()
        vergabe.adresse()
        s.merke(patient, vergabe)
    assert pfad.exists()
    with Pseudonymspeicher(pfad) as s:
        z = s.zuordnung(patient.schluessel())
        assert z["Patient A"] == patient.vollname
        assert z[f"[geb. {patient.geburtsdatum.year}]"] == patient.geburtsdatum.strftime("%d.%m.%Y")
        assert patient.strasse in z["[Wohnort]"]
        info = s.patient_info(patient.schluessel())
        assert info["geschlecht"] == "m" and info["buchstabe"] == "A"
