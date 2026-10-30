"""The command line drives the same pipeline: anonymise, check, re-identify."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from anonymed.cli import app
from anonymed.kern.modell import Patient

runner = CliRunner()


def _patientendatei(tmp_path: Path, p: Patient) -> Path:
    daten = {
        "nachname": p.nachname, "vorname": p.vorname, "geschlecht": p.geschlecht,
        "geburtsdatum": p.geburtsdatum.isoformat(), "strasse": p.strasse, "plz": p.plz,
        "ort": p.ort, "telefone": p.telefone, "emails": p.emails,
        "versichertennummer": p.versichertennummer, "patienten_id": p.patienten_id,
        "aktenzeichen": p.aktenzeichen,
        "angehoerige": [{"nachname": a.nachname, "vorname": a.vorname} for a in p.angehoerige],
        "zuweiser": [{"nachname": z.nachname, "vorname": z.vorname, "titel": z.titel,
                      "strasse": z.strasse} for z in p.zuweiser],
    }
    pfad = tmp_path / "patient.json"
    pfad.write_text(json.dumps(daten, ensure_ascii=False), encoding="utf-8")
    return pfad


def test_rundlauf_ueber_cli(tmp_path: Path, befund: Path, patient: Patient) -> None:
    pdatei = _patientendatei(tmp_path, patient)
    speicher = tmp_path / "store.sqlite"
    r = runner.invoke(app, ["anonymisieren", str(befund), "--patient", str(pdatei),
                            "--speicher", str(speicher)])
    assert r.exit_code == 0, r.output
    assert "Gegenprobe: bestanden" in r.output
    assert (tmp_path / "befund_anonym.pdf").exists()
    txt = (tmp_path / "befund_anonym.txt").read_text(encoding="utf-8")
    assert patient.nachname not in txt and "Patient A" in txt

    r = runner.invoke(app, ["pruefen", str(tmp_path / "befund_anonym.pdf"), "--patient",
                            str(pdatei), "--speicher", str(speicher)])
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["pruefen", str(befund), "--patient", str(pdatei),
                            "--speicher", str(speicher)])
    assert r.exit_code == 2 and "NICHT bestanden" in r.output

    r = runner.invoke(app, ["pseudonyme", "--speicher", str(speicher)])
    assert r.exit_code == 0 and f"A  {patient.nachname}, {patient.vorname}" in r.output

    brief = tmp_path / "brief.txt"
    jahr = patient.geburtsdatum.year
    brief.write_text(f"Patient A, [geb. {jahr}], wurde von Dr. Z1 vorgestellt.", encoding="utf-8")
    r = runner.invoke(app, ["rueckersetzen", str(brief), "--buchstabe", "A", "--form", "anrede",
                            "--speicher", str(speicher)])
    assert r.exit_code == 0, r.output
    assert f"Herr {patient.nachname}" in r.output
    assert patient.geburtsdatum.strftime("%d.%m.%Y") in r.output
    assert patient.zuweiser[0].nachname in r.output

    r = runner.invoke(app, ["rueckersetzen", "--suche", patient.nachname[:3], "--speicher",
                            str(speicher)], input="Patient A und Patient Q.")
    assert r.exit_code == 3
    assert patient.vollname in r.output and "Patient Q" in r.output


def test_vorlage_und_konfig(tmp_path: Path) -> None:
    r = runner.invoke(app, ["vorlage"])
    assert r.exit_code == 0 and json.loads(r.output)["nachname"] == ""
    r = runner.invoke(app, ["konfig", "--speicher", str(tmp_path / "x.sqlite")])
    assert r.exit_code == 0 and "AnonyMed" in r.output and "x.sqlite" in r.output
    r = runner.invoke(app, ["kontakte"])
    assert r.exit_code == 1


def test_fehlerhafte_eingaben(tmp_path: Path, befund: Path, patient: Patient) -> None:
    pdatei = _patientendatei(tmp_path, patient)
    r = runner.invoke(app, ["anonymisieren", str(befund), "--patient", str(pdatei), "--datum", "x"])
    assert r.exit_code == 1
    leer = tmp_path / "leer.json"
    leer.write_text("{}", encoding="utf-8")
    r = runner.invoke(app, ["anonymisieren", str(befund), "--patient", str(leer)])
    assert r.exit_code == 1
    r = runner.invoke(app, ["rueckersetzen", "--speicher", str(tmp_path / "s.sqlite")], input="x")
    assert r.exit_code == 1
