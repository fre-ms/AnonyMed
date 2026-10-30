"""CLI with CardDAV: search, show, export, anonymise straight from the address book."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from anonymed.cli import app
from anonymed.kern.modell import Patient
from tests.carddav_fake import FakeCardDAV
from tests.synthetisch import vcard_aus_patient

runner = CliRunner()


def test_adressbuecher_und_suche(carddav_umgebung: FakeCardDAV, patient: Patient,
                                 patientin: Patient, tmp_path: Path) -> None:
    r = runner.invoke(app, ["kontakte", "adressbuecher"])
    assert r.exit_code == 0 and "medifed-patients" in r.output and "Kontakte" in r.output
    speicher = tmp_path / "s.sqlite"
    r = runner.invoke(app, ["kontakte", "suchen", patient.nachname[:3], "--speicher",
                            str(speicher)])
    assert r.exit_code == 0, r.output
    assert f"{patient.nachname}, {patient.vorname}" in r.output
    assert patient.geburtsdatum.strftime("%d.%m.%Y") in r.output
    r = runner.invoke(app, ["kontakte", "suchen", "Zyxwv"])
    assert r.exit_code == 1


def test_zeigen_und_export(carddav_umgebung: FakeCardDAV, patient: Patient, tmp_path: Path) -> None:
    r = runner.invoke(app, ["kontakte", "zeigen", patient.nachname])
    assert r.exit_code == 0, r.output
    assert patient.versichertennummer in r.output and "Herr" in r.output
    r = runner.invoke(app, ["kontakte", "zeigen", patient.nachname, "--json"])
    daten = json.loads(r.output)
    assert daten["nachname"] == patient.nachname
    assert daten["geburtsdatum"] == patient.geburtsdatum.isoformat()
    ziel = tmp_path / "patient.json"
    r = runner.invoke(app, ["kontakte", "export", patient.nachname, "-o", str(ziel)])
    assert r.exit_code == 0 and ziel.exists()
    assert json.loads(ziel.read_text(encoding="utf-8"))["angehoerige"] == []
    r = runner.invoke(app, ["kontakte", "export", patient.nachname, "-o", str(ziel)])
    assert r.exit_code == 1 and "existiert" in r.output


def test_anonymisieren_aus_adressbuch(carddav_umgebung: FakeCardDAV, befund: Path,
                                      patient: Patient, tmp_path: Path) -> None:
    speicher = tmp_path / "s.sqlite"
    r = runner.invoke(app, ["anonymisieren", str(befund), "--kontakt", patient.nachname,
                            "--speicher", str(speicher)])
    assert r.exit_code == 0, r.output
    assert "Gegenprobe: bestanden" in r.output
    txt = (befund.parent / "befund_anonym.txt").read_text(encoding="utf-8")
    assert patient.nachname not in txt and "Patient A" in txt
    # the letter is now visible in the search
    r = runner.invoke(app, ["kontakte", "suchen", patient.nachname, "--speicher", str(speicher)])
    assert "→ Patient A" in r.output
    r = runner.invoke(app, ["pruefen", str(befund.parent / "befund_anonym.pdf"), "--kontakt",
                            patient.nachname, "--speicher", str(speicher)])
    assert r.exit_code == 0, r.output


def test_mehrdeutig_und_nr(carddav_umgebung: FakeCardDAV, befund: Path, patient: Patient,
                           patientin: Patient, tmp_path: Path) -> None:
    r = runner.invoke(app, ["anonymisieren", str(befund), "--kontakt", "Zyxwv"])
    assert r.exit_code == 1 and "Kein Kontakt" in r.output
    vcf = tmp_path / "beide.vcf"
    vcf.write_text(vcard_aus_patient(patient) + vcard_aus_patient(patientin), encoding="utf-8")
    r = runner.invoke(app, ["anonymisieren", str(befund), "--vcf", str(vcf)])
    assert r.exit_code == 1 and "Mehrere Treffer" in r.output and patientin.nachname in r.output
    r = runner.invoke(app, ["anonymisieren", str(befund), "--vcf", str(vcf), "--nr", "1",
                            "--speicher", str(tmp_path / "s.sqlite")])
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["anonymisieren", str(befund), "--vcf", str(vcf), "--kontakt",
                            patientin.nachname, "--speicher", str(tmp_path / "s.sqlite")])
    assert r.exit_code == 0, r.output
    assert "Patientin B" in r.output
    r = runner.invoke(app, ["anonymisieren", str(befund)])
    assert r.exit_code == 1 and "--patient, --vcf oder --kontakt" in r.output


def test_einrichten_und_konfig(fake_carddav: FakeCardDAV, tmp_path: Path,
                               monkeypatch) -> None:
    monkeypatch.delenv("ANONYMED_CARDDAV_URL", raising=False)
    konfig = tmp_path / "konfig.toml"
    r = runner.invoke(app, ["kontakte", "einrichten", "--url", fake_carddav.url, "--benutzer",
                            fake_carddav.benutzer, "--adressbuch", "medifed-patients", "--konfig",
                            str(konfig), "--kein-schluesselbund"],
                      input=fake_carddav.passwort + "\n")
    assert r.exit_code == 0, r.output
    assert konfig.exists() and "medifed-patients" in konfig.read_text(encoding="utf-8")
    assert fake_carddav.passwort not in konfig.read_text(encoding="utf-8")
    r = runner.invoke(app, ["kontakte", "einrichten", "--url", fake_carddav.url, "--benutzer",
                            fake_carddav.benutzer, "--konfig", str(konfig),
                            "--kein-schluesselbund"], input="falsch\n")
    assert r.exit_code == 1 and "Anmeldung abgelehnt" in r.output
    monkeypatch.setenv("ANONYMED_CARDDAV_PASSWORT", fake_carddav.passwort)
    r = runner.invoke(app, ["kontakte", "adressbuecher", "--konfig", str(konfig)])
    assert r.exit_code == 0 and "Patienten" in r.output
    r = runner.invoke(app, ["kontakte", "adressbuecher", "--konfig", str(tmp_path / "fehlt.toml")])
    assert r.exit_code == 1 and "nicht eingerichtet" in r.output
