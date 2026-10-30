"""Pattern builders: what must match and, just as important, what must not."""

from __future__ import annotations

import datetime as dt
import re

import pytest

from anonymed.erkennung import varianten as v


def _passt(muster: str | None, text: str) -> bool:
    assert muster is not None
    return re.search(muster, text) is not None


@pytest.mark.parametrize(
    "text", ["Müller", "MÜLLER", "Mueller", "MUELLER", "Muller", "Müllers", "Herr Müller,"]
)
def test_name_varianten(text: str) -> None:
    assert _passt(v.name_muster("Müller"), text)


@pytest.mark.parametrize("text", ["müller", "Müllerstraße", "Schmüller", "Müllerin"])
def test_name_keine_fehltreffer(text: str) -> None:
    assert not _passt(v.name_muster("Müller"), text)


def test_name_zu_kurz() -> None:
    assert v.name_muster("M") is None


@pytest.mark.parametrize(
    "text",
    [
        "Max Mustermann",
        "Mustermann, Max",
        "Mustermann Max",
        "Max Peter Mustermann",
        "Max-Peter Mustermann",
        "M. Mustermann",
        "MAX MUSTERMANN",
        "Max Mustermanns Befund",
        "Max\nMustermann",
    ],
)
def test_vollname(text: str) -> None:
    assert _passt(v.vollname_muster(["Max", "Peter"], "Mustermann"), text)


def test_vollname_nicht_teilwort() -> None:
    assert not _passt(v.vollname_muster(["Max"], "Mustermann"), "Maxi Mustermanninger")


def test_titel_name_fuer_zuweiser() -> None:
    muster = v.titel_name_muster(["Anna"], "Schmidt")
    m = re.search(muster, "Überweisung durch Dr. med. Anna Schmidt vom")
    assert m and m.group(0) == "Dr. med. Anna Schmidt"
    m = re.search(muster, "Praxis Dr. Schmidt")
    assert m and m.group(0) == "Dr. Schmidt"


def test_nachname_teile() -> None:
    assert v.nachname_teile("Müller-Lüdenscheidt") == ["Müller", "Lüdenscheidt"]
    assert v.nachname_teile("von der Leyen") == ["Leyen"]
    assert v.nachname_teile("Meier") == []


@pytest.mark.parametrize(
    "text",
    [
        "01.02.1990",
        "1.2.1990",
        "01.02.90",
        "01. 02. 1990",
        "1990-02-01",
        "01/02/1990",
        "1. Februar 1990",
        "01. Feb. 1990",
        "1 FEBRUAR 1990",
    ],
)
def test_datum_formate(text: str) -> None:
    assert _passt(v.datum_muster(dt.date(1990, 2, 1)), text)


@pytest.mark.parametrize(
    "text", ["11.02.1990", "21.02.1990", "01.02.1991", "01.03.1990", "1.2.19900"]
)
def test_datum_keine_fehltreffer(text: str) -> None:
    assert not _passt(v.datum_muster(dt.date(1990, 2, 1)), text)


@pytest.mark.parametrize(
    "text",
    ["089 1234567", "089/1234567", "089-123 45 67", "+49 89 1234567", "0049 (0)89 1234567",
     "+49 (0)89 123 45 67", "(089) 1234567"],
)
def test_telefon(text: str) -> None:
    assert _passt(v.telefon_muster("+49 (0)89 1234567"), text)


def test_telefon_andere_nummer() -> None:
    assert not _passt(v.telefon_muster("089 1234567"), "089 1234568")
    assert v.telefon_muster("123") is None


@pytest.mark.parametrize(
    "text",
    ["Musterstraße 12", "Musterstr. 12", "Musterstrasse 12a", "MUSTERSTRASSE 12", "Musterstraße",
     "Muster-Straße 12", "Musterstraße, 12"],
)
def test_adresse(text: str) -> None:
    assert _passt(v.adresse_muster("Musterstraße 12"), text)


def test_adresse_ohne_suffix_und_fehltreffer() -> None:
    assert _passt(v.adresse_muster("Am Alten Markt 3"), "wohnhaft Am Alten Markt 3")
    assert _passt(v.adresse_muster("Lindenweg 5"), "Lindenweg 5b")
    assert not _passt(v.adresse_muster("Musterstraße 12"), "Mustermann 12")


@pytest.mark.parametrize("text", ["München", "Muenchen", "MÜNCHEN", "in München,"])
def test_ort(text: str) -> None:
    assert _passt(v.ort_muster("München"), text)


@pytest.mark.parametrize("text", ["80331 München", "D-80331 München", "D 80331 Muenchen"])
def test_plz_ort(text: str) -> None:
    assert _passt(v.plz_ort_muster("80331", "München"), text)


def test_ort_keine_fehltreffer() -> None:
    assert not _passt(v.ort_muster("München"), "Münchener Kindl")
    assert not _passt(v.plz_ort_muster("80331", "München"), "80333 München")
    assert v.plz_ort_muster("", "München") is None


def test_codes() -> None:
    assert _passt(v.exakt_muster("C815908301"), "Nr. c815908301,")
    assert _passt(v.exakt_muster("AZ 2023/171"), "Zeichen AZ  2023/171")
    assert not _passt(v.exakt_muster("C815908301"), "C8159083012")
    assert _passt(v.nummer_muster("68202938"), "ID 68202938.")
    assert not _passt(v.nummer_muster("68202938"), "168202938")
    assert v.nummer_muster("12") is None


def test_email() -> None:
    assert _passt(v.email_muster("Max.Mustermann@example.org"), "an max.mustermann@example.org!")
    assert not _passt(v.email_muster("max@example.org"), "a.max@example.org")


def test_begriff() -> None:
    assert _passt(v.begriff_muster("Praxis Beispiel"), "in der PRAXIS  Beispiel")
    assert not _passt(v.begriff_muster("Beispiel"), "Beispiele")
