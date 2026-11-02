"""Address books (local vCards and CardDAV together), storage paths and the patient step."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtWidgets import QApplication
from typer.testing import CliRunner

from anonymed import konfig as konfigmodul
from anonymed.cli import app
from anonymed.kern.modell import Patient
from anonymed.pseudonyme import sicherung as sichmodul
from anonymed.pseudonyme.speicher import (
    Pseudonymspeicher,
    standard_schluesselpfad,
    standardpfad,
)
from anonymed.quellen import adressbuch
from tests.carddav_fake import FakeCardDAV
from tests.synthetisch import synthetischer_patient, vcard_aus_patient
from tests.test_gui import qapp  # noqa: F401


@pytest.fixture
def datenordner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    ordner = tmp_path / "daten"
    monkeypatch.setenv("ANONYMED_DATENORDNER", str(ordner))
    monkeypatch.delenv("ANONYMED_CARDDAV_URL", raising=False)
    monkeypatch.delenv("ANONYMED_CARDDAV_PASSWORT", raising=False)
    return ordner


def _vcf(pfad: Path, *patienten: Patient) -> Path:
    pfad.write_text("".join(vcard_aus_patient(p, f"uid-{i}") for i, p in enumerate(patienten)),
                    encoding="utf-8")
    return pfad


def test_lokales_adressbuch(datenordner: Path, tmp_path: Path, patient: Patient,
                            patientin: Patient) -> None:
    datei = _vcf(tmp_path / "kontakte.vcf", patient, patientin)
    assert adressbuch.suche_lokal(datei, patient.nachname[:4]) and \
        adressbuch.suche_lokal(datei, "")[1].nachname == patientin.nachname
    ordner = tmp_path / "buch"
    ordner.mkdir()
    _vcf(ordner / "a.vcf", patient)
    _vcf(ordner / "b.vcf", patientin)
    (ordner / "notiz.txt").write_text("kein vcf")
    assert len(adressbuch.lokale_dateien(ordner)) == 2
    assert len(adressbuch.suche_lokal(ordner, str(patientin.geburtsdatum.year))) >= 1
    assert not adressbuch.eingerichtet()
    e = konfigmodul.lade_einstellungen()
    e.adressbuch_lokal = str(ordner)
    konfigmodul.speichere_einstellungen(e)
    assert adressbuch.eingerichtet()
    ergebnis = adressbuch.suche(patient.nachname)
    assert [p.nachname for p in ergebnis.patienten] == [patient.nachname]
    assert ergebnis.quellen and not ergebnis.fehler


def test_beide_quellen_und_passwort_in_datei(datenordner: Path, tmp_path: Path, patient: Patient,
                                            patientin: Patient,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    """Local book and CardDAV answer together; without a keyring the password may live in
    the configuration file."""
    server = FakeCardDAV(buecher={"patienten": ("Patienten", [vcard_aus_patient(patientin,
                                                                                  "u1")])}).start()
    try:
        e = konfigmodul.lade_einstellungen()
        e.adressbuch_lokal = str(_vcf(tmp_path / "lokal.vcf", patient))
        konfigmodul.speichere_einstellungen(e)
        k = konfigmodul.CardDAVKonfig(url=server.url, benutzer=server.benutzer,
                                      adressbuch="patienten", passwort_datei=server.passwort)
        monkeypatch.setattr(konfigmodul, "passwort_speichern", lambda *_a, **_k: False)
        konfigmodul.speichere_konfig(k)
        geladen = konfigmodul.lade_konfig()
        assert geladen.passwort_datei == server.passwort
        assert konfigmodul.passwort_laden(geladen) == server.passwort
        ergebnis = adressbuch.suche("")  # everything from both
        namen = {p.nachname for p in ergebnis.patienten}
        assert {patient.nachname, patientin.nachname} <= namen and not ergebnis.fehler
        assert len(ergebnis.quellen) == 2
        # a wrong password fails only the CardDAV source
        k.passwort_datei = "falsch"
        konfigmodul.speichere_konfig(k)
        ergebnis = adressbuch.suche(patient.nachname)
        assert ergebnis.patienten and len(ergebnis.fehler) == 1 and "CardDAV" in ergebnis.fehler[0]
        # CLI: contacts come from both books
        runner = CliRunner()
        k.passwort_datei = server.passwort
        konfigmodul.speichere_konfig(k)
        r = runner.invoke(app, ["kontakte", "suchen", patientin.nachname[:4]])
        assert r.exit_code == 0 and patientin.nachname in r.output, r.output
        r = runner.invoke(app, ["kontakte", "lokal"])
        assert r.exit_code == 0 and "lokal.vcf" in r.output
        r = runner.invoke(app, ["kontakte", "lokal", "--entfernen"])
        assert r.exit_code == 0 and not konfigmodul.lade_einstellungen().adressbuch_lokal
        r = runner.invoke(app, ["kontakte", "lokal", str(tmp_path / "fehlt.vcf")])
        assert r.exit_code == 1
    finally:
        server.stop()


def test_speicherort_konfigurierbar(datenordner: Path, tmp_path: Path, befund: Path,
                                    patient: Patient) -> None:
    from anonymed.ablauf import Ablaufoptionen, anonymisiere

    assert standardpfad() == datenordner / "pseudonyme.sqlite"
    with Pseudonymspeicher() as sp:
        erg = anonymisiere(befund, patient, Ablaufoptionen(bericht=False, textdatei=False,
                                                           ziel_ordner=tmp_path / "aus"),
                           speicher=sp)
        kennung = erg.kennung
    assert (datenordner / "pseudonymschluessel.txt").exists()
    neu_db, neu_key = tmp_path / "tresor" / "anonymed.sqlite", tmp_path / "keys" / "k.txt"
    with pytest.raises(sichmodul.SicherungFehler):
        sichmodul.verlege(Path("relativ.sqlite"), neu_key)
    v = sichmodul.verlege(neu_db, neu_key)
    assert v.kopiert and len(v.umbenannt) == 2 and all(p.exists() for p in v.umbenannt)
    assert standardpfad() == neu_db and standard_schluesselpfad() == neu_key
    assert not (datenordner / "pseudonyme.sqlite").exists()
    with Pseudonymspeicher() as sp:  # default store now lives there, same key, same code
        assert sp.pfad == neu_db and sp.schluesselpfad == neu_key
        assert sp.kennung_fuer(patient).kennung == kennung
    st = sichmodul.statistik()
    assert st.pfad == neu_db and st.schluessel_pfad == neu_key and st.patienten == 1
    # an explicit database path keeps its key next to it (tests, --speicher)
    with Pseudonymspeicher(tmp_path / "anders" / "x.sqlite") as sp:
        assert sp.schluesselpfad == tmp_path / "anders" / "pseudonymschluessel.txt"
    # a second move with another database already at the target is refused
    fremd = tmp_path / "fremd" / "p.sqlite"
    with Pseudonymspeicher(fremd) as sp:
        sp.kennung_fuer(synthetischer_patient(3, "w"))
    with pytest.raises(sichmodul.SicherungFehler, match="schon eine Datenbank"):
        sichmodul.verlege(fremd, neu_key)
    runner = CliRunner()
    r = runner.invoke(app, ["datenbank", "speicherort"])
    assert r.exit_code == 0 and str(neu_db) in r.output and str(neu_key) in r.output


def test_patient_seite(qapp: QApplication, datenordner: Path, tmp_path: Path,  # noqa: F811
                       patient: Patient, patientin: Patient) -> None:
    from anonymed.gui.hauptfenster import Hauptfenster
    from anonymed.gui.patient_seite import Listenfeld, zeilen_aus_datei
    from anonymed.gui.sitzung import Sitzung

    fenster = Hauptfenster(Sitzung(speicherpfad=tmp_path / "s.sqlite"))
    seite = fenster.patient
    seite.synchron = True
    assert not seite.treffer_gruppe.isVisibleTo(fenster)  # nothing to show yet
    seite.suche.setText(patient.nachname)
    assert seite.suchen() is None and "Kein Adressbuch" in seite.hinweis.text()
    e = konfigmodul.lade_einstellungen()
    e.adressbuch_lokal = str(_vcf(tmp_path / "k.vcf", patient, patientin))
    konfigmodul.speichere_einstellungen(e)
    ergebnis = seite.suchen()
    assert ergebnis is not None and len(ergebnis.patienten) == 1
    assert seite.treffer_gruppe.isVisibleTo(fenster) and seite.liste.count() == 1
    assert seite.f["nachname"].text() == patient.nachname  # a single hit fills the form
    seite.suche.setText("gibtesnicht")
    seite.suchen()
    assert not seite.treffer_gruppe.isVisibleTo(fenster) and "Kein Treffer" in seite.hinweis.text()
    # a dropped text file adds its lines to a list field
    liste = tmp_path / "zuweiser.txt"
    liste.write_text("# Zuweiser\nDr. med. Anna Beispiel\n\nDr. Max Muster\n", encoding="utf-8")
    assert zeilen_aus_datei(liste) == ["Dr. med. Anna Beispiel", "Dr. Max Muster"]
    feld: Listenfeld = seite.zuweiser
    feld.setPlainText("Dr. Max Muster")
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(liste))])
    assert feld.canInsertFromMimeData(mime)
    feld.insertFromMimeData(mime)
    assert feld.toPlainText().splitlines() == ["Dr. Max Muster", "Dr. med. Anna Beispiel"]
    assert [z.nachname for z in seite.patient().zuweiser] == ["Muster", "Beispiel"]
    fenster.close()


def test_adressbuch_dialog(qapp: QApplication, datenordner: Path, tmp_path: Path,  # noqa: F811
                           patient: Patient, monkeypatch: pytest.MonkeyPatch) -> None:
    from anonymed.gui.adressbuch_dialog import AdressbuchDialog

    server = FakeCardDAV(buecher={"patienten": ("Patienten", [vcard_aus_patient(patient,
                                                                                  "u1")])}).start()
    try:
        monkeypatch.setattr(konfigmodul, "passwort_speichern", lambda *_a, **_k: False)
        import anonymed.gui.adressbuch_dialog as dialogmodul

        monkeypatch.setattr(dialogmodul, "passwort_speichern", lambda *_a, **_k: False)
        dialog = AdressbuchDialog()
        dialog.lokal.setText(str(tmp_path / "fehlt.vcf"))
        assert not dialog.speichern() and "keine vCard" in dialog.status.text()
        dialog.lokal.setText(str(_vcf(tmp_path / "k.vcf", patient)))
        dialog.url.setText(server.url)
        dialog.benutzer.setText(server.benutzer)
        assert dialog.lade_adressbuecher() == [] and "App-Passwort" in dialog.status.text()
        dialog.passwort.setText(server.passwort)
        buecher = dialog.lade_adressbuecher()
        assert "patienten" in buecher and dialog.adressbuch.count() >= 1
        assert dialog.speichern()
        e = konfigmodul.lade_einstellungen()
        k = konfigmodul.lade_konfig()
        assert e.adressbuch_lokal.endswith("k.vcf") and k.url == server.url
        assert k.passwort_datei == server.passwort  # no keyring: kept in the 0600 file
        assert adressbuch.suche(patient.nachname).patienten
    finally:
        server.stop()


def test_datenbank_seite_speicherort(qapp: QApplication, datenordner: Path,  # noqa: F811
                                     tmp_path: Path) -> None:
    from anonymed.gui.hauptfenster import Hauptfenster
    from anonymed.gui.sitzung import Sitzung

    with Pseudonymspeicher() as sp:
        sp.kennung_fuer(synthetischer_patient(5, "m"))
    fenster = Hauptfenster(Sitzung())  # default paths: the location is editable
    seite = fenster.datenbank
    seite.synchron = True
    seite.aktualisiere()
    assert seite.ort_datenbank.text() == str(datenordner / "pseudonyme.sqlite")
    seite.ort_datenbank.setText(str(tmp_path / "woanders" / "db.sqlite"))
    seite.ort_schluessel.setText(str(tmp_path / "woanders" / "schluessel.txt"))
    v = seite.speicherort_setzen()
    assert v is not None and v.kopiert and "Speicherort" in seite.ort_status.text()
    assert standardpfad() == tmp_path / "woanders" / "db.sqlite"
    assert sichmodul.statistik().patienten == 1
    fenster.close()
