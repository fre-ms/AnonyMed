"""Shared fixtures.  All data is synthetic (Faker de_DE, fixed seeds)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from anonymed.kern.modell import Patient
from anonymed.pseudonyme.speicher import Pseudonymspeicher
from tests.carddav_fake import FakeCardDAV
from tests.synthetisch import erzeuge_befund, synthetischer_patient, vcard_aus_patient


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
    monkeypatch.setenv("ANONYMED_CARDDAV_URL", fake_carddav.url)
    monkeypatch.setenv("ANONYMED_CARDDAV_BENUTZER", fake_carddav.benutzer)
    monkeypatch.setenv("ANONYMED_CARDDAV_ADRESSBUCH", "medifed-patients")
    monkeypatch.setenv("ANONYMED_CARDDAV_PASSWORT", fake_carddav.passwort)
    return fake_carddav
