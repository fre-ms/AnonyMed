"""Shared fixtures.  All data is synthetic (Faker de_DE, fixed seeds)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from pseudonymed.kern.modell import Patient
from pseudonymed.pseudonyme.speicher import Pseudonymspeicher
from tests.carddav_fake import FakeCardDAV
from tests.synthetisch import erzeuge_befund, synthetischer_patient, vcard_aus_patient


@pytest.fixture(autouse=True, scope="session")
def datenordner_isoliert(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """No test touches the user's real data folder: database, key, vocabulary and
    configuration go to a folder of the session (tests may override per test)."""
    import os

    ordner = tmp_path_factory.mktemp("pseudonymed-daten")
    alt = os.environ.get("PSEUDONYMED_DATENORDNER")
    alt_lexikon = os.environ.get("PSEUDONYMED_LEXIKON")
    os.environ["PSEUDONYMED_DATENORDNER"] = str(ordner)
    os.environ["PSEUDONYMED_LEXIKON"] = "0"  # the lexicon tests switch it on for themselves
    try:
        yield ordner
    finally:
        for name, wert in (("PSEUDONYMED_DATENORDNER", alt), ("PSEUDONYMED_LEXIKON", alt_lexikon)):
            if wert is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = wert


@pytest.fixture
def patient() -> Patient:
    return synthetischer_patient(7, "m")


@pytest.fixture
def patientin() -> Patient:
    return synthetischer_patient(11, "w")


@pytest.fixture
def befund(tmp_path: Path, patient: Patient) -> Path:
    return erzeuge_befund(tmp_path / "befund.pdf", patient)


TESTSCHLUESSEL = bytes(range(32))


@pytest.fixture
def speicher() -> Iterator[Pseudonymspeicher]:
    with Pseudonymspeicher(":memory:", geheimnis=TESTSCHLUESSEL) as s:
        yield s


@pytest.fixture
def buchstabenspeicher() -> Iterator[Pseudonymspeicher]:
    with Pseudonymspeicher(":memory:", schema="buchstabe") as s:
        yield s


@pytest.fixture
def fake_carddav(patient: Patient, patientin: Patient) -> Iterator[FakeCardDAV]:
    """Two address books: an empty 'contacts' and 'medifed-patients' with two synthetic cards."""
    server = FakeCardDAV(
        buecher={
            "contacts": ("Kontakte", []),
            "medifed-patients": ("Patienten", [vcard_aus_patient(patient, "uid-a"),
                                               vcard_aus_patient(patientin, "uid-b")]),
        }
    ).start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def carddav_umgebung(fake_carddav: FakeCardDAV, monkeypatch: pytest.MonkeyPatch) -> FakeCardDAV:
    """Point the CLI at the fake server through environment variables."""
    monkeypatch.setenv("PSEUDONYMED_CARDDAV_URL", fake_carddav.url)
    monkeypatch.setenv("PSEUDONYMED_CARDDAV_BENUTZER", fake_carddav.benutzer)
    monkeypatch.setenv("PSEUDONYMED_CARDDAV_ADRESSBUCH", "medifed-patients")
    monkeypatch.setenv("PSEUDONYMED_CARDDAV_PASSWORT", fake_carddav.passwort)
    return fake_carddav
