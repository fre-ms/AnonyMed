"""Pseudonyms in model output go back to clear data, inflections included."""

from __future__ import annotations

from anonymed.kern.modell import Patient
from anonymed.pseudonyme.rueckersetzung import rueckersetzen
from anonymed.pseudonyme.vergabe import Pseudonymvergabe


def _zuordnung(patient: Patient) -> dict[str, str]:
    v = Pseudonymvergabe(patient, "A")
    v.hauptperson()
    v.geburtsdatum()
    v.adresse()
    v.angehoerige(1, patient.angehoerige[0])
    v.zuweiser(1, patient.zuweiser[0])
    return v.zuordnung


def test_vollname(patient: Patient) -> None:
    z = _zuordnung(patient)
    geb = patient.geburtsdatum
    text = (f"Patient A, [geb. {geb.year}], aus [Wohnort], wurde von Dr. Z1 überwiesen. "
            f"Die Anamnese des Patienten A ergab mit Angehörige A1 ... Pat. A ist orientiert.")
    r = rueckersetzen(text, z)
    assert patient.vollname in r.text
    assert geb.strftime("%d.%m.%Y") in r.text
    assert patient.angehoerige[0].vollname in r.text
    assert "Dr. med. " + patient.zuweiser[0].vollname in r.text
    assert "Patient A" not in r.text and "Pat. A" not in r.text
    assert r.verbleibend == []
    assert r.ersetzt == 7


def test_anrede_nach_kasus(patient: Patient) -> None:
    z = _zuordnung(patient)
    info = {"nachname": patient.nachname, "vorname": patient.vorname, "geschlecht": "m"}
    r = rueckersetzen("Patient A kam. Wir sahen Patienten A erneut.", z, info, form="anrede")
    assert r.text == f"Herr {patient.nachname} kam. Wir sahen Herrn {patient.nachname} erneut."


def test_anrede_weiblich(patientin: Patient) -> None:
    z = _zuordnung(patientin)
    info = {"nachname": patientin.nachname, "geschlecht": "w"}
    r = rueckersetzen("Patientin A berichtet.", z, info, form="anrede")
    assert r.text == f"Frau {patientin.nachname} berichtet."


def test_verbleibende_pseudonyme(patient: Patient) -> None:
    r = rueckersetzen("Patient A und Patient C sowie [geb. 1888] und Dr. Z9.", _zuordnung(patient))
    assert "Patient C" in r.verbleibend
    assert "[geb. 1888]" in r.verbleibend
    assert "Dr. Z9" in r.verbleibend
    assert "Patient A" not in r.verbleibend
