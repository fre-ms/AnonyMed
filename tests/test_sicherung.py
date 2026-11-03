"""Backup and restore of database and key; statistics per category."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication
from typer.testing import CliRunner

from anonymed.ablauf import Ablaufoptionen, anonymisiere
from anonymed.cli import app
from anonymed.kern.modell import Patient
from anonymed.pseudonyme import pgp as pgpmodul
from anonymed.pseudonyme import sicherung as sichmodul
from anonymed.pseudonyme.speicher import Pseudonymspeicher
from tests.synthetisch import synthetischer_patient
from tests.test_gui import qapp  # noqa: F401


def _gefuellt(ordner: Path, befund: Path, patient: Patient) -> Path:
    """A database with one anonymised report; returns the database path."""
    db = ordner / "pseudonyme.sqlite"
    with Pseudonymspeicher(db) as sp:
        erg = anonymisiere(befund, patient, Ablaufoptionen(bericht=False, textdatei=False,
                                                           ziel_ordner=ordner / "aus"),
                           speicher=sp)
        assert erg.freigegeben
    return db


def test_statistik_und_kategorien(tmp_path: Path, befund: Path, patient: Patient,
                                  monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANONYMED_DATENORDNER", str(tmp_path / "daten"))
    leer = sichmodul.statistik(tmp_path / "nix.sqlite")
    assert not leer.existiert and leer.patienten == 0 and "nein" in dict(leer.zeilen())["Vorhanden"]
    db = _gefuellt(tmp_path, befund, patient)
    st = sichmodul.statistik(db)
    assert st.existiert and st.groesse > 0 and st.patienten == 1 and st.zuordnungen >= 8
    assert st.schluessel_vorhanden and len(st.schluessel_kennung) == 16 and st.schema == "hash"
    kategorien = dict(st.je_kategorie)
    assert kategorien["Patient"] == 1 and "Geburtsdatum" in kategorien and "Straße" in kategorien
    assert "Zuweiser" in kategorien and st.integritaet == "ok" and st.letzter_eintrag
    zeilen = dict(st.zeilen())
    assert zeilen["Patienten"] == "1" and "    Patient" in zeilen
    assert sichmodul.kategorie("[Code]", "manuell") == "Barcode/QR"
    assert sichmodul.kategorie("irgendwas", "email") == "E-Mail"


def test_export_und_wiederherstellung(tmp_path: Path, befund: Path, patient: Patient,
                                      monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANONYMED_DATENORDNER", str(tmp_path / "daten"))
    (tmp_path / "daten" / "fachbegriffe").mkdir(parents=True)
    (tmp_path / "daten" / "fachbegriffe" / "nutzervokabular.txt").write_text("Flantzlegacy\n")
    (tmp_path / "daten" / "konfig.toml").write_text('[gui]\nsprache = "de"\n')
    db = _gefuellt(tmp_path, befund, patient)
    with Pseudonymspeicher(db) as sp:
        kennung = sp.finde()[0]["kennung"]
        zuordnung = sp.zuordnung(patient.schluessel())
    with pytest.raises(sichmodul.SicherungFehler):
        sichmodul.exportiere(tmp_path / "x.zip", tmp_path / "fehlt.sqlite")
    archiv = sichmodul.exportiere(tmp_path / "sicherung", db)  # plain, folder given
    assert archiv.parent == tmp_path / "sicherung" and archiv.suffix == ".zip"
    with zipfile.ZipFile(archiv) as z:
        namen = set(z.namelist())
    assert {"manifest.json", "pseudonyme.sqlite", "pseudonymschluessel.txt",
            "konfig.toml"} <= namen
    assert "fachbegriffe/nutzervokabular.txt" not in namen  # taken over into the table
    m = sichmodul.pruefe(archiv)
    assert m.patienten == 1 and m.zuordnungen >= 8 and m.konfig and m.vokabular == 0
    assert m.schluessel_kennung == sichmodul.statistik(db).schluessel_kennung
    # restore into an empty place: same identifiers, same mapping, same key
    neu = tmp_path / "neu" / "pseudonyme.sqlite"
    monkeypatch.setenv("ANONYMED_DATENORDNER", str(tmp_path / "neu"))
    b = sichmodul.importiere(archiv, neu)
    assert b.modus == "ersetzen" and b.patienten_neu == 1 and b.schluessel_ersetzt
    assert b.konfig_ersetzt and b.vokabular_dateien == 0 and b.sicherung_vorher == []
    with Pseudonymspeicher(neu) as sp2:
        assert sp2.finde()[0]["kennung"] == kennung
        assert sp2.zuordnung(patient.schluessel()) == zuordnung
        assert sp2.kennung_fuer(patient).kennung == kennung  # the key came along
    assert (tmp_path / "neu" / "konfig.toml").exists()
    from anonymed.erkennung import vokabular as vokmodul

    # the old per-file list went into the default database of the original data folder
    with vokmodul.Vokabular(tmp_path / "daten" / "pseudonyme.sqlite", grundbestand=False) as v:
        assert v.enthaelt("Flantzlegacy")
    # restore over an existing database sets the old one aside
    b2 = sichmodul.importiere(archiv, neu)
    assert len(b2.sicherung_vorher) == 2 and all(p.exists() for p in b2.sicherung_vorher)
    assert not b2.schluessel_ersetzt


def test_zusammenfuehren(tmp_path: Path, befund: Path, patient: Patient,
                         monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANONYMED_DATENORDNER", str(tmp_path / "daten"))
    db = _gefuellt(tmp_path / "a", befund, patient)
    archiv_a = sichmodul.exportiere(tmp_path / "a.zip", db)
    # a second database with the same key and another patient
    zweite = tmp_path / "b" / "pseudonyme.sqlite"
    zweite.parent.mkdir()
    (tmp_path / "b" / "pseudonymschluessel.txt").write_bytes(
        (tmp_path / "a" / "pseudonymschluessel.txt").read_bytes())
    andere = synthetischer_patient(11, "w")
    from tests.synthetisch import erzeuge_befund

    befund_b = erzeuge_befund(tmp_path / "b" / "befund.pdf", andere)
    with Pseudonymspeicher(zweite) as sp:
        assert anonymisiere(befund_b, andere, Ablaufoptionen(bericht=False, textdatei=False),
                            speicher=sp).freigegeben
    b = sichmodul.importiere(archiv_a, zweite, modus="zusammenfuehren")
    assert b.patienten_neu == 1 and b.zuordnungen_neu >= 8 and not b.schluessel_ersetzt
    with Pseudonymspeicher(zweite) as sp:
        assert len(sp.finde()) == 2
    b = sichmodul.importiere(archiv_a, zweite, modus="zusammenfuehren")  # idempotent
    assert b.patienten_neu == 0 and b.zuordnungen_neu == 0
    # a different key cannot be merged
    dritte = tmp_path / "c" / "pseudonyme.sqlite"
    dritte.parent.mkdir()
    with Pseudonymspeicher(dritte) as sp:
        assert anonymisiere(befund_b, andere, Ablaufoptionen(bericht=False, textdatei=False),
                            speicher=sp).freigegeben
    with pytest.raises(sichmodul.SicherungFehler, match="gleichem Pseudonymschlüssel"):
        sichmodul.importiere(archiv_a, dritte, modus="zusammenfuehren")
    with pytest.raises(sichmodul.SicherungFehler):
        sichmodul.importiere(archiv_a, dritte, modus="egal")


@pytest.mark.skipif(not pgpmodul.verfuegbar(), reason="weder pysequoia noch GnuPG")
def test_verschluesselte_sicherung(tmp_path: Path, befund: Path, patient: Patient,
                                   monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANONYMED_DATENORDNER", str(tmp_path / "daten"))
    db = _gefuellt(tmp_path, befund, patient)
    archiv = sichmodul.exportiere(tmp_path / "s", db, passphrase="sehr-geheim-1")
    assert archiv.name.endswith(".zip.gpg") and not archiv.read_bytes().startswith(b"PK")
    assert patient.nachname.encode() not in archiv.read_bytes()
    with pytest.raises(sichmodul.SicherungFehler, match="Passphrase"):
        sichmodul.pruefe(archiv)
    with pytest.raises(sichmodul.SicherungFehler):
        sichmodul.pruefe(archiv, "falsch")
    assert sichmodul.pruefe(archiv, "sehr-geheim-1").patienten == 1
    b = sichmodul.importiere(archiv, tmp_path / "wieder" / "p.sqlite", passphrase="sehr-geheim-1")
    assert b.patienten_neu == 1


def test_datenbank_ueber_cli(tmp_path: Path, befund: Path, patient: Patient,
                             monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANONYMED_DATENORDNER", str(tmp_path / "daten"))
    db = _gefuellt(tmp_path, befund, patient)
    runner = CliRunner()
    r = runner.invoke(app, ["datenbank", "status", "--speicher", str(db)])
    assert r.exit_code == 0 and "Patienten" in r.output and "Zuordnungen gesamt" in r.output
    r = runner.invoke(app, ["datenbank", "exportieren", str(tmp_path / "s"), "--speicher",
                            str(db), "--unverschluesselt"])
    assert r.exit_code == 0 and "UNVERSCHLÜSSELT" in r.output, r.output
    archiv = next((tmp_path / "s").glob("*.zip"))
    neu = tmp_path / "n" / "p.sqlite"
    r = runner.invoke(app, ["datenbank", "importieren", str(archiv), "--speicher", str(neu)],
                      input="n\n")
    assert r.exit_code == 1 and not neu.exists()  # declined
    r = runner.invoke(app, ["datenbank", "importieren", str(archiv), "--speicher", str(neu),
                            "--ja"])
    assert r.exit_code == 0 and "Wiederhergestellt: 1 Patienten" in r.output, r.output
    r = runner.invoke(app, ["datenbank", "status", "--speicher", str(neu)])
    assert "Patienten                 1" in r.output.replace("  ", " ") or "1" in r.output
    if pgpmodul.verfuegbar():
        r = runner.invoke(app, ["datenbank", "exportieren", str(tmp_path / "v.zip"), "--speicher",
                                str(db), "--passphrase", "kurz"])
        assert r.exit_code == 1 and "zu kurz" in r.output
        r = runner.invoke(app, ["datenbank", "exportieren", str(tmp_path / "v.zip"), "--speicher",
                                str(db), "--passphrase", "lang-genug-1"])
        assert r.exit_code == 0 and (tmp_path / "v.zip.gpg").exists()


def test_fenster_datenbank(qapp: QApplication, tmp_path: Path, befund: Path,  # noqa: F811
                           patient: Patient, monkeypatch: pytest.MonkeyPatch) -> None:
    from anonymed.gui.hauptfenster import Hauptfenster
    from anonymed.gui.sitzung import Sitzung

    monkeypatch.setenv("ANONYMED_DATENORDNER", str(tmp_path / "daten"))
    db = _gefuellt(tmp_path, befund, patient)
    sitzung = Sitzung(speicherpfad=db)
    fenster = Hauptfenster(sitzung)
    seite = fenster.datenbank
    seite.synchron = True
    fenster.zeige_seite(seite)
    qapp.processEvents()
    assert fenster.tabs.tabText(fenster.tabs.currentIndex()) == "11 Datenbank"
    st = seite.aktualisiere()
    assert st.patienten == 1 and seite.tabelle.rowCount() == len(st.zeilen())
    with pytest.raises(RuntimeError):
        seite.exportieren()  # no target folder
    seite.export_ziel.setText(str(tmp_path / "sicherung"))
    seite.unverschluesselt.setChecked(True)
    archiv = seite.exportieren()
    assert archiv is not None and archiv.exists()
    assert "UNVERSCHLÜSSELT" in seite.export_status.text()
    if pgpmodul.verfuegbar():
        seite.unverschluesselt.setChecked(False)
        seite.export_passphrase.setText("passphrase-1")
        seite.export_passphrase2.setText("anders")
        with pytest.raises(RuntimeError):
            seite.exportieren()
        seite.export_passphrase.setText("passphrase-1")
        seite.export_passphrase2.setText("passphrase-1")
        verschluesselt = seite.exportieren()
        assert verschluesselt is not None and verschluesselt.name.endswith(".gpg")
    seite.import_pfad.setText(str(archiv))
    m = seite.archiv_pruefen()
    assert m is not None and "Gleicher Schlüssel" in seite.archiv_info.text()
    assert seite.import_knopf.isEnabled() and seite.modus_zusammen.isEnabled()
    seite.modus_zusammen.setChecked(True)
    b = seite.importieren()
    assert b is not None and b.modus == "zusammenfuehren" and b.patienten_neu == 0
    seite.modus_ersetzen.setChecked(True)
    b = seite.importieren()
    assert b is not None and b.modus == "ersetzen" and len(b.sicherung_vorher) == 2
    assert "Wiederhergestellt" in seite.import_status.text()
    fenster.close()
