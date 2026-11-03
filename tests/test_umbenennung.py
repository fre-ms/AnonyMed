"""The renaming from AnonyMed to PseudonyMed: old variables, folders, files and commands work."""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pseudonymed import umgebung
from pseudonymed.cli import app
from pseudonymed.pseudonyme import kapsel as kapselmodul
from pseudonymed.pseudonyme import sicherung as sichmodul
from pseudonymed.pseudonyme.speicher import Pseudonymspeicher


def test_variablen_mit_rueckfall(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PSEUDONYMED_OCR", raising=False)
    monkeypatch.delenv("ANONYMED_OCR", raising=False)
    assert umgebung.variable("OCR", "auto") == "auto"
    monkeypatch.setenv("ANONYMED_OCR", "tesseract")
    assert umgebung.variable("OCR") == "tesseract"
    monkeypatch.setenv("PSEUDONYMED_OCR", "rapidocr")
    assert umgebung.variable("OCR") == "rapidocr"
    assert umgebung.namen("OCR") == ["PSEUDONYMED_OCR", "ANONYMED_OCR"]


@pytest.fixture
def ohne_datenordner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("PSEUDONYMED_DATENORDNER", raising=False)
    monkeypatch.delenv("ANONYMED_DATENORDNER", raising=False)
    plattform = tmp_path / "Application Support"
    monkeypatch.setattr(umgebung, "user_data_path", lambda name, appauthor: plattform / name)
    monkeypatch.setattr(umgebung, "user_config_path", lambda name, appauthor: plattform / name)
    return plattform


def test_alter_datenordner_zieht_um(ohne_datenordner: Path) -> None:
    alt = ohne_datenordner / "AnonyMed"
    alt.mkdir(parents=True)
    (alt / "pseudonyme.sqlite").write_bytes(b"db")
    (alt / "pseudonymschluessel.txt").write_text("schluessel", encoding="utf-8")
    neu = umgebung.datenordner()
    assert neu == ohne_datenordner / "PseudonyMed" and not alt.exists()
    assert (neu / "pseudonyme.sqlite").read_bytes() == b"db"
    assert umgebung.konfigordner() == neu  # macOS: one folder for both, moved once
    alt.mkdir()  # both exist: the new one wins, the old one is left alone
    (alt / "pseudonyme.sqlite").write_bytes(b"alt")
    assert umgebung.datenordner() == neu and (alt / "pseudonyme.sqlite").exists()


def test_ohne_alten_ordner_der_neue(ohne_datenordner: Path) -> None:
    assert umgebung.datenordner() == ohne_datenordner / "PseudonyMed"
    assert not (ohne_datenordner / "PseudonyMed").exists()  # created by whoever writes


def test_datenordner_aus_alter_variable(tmp_path: Path, ohne_datenordner: Path,
                                        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANONYMED_DATENORDNER", str(tmp_path / "portabel"))
    assert umgebung.datenordner() == tmp_path / "portabel"


def test_alte_kapsel_wird_gelesen() -> None:
    k = kapselmodul.Kapsel(kennung="K7Q4MX", patient={"nachname": "Test"},
                           zuordnungen={"Patient K7Q4MX": "Max Test"})
    daten = json.loads(k.als_json())
    daten["anonymed_kapsel"] = daten.pop("pseudonymed_kapsel")
    assert kapselmodul.Kapsel.aus_json(json.dumps(daten).encode()) == k


def test_alte_sicherung_wird_gelesen(tmp_path: Path) -> None:
    db = tmp_path / "daten" / "pseudonyme.sqlite"
    db.parent.mkdir()
    Pseudonymspeicher(db).close()
    archiv = sichmodul.exportiere(tmp_path / "neu.zip", db, konfig=False, vokabular=False)
    alt = tmp_path / "alt.zip"
    with zipfile.ZipFile(archiv) as quelle, zipfile.ZipFile(alt, "w") as ziel:
        for name in quelle.namelist():
            inhalt = quelle.read(name)
            if name == sichmodul.NAME_MANIFEST:
                m = json.loads(inhalt)
                m["anonymed_sicherung"] = m.pop("pseudonymed_sicherung")
                inhalt = json.dumps(m).encode()
            ziel.writestr(name, inhalt)
    assert sichmodul.pruefe(alt).dateien == sichmodul.pruefe(archiv).dateien


def test_schluesselbund_mit_altem_dienst(monkeypatch: pytest.MonkeyPatch) -> None:
    keyring = pytest.importorskip("keyring")
    from pseudonymed import konfig

    monkeypatch.delenv("PSEUDONYMED_CARDDAV_PASSWORT", raising=False)
    monkeypatch.delenv("ANONYMED_CARDDAV_PASSWORT", raising=False)
    eintraege = {("AnonyMed CardDAV", "dr@cloud.example"): "app-passwort"}
    monkeypatch.setattr(keyring, "get_password", lambda dienst, name: eintraege.get((dienst, name)))
    k = konfig.CardDAVKonfig(url="https://cloud.example/remote.php/dav", benutzer="dr")
    assert konfig.passwort_laden(k) == "app-passwort"


def _ohne_farben(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)  # the CI forces coloured help


def test_alter_befehl_bleibt(tmp_path: Path) -> None:
    r = CliRunner().invoke(app, ["anonymisieren", "--help"])
    assert r.exit_code == 0 and "--patient" in _ohne_farben(r.output), r.output
    hilfe = _ohne_farben(CliRunner().invoke(app, ["--help"]).output)
    assert "pseudonymisieren" in hilfe and "anonymisieren " not in hilfe


def test_tests_laufen_im_eigenen_datenordner() -> None:
    """The isolation is in place from collection on (see conftest.pytest_configure)."""
    from platformdirs import user_data_path

    ordner = umgebung.datenordner()
    assert ordner.name.startswith("pseudonymed-daten-")
    assert ordner != user_data_path(umgebung.NAME, appauthor=False)
    assert umgebung.konfigordner() == ordner
