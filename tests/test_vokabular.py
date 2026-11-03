"""The vocabulary as a database table: seeding, editing, CSV, the matcher's cache, the step."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from pseudonymed.erkennung import fachbegriffe as fachmodul
from pseudonymed.erkennung import vokabular as vokmodul
from pseudonymed.pseudonyme import sicherung as sichmodul
from pseudonymed.pseudonyme.speicher import Pseudonymspeicher, standardpfad
from tests.test_gui import qapp  # noqa: F401


@pytest.fixture
def frisch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PSEUDONYMED_DATENORDNER", str(tmp_path / "daten"))
    fachmodul.standard.cache_clear()
    yield tmp_path / "daten"
    fachmodul.standard.cache_clear()


def test_grundbestand_und_legacy(frisch: Path) -> None:
    ordner = frisch / "fachbegriffe"
    ordner.mkdir(parents=True)
    (ordner / "nutzervokabular.txt").write_text("# alt\nFlantzitis\n", encoding="utf-8")
    (ordner / "klinik.txt").write_text("Musterklinikum\n", encoding="utf-8")
    with vokmodul.Vokabular() as v:
        assert v.pfad == standardpfad() and v.anzahl() > 39000
        kategorien = dict(v.kategorien())
        assert kategorien[vokmodul.KATEGORIE_MEDIZIN] > 39000
        # body parts already in the Wikidata list keep that category: only the rest counts here
        assert kategorien[vokmodul.KATEGORIE_KOERPER] > 200 and kategorien["klinik"] == 1
        assert kategorien[vokmodul.KATEGORIE_NUTZER] == 1 and kategorien[vokmodul.KATEGORIE_EPONYME]
        assert v.enthaelt("Flantzitis") and v.enthaelt("Nierenlager") and v.version() >= 1
    assert not (ordner / "nutzervokabular.txt").exists()
    assert (ordner / "nutzervokabular.txt.uebernommen").exists()
    fb = fachmodul.standard()
    assert fb.ist_fachbegriff("Flantzitis") and fb.ist_fachbegriff("Musterklinikum")
    assert any(q == "klinik" for q, _n in fb.quellen)
    # the store schema carries the table as well
    with Pseudonymspeicher() as sp:
        assert sp.verbindung.execute("SELECT COUNT(*) FROM vokabular").fetchone()[0] > 39000


def test_bearbeiten_und_cache(frisch: Path) -> None:
    fb = fachmodul.standard()
    assert fb.ist_fachbegriff("Wolf") and not fb.ist_fachbegriff("Flantzose")
    with vokmodul.Vokabular() as v:
        v1 = v.version()
        assert v.fuege_hinzu(["Flantzose", "  flantzose ", "ab"], "Test") == 1  # case, length
        assert v.entferne(["Wolf", "gibtesnicht"]) == 1
        assert v.benenne_um("Flantzose", "Flantzosis") and not v.benenne_um("Flantzosis", "Wade")
        assert v.setze_kategorie(["Flantzosis"], "Neu") == 1
        assert v.version() > v1
        assert [z[1] for z in v.alle(suche="flantzos")] == ["Neu"]
        assert v.alle(suche="100%") == [] and v.alle(kategorie="Neu")[0][0] == "Flantzosis"
    fb2 = fachmodul.standard()  # rebuilt because the version changed
    assert fb2 is not fb and fb2.ist_fachbegriff("Flantzosis") and not fb2.ist_fachbegriff("Wolf")
    assert fachmodul.standard() is fb2  # unchanged table: cached


def test_csv_rundlauf(frisch: Path, tmp_path: Path) -> None:
    with vokmodul.Vokabular() as v:
        v.fuege_hinzu(["Alpha-Test", "Beta Begriff"], "Kat A")
        n = v.exportiere_csv(tmp_path / "a.csv", kategorie="Kat A")
    text = (tmp_path / "a.csv").read_text(encoding="utf-8")
    assert n == 2 and text == "begriff,kategorie\nAlpha-Test,Kat A\nBeta Begriff,Kat A\n"
    (tmp_path / "b.csv").write_text("Begriff;Kategorie\nGamma;Kat B\nAlpha-Test;Kat A\nDelta;\n",
                                    encoding="utf-8")
    (tmp_path / "c.txt").write_text("# Liste\nEpsilon\n\nZeta Zwei\n", encoding="utf-8")
    with vokmodul.Vokabular() as v:
        assert v.importiere_csv(tmp_path / "b.csv", "Standard") == (2, 3)
        assert v.importiere_csv(tmp_path / "c.txt", "Liste") == (2, 2)
        assert {b: k for b, k, _a in v.alle(suche="Delta")}["Delta"] == "Standard"
        assert [k for _b, k, _a in v.alle(suche="Zeta Zwei")] == ["Liste"]
        assert v.importiere_csv(tmp_path / "c.txt", "Liste") == (0, 2)
    fb = fachmodul.standard()
    assert fb.geschuetzt("Zeta Zwei", 0, 9, ganz=True)


def test_sicherung_mit_vokabular(frisch: Path, tmp_path: Path) -> None:
    with Pseudonymspeicher():  # creates the key file the backup insists on
        pass
    with vokmodul.Vokabular() as v:
        v.fuege_hinzu(["Sicherungsbegriff"], "Test")
    st = sichmodul.statistik()
    assert st.vokabular_begriffe > 39000 and dict(st.vokabular_kategorien)["Test"] == 1
    assert "Vokabular (Begriffe)" in dict(st.zeilen())
    archiv = sichmodul.exportiere(tmp_path / "s.zip")
    andere = tmp_path / "andere" / "p.sqlite"
    with vokmodul.Vokabular(andere) as v2:
        assert not v2.enthaelt("Sicherungsbegriff")
    b = sichmodul.importiere(archiv, andere, modus="zusammenfuehren")
    assert b.vokabular_neu >= 1
    with vokmodul.Vokabular(andere) as v2:
        assert v2.enthaelt("Sicherungsbegriff")


def test_vokabular_seite(qapp: QApplication, frisch: Path, tmp_path: Path) -> None:  # noqa: F811
    from pseudonymed.gui.hauptfenster import Hauptfenster
    from pseudonymed.gui.sitzung import Sitzung

    fenster = Hauptfenster(Sitzung())
    assert [fenster.tabs.tabText(i) for i in range(4)] == [
        "1 Patientendaten", "2 Identifikatoren", "3 Fachvokabular", "4 Dokumente"]
    seite = fenster.vokabular
    seite.synchron = True
    fenster.zeige_seite(seite)
    qapp.processEvents()
    gesamt = seite.modell.rowCount()
    assert gesamt > 39000 and seite.proxy.rowCount() == gesamt
    seite.suche.setText("nierenlag")
    assert 1 <= seite.proxy.rowCount() <= 5
    seite.suche.clear()
    assert seite.hinzufuegen("Testbegriff Neu", "Meine") == 1
    assert seite.hinzufuegen("Testbegriff Neu") == 0
    assert seite.hinzufuegen("ab") == 0 and "drei Zeichen" in seite.status.text()
    assert fachmodul.standard().geschuetzt("Testbegriff Neu", 0, 15, ganz=True)
    # the category filter narrows the table; sorting by the header works on the proxy
    i = seite.kategorie_filter.findData("Meine")
    seite.kategorie_filter.setCurrentIndex(i)
    assert seite.modell.rowCount() == 1 and seite.modell.zeilen[0][0] == "Testbegriff Neu"
    from PySide6.QtCore import Qt

    seite.kategorie_filter.setCurrentIndex(0)
    seite.ansicht.sortByColumn(1, Qt.SortOrder.DescendingOrder)
    erste = seite.proxy.index(0, 1).data()
    assert erste.casefold() >= seite.proxy.index(seite.proxy.rowCount() - 1, 1).data().casefold()
    # editing through the model writes to the table
    quelle = next(r for r, z in enumerate(seite.modell.zeilen) if z[0] == "Testbegriff Neu")
    idx = seite.modell.index(quelle, 1)
    assert seite.modell.setData(idx, "Andere") and seite.modell.data(idx) == "Andere"
    with vokmodul.Vokabular() as v:
        assert [k for _b, k, _a in v.alle(suche="Testbegriff Neu")] == ["Andere"]
    assert not seite.modell.setData(seite.modell.index(quelle, 0), "Wade")  # exists already
    # delete, export, import, reseed
    assert seite.loeschen(["Testbegriff Neu", "Wolf"]) == 2
    assert not fachmodul.standard().ist_fachbegriff("Wolf")
    assert seite.exportiere_csv(tmp_path / "alles.csv") == seite.modell.rowCount()
    (tmp_path / "neu.txt").write_text("Importbegriff\n", encoding="utf-8")
    assert seite.importiere_datei(tmp_path / "neu.txt", "Liste") == 1
    assert seite.grundbestand_einspielen() >= 1  # "Wolf" comes back
    assert fachmodul.standard().ist_fachbegriff("Wolf")
    assert "Nutzervokabular" in fenster.analysieren.vokabular_info.text() or \
        fenster.analysieren.aktualisiere() is None
    fenster.close()
