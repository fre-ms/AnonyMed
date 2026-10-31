"""The desktop interface, driven offscreen: session model and the five pages in sequence."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from anonymed.gui.hauptfenster import Hauptfenster  # noqa: E402
from anonymed.gui.patient_seite import _personen  # noqa: E402
from anonymed.gui.sitzung import Sitzung  # noqa: E402
from anonymed.kern.modell import Patient  # noqa: E402
from tests.test_ablauf import gefundene_klardaten  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_personen_aus_text() -> None:
    p = _personen("Dr. med. Anna Schmidt\nMüller, Hans\n\nProf. Dr. Berg, Eva")
    assert [(x.titel, x.vorname, x.nachname) for x in p] == [
        ("Dr. med.", "Anna", "Schmidt"), ("", "Hans", "Müller"), ("Prof. Dr.", "Eva", "Berg")]


def test_sitzung_ohne_oberflaeche(tmp_path: Path, befund: Path, patient: Patient) -> None:
    s = Sitzung(speicherpfad=tmp_path / "s.sqlite")
    s.patient = patient
    assert s.fuege_hinzu(befund) is not None
    assert s.fuege_hinzu(befund) is None  # no duplicates
    assert s.fuege_hinzu(tmp_path / "notiz.xyz") is None
    d = s.dokumente[0]
    assert d.zustand == "noch nicht analysiert"
    s.analysiere(d)
    assert d.vorbereitung is not None and "Treffer" in d.zustand
    erg = s.exportiere(d)
    assert erg.freigegeben and d.zustand == "freigegeben"
    s.schliessen()


def test_fenster_rundlauf(qapp: QApplication, tmp_path: Path, befund: Path,
                          patient: Patient) -> None:
    sitzung = Sitzung(speicherpfad=tmp_path / "s.sqlite")
    fenster = Hauptfenster(sitzung)
    fenster.dokumente.synchron = True
    fenster.export.synchron = True

    # 1 patient
    fenster.patient.fuelle(patient)
    assert fenster.patient.f["nachname"].text() == patient.nachname
    fenster.patient.uebernehmen()
    assert sitzung.patient is not None and sitzung.patient.vollname == patient.vollname
    assert sitzung.patient.zuweiser[0].nachname == patient.zuweiser[0].nachname
    assert fenster.tabs.currentWidget() is fenster.dokumente

    # 2 documents
    fenster.dokumente.hinzufuegen([befund])
    assert fenster.dokumente.liste.count() == 1
    fenster.dokumente.analysieren()
    qapp.processEvents()
    d = sitzung.dokumente[0]
    assert d.vorbereitung is not None and d.vorbereitung.fundstellen
    assert fenster.tabs.currentWidget() is fenster.pruefen

    # 3 review: drop one hit, draw one box; medical terms are marked by default
    pr = fenster.pruefen
    assert pr.baum.topLevelItemCount() == 1 and pr.dokument is d
    assert fenster.dokumente.fachbegriffe.isChecked()
    assert d.vorbereitung.fachbegriffe_anzahl >= 3  # Hypertonie, Ramipril, Atorvastatin ...
    assert d.vorbereitung.fachbegriffe_rects(0)
    assert pr.fach_anzeigen.isChecked() and "Fachbegriffe markiert" in pr.hinweis.text()
    pr.fach_anzeigen.setChecked(False)
    pr.fach_anzeigen.setChecked(True)
    from anonymed.erkennung import ner as nermodul

    assert fenster.dokumente.ner.isChecked() == nermodul.verfuegbar()
    vorher = len(pr.fundstellen())
    assert pr.tabelle.rowCount() == vorher > 5
    ziel = next(f for f in pr.fundstellen() if f.regel == "zuweiser1.name")
    pr.waehle(ziel)
    pr.verwerfe()
    assert len(pr.fundstellen()) == vorher - 1
    pr.kasten((56, 300, 400, 320), "[Kasten]")
    assert any(f.quelle == "manuell" for f in pr.fundstellen())
    assert pr.tabelle.rowCount() == len(pr.fundstellen())

    # 4 export
    fenster.export.ziel.setText(str(tmp_path / "aus"))
    fenster.export.exportieren()
    qapp.processEvents()
    text = fenster.export.ausgabe.toPlainText()
    assert "Gegenprobe: bestanden" in text, text
    assert (tmp_path / "aus" / "befund_anonym.pdf").exists()
    sidecar = (tmp_path / "aus" / "befund_anonym.txt").read_text(encoding="utf-8")
    assert "[Kasten]" in sidecar
    assert patient.nachname not in sidecar
    assert patient.zuweiser[0].nachname in sidecar  # deliberately kept

    # 5 re-identify
    fenster.tabs.setCurrentWidget(fenster.rueckersetzen)
    rs = fenster.rueckersetzen
    assert rs.person.count() == 1
    k = d.ergebnis.kennung
    rs.eingabe.setPlainText(f"Patient {k} wurde untersucht; Angehörige {k}-1 war dabei.")
    rs.rueckersetzen()
    aus = rs.ausgabe.toPlainText()
    assert patient.vollname in aus and patient.angehoerige[0].vollname in aus
    assert "nichts offen" in rs.hinweis.text()
    assert gefundene_klardaten(text, patient) == []  # the report shows no clear data
    fenster.close()


def test_fenster_textdatei(qapp: QApplication, tmp_path: Path, patient: Patient) -> None:
    """A transcript goes through the same five steps; the review shows text, not a page."""
    from PySide6.QtGui import QTextCursor

    from tests.synthetisch import erzeuge_transkript, verschrieben

    quelle = erzeuge_transkript(tmp_path / "gespraech.txt", patient)
    sitzung = Sitzung(speicherpfad=tmp_path / "s.sqlite")
    fenster = Hauptfenster(sitzung)
    fenster.dokumente.synchron = True
    fenster.export.synchron = True
    fenster.pruefen.synchron = True
    fenster.patient.fuelle(patient)
    fenster.patient.uebernehmen()
    fenster.dokumente.hinzufuegen([quelle, tmp_path / "egal.xyz"])
    assert fenster.dokumente.liste.count() == 1
    assert fenster.dokumente.unscharf.isChecked()
    fenster.dokumente.analysieren()
    qapp.processEvents()
    d = sitzung.dokumente[0]
    assert d.vorbereitung is not None and d.vorbereitung.ist_text
    assert "Text, " in fenster.dokumente.liste.item(0).text()

    pr = fenster.pruefen
    assert pr.stapel.currentWidget() is pr.textansicht and not pr.markieren.isHidden()
    assert pr.baum.topLevelItem(0).child(0).text(0) == "Text"
    assert pr.textansicht.toPlainText() == d.vorbereitung.text
    assert pr.tabelle.rowCount() == len(pr.fundstellen()) > 5
    assert any(f.quelle == "unscharf" for f in pr.fundstellen())
    assert "Unscharfe Suche" in pr.hinweis.text()
    unscharf = next(f for f in pr.fundstellen() if f.quelle == "unscharf")
    pr.waehle(unscharf)
    assert pr.textansicht.textCursor().position() == unscharf.start
    pr.verwerfe()
    assert unscharf not in pr.fundstellen()
    with pytest.raises(ValueError):
        pr.markiere_auswahl("[X]")  # nothing selected
    text = d.vorbereitung.text
    a = text.index("Wörter vergesse")
    cursor = pr.textansicht.textCursor()
    cursor.setPosition(a)
    cursor.setPosition(a + len("Wörter vergesse"), QTextCursor.MoveMode.KeepAnchor)
    pr.textansicht.setTextCursor(cursor)
    pr.markiere_auswahl("[Symptom]")
    assert any(f.quelle == "manuell" and f.pseudonym == "[Symptom]" for f in pr.fundstellen())
    pr.fach_anzeigen.setChecked(False)
    pr.fach_anzeigen.setChecked(True)

    fenster.export.ziel.setText(str(tmp_path / "aus"))
    fenster.export.exportieren()
    qapp.processEvents()
    protokoll = fenster.export.ausgabe.toPlainText()
    assert "Gegenprobe: bestanden" in protokoll and "Freigegeben" in protokoll, protokoll
    ziel = tmp_path / "aus" / "gespraech_anonym.txt"
    aus = ziel.read_text(encoding="utf-8")
    assert "[Symptom]" in aus and verschrieben(patient.nachname) in aus  # discarded = kept
    assert patient.nachname not in aus and gefundene_klardaten(protokoll, patient) == []
    fenster.close()


def test_selbsttest(qapp: QApplication, capsys: pytest.CaptureFixture[str]) -> None:
    from anonymed.gui.app import main

    assert main(["--selbsttest"]) == 0
    assert "Selbsttest bestanden" in capsys.readouterr().out


def test_englische_oberflaeche(qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    from anonymed.gui import uebersetzung

    assert uebersetzung.sprache_bestimmen(["--sprache", "en"]) == "en"
    assert uebersetzung.sprache_bestimmen(["--language=EN-GB"]) == "en-gb"
    assert uebersetzung.ohne_sprachargumente(["x", "--sprache", "en", "y"]) == ["x", "y"]
    monkeypatch.setenv("ANONYMED_SPRACHE", "en")
    assert uebersetzung.sprache_bestimmen([]) == "en"
    uebersetzer = uebersetzung.installiere(qapp, "en_gb")
    assert uebersetzer is not None
    try:
        fenster = Hauptfenster(Sitzung(speicherpfad=None))
        tabs = [fenster.tabs.tabText(i) for i in range(fenster.tabs.count())]
        assert tabs == ["1 Patient", "2 Documents", "3 Review", "4 Export", "5 Re-identify"]
        assert fenster.patient.f["nachname"].placeholderText() == ""
        assert fenster.export.knopf.text() == "Export"
        fenster.close()
    finally:
        qapp.removeTranslator(uebersetzer)
    fenster = Hauptfenster(Sitzung(speicherpfad=None))
    assert fenster.tabs.tabText(1) == "2 Dokumente"
    fenster.close()
