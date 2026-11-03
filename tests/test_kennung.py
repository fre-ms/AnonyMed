"""Phonetic codes, keyed-hash identifiers and their check character."""

from __future__ import annotations

import dataclasses
import datetime as dt

import pytest

from pseudonymed.kern.modell import Patient
from pseudonymed.pseudonyme.kennung import (
    ALPHABET,
    hash_kennung,
    hauptteil,
    kanonische_identitaet,
    kanonische_strasse,
    kanonischer_ort,
    kennung_gueltig,
    pruefzeichen,
    trenne_hausnummer,
)
from pseudonymed.pseudonyme.phonetik import koelner_phonetik

SCHLUESSEL = bytes(range(32))


@pytest.mark.parametrize(
    ("wort", "code"),
    [("Wikipedia", "3412"), ("Breschnew", "17863"), ("Müller-Lüdenscheidt", "65752682"),
     ("Müller", "657"), ("Mueller", "657"), ("Miller", "657"), ("Meier", "67"), ("Mayer", "67"),
     ("Meyer", "67"), ("Schmidt", "862"), ("Schmitt", "862"), ("Christian", "47826"),
     ("Kristian", "47826"), ("", "")],
)
def test_koelner_phonetik(wort: str, code: str) -> None:
    assert koelner_phonetik(wort) == code


def test_pruefzeichen() -> None:
    code = "K7Q4M"
    voll = code + pruefzeichen(code)
    assert kennung_gueltig(voll)
    for i in range(len(voll)):
        for c in ALPHABET:
            if c != voll[i]:
                assert not kennung_gueltig(voll[:i] + c + voll[i + 1 :])  # every single error
    assert not kennung_gueltig("K7Q4M0")  # 0 is not in the alphabet
    assert not kennung_gueltig("")


def test_hash_reproduzierbar_und_schluesselabhaengig() -> None:
    a = hash_kennung(SCHLUESSEL, "657|47|1959-07-21")
    assert len(a) == 6 and kennung_gueltig(a) and set(a) <= set(ALPHABET)
    assert a == hash_kennung(SCHLUESSEL, "657|47|1959-07-21")
    assert a != hash_kennung(bytes(32), "657|47|1959-07-21")
    assert a != hash_kennung(SCHLUESSEL, "657|47|1959-07-22")
    assert a != hash_kennung(SCHLUESSEL, "657|47|1959-07-21", variante=1)
    assert len(hash_kennung(SCHLUESSEL, "x", laenge=8)) == 9


def test_kanonische_identitaet_toleriert_schreibweisen() -> None:
    basis = Patient(nachname="Müller", vorname="Hans Peter", geburtsdatum=dt.date(1960, 2, 1))
    k = kanonische_identitaet(basis)
    assert k == "657|068|1960-02-01"
    for variante in (
        dataclasses.replace(basis, nachname="Mueller"),
        dataclasses.replace(basis, nachname="MÜLLER", vorname="Hans-Peter"),
        dataclasses.replace(basis, nachname="Miller", vorname="Hanns"),
        dataclasses.replace(basis, nachname="von Müller"),
    ):
        assert kanonische_identitaet(variante) == k
    assert kanonische_identitaet(dataclasses.replace(basis, geburtsdatum=dt.date(1961, 2, 1))) != k
    assert kanonische_identitaet(dataclasses.replace(basis, vorname="Peter Hans")) != k
    ohne = dataclasses.replace(basis, geburtsdatum=None)
    assert kanonische_identitaet(ohne) == "muller|hans|"
    assert koelner_phonetik("Hans") == koelner_phonetik("Hanns") == "068"


def test_hauptteil() -> None:
    assert hauptteil("Müller-Lüdenscheidt") == "Lüdenscheidt"
    assert hauptteil("von der Leyen") == "Leyen"
    assert hauptteil("de") == "de"
    assert hauptteil("") == ""


def test_kanonische_strasse() -> None:
    k = kanonische_strasse("Musterstraße 12")
    assert k == "strasse:6827:str:12"
    for variante in ("Musterstr. 12", "Muster-Strasse 12", "MUSTERSTRASSE 12", "Musterstraße, 12",
                     "Musterstraße 12 "):
        assert kanonische_strasse(variante) == k, variante
    assert kanonische_strasse("Musterstraße 12a") == "strasse:6827:str:12a"
    assert kanonische_strasse("Musterstraße 14") != k
    assert kanonische_strasse("Musterweg 12") != k
    assert kanonische_strasse("Lindenweg 5") == kanonische_strasse("Linden-Weg 5")
    assert kanonische_strasse("Am Alten Markt 3") == kanonische_strasse("Am alten Markt 3")
    assert kanonische_strasse("Straße des Friedens 1").startswith("strasse:8278")
    assert trenne_hausnummer("Hauptstraße 12 a") == ("Hauptstraße", "12a")
    assert trenne_hausnummer("Hauptstraße") == ("Hauptstraße", "")


def test_kanonischer_ort() -> None:
    assert kanonischer_ort("München") == kanonischer_ort("Muenchen") == kanonischer_ort("MÜNCHEN")
    assert kanonischer_ort("Frankfurt am Main") == kanonischer_ort("Frankfurt a. Main")
    assert kanonischer_ort("Frankfurt am Main") != kanonischer_ort("Frankfurt (Oder)")
    assert kanonischer_ort("Bad Tölz") == kanonischer_ort("Tölz")
    assert kanonischer_ort("Riesa") != kanonischer_ort("Dresden")
