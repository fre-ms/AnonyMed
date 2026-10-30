"""Shared fixtures.  All data is synthetic (Faker de_DE, fixed seeds)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from anonymed.kern.modell import Patient
from anonymed.pseudonyme.speicher import Pseudonymspeicher
from tests.synthetisch import erzeuge_befund, synthetischer_patient


@pytest.fixture
def patient() -> Patient:
    return synthetischer_patient(7, "m")


@pytest.fixture
def patientin() -> Patient:
    return synthetischer_patient(11, "w")


@pytest.fixture
def befund(tmp_path: Path, patient: Patient) -> Path:
    return erzeuge_befund(tmp_path / "befund.pdf", patient)


@pytest.fixture
def speicher() -> Iterator[Pseudonymspeicher]:
    with Pseudonymspeicher(":memory:") as s:
        yield s
