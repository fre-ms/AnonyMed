"""Optional reference data loaded from the publisher: the Austrian hospital directory.

The workbook here is synthetic but follows the ministry's layout (sheets "KA gesamt" and
"Rohdaten Standorte"); place names are real Austrian municipalities from the bundled
demography table, hospital names are invented."""

from __future__ import annotations

import io
import sqlite3
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import pytest
from PySide6.QtWidgets import QApplication
from typer.testing import CliRunner

from anonymed.erkennung import lexikon
from anonymed.erkennung import zusatzdaten as zd
from tests.test_gui import qapp  # noqa: F401

KOPF = ["Bundesland", "KA-Nr.", "Bezeichnung", "Kurzbezeichnung", "Versorgungssektor",
        "Versorgungsbereich", "Bezirk", "Adresse", "Bettenanzahl", "Träger-Nr.",
        "Trägerbezeichnung"]
KAS = [
    ["Burgenland", "K901", "Musterklinik am Bach", "Bach KL", "Akutversorgung",
     "Allgemeinversorgung", "Eisenstadt(Stadt)", "Hauptstraße 1, 7000 Eisenstadt", "120", "T1",
     "Musterträger GmbH"],
    ["Burgenland", "K902", "Reha-Zentrum Beispielhof", "Beispielhof REHA", "Rehabilitation",
     "Bewegungsapparat", "Oberwart", "Kurweg 2, 7431 Bad Tatzmannsdorf", "80", "T2",
     "Reha Muster GmbH"],
    ["Wien", "K903", "Beispielinstitut für Psychiatrie", "Beispiel PSY", "Akutversorgung",
     "Psychiatrie", "Wien 23.,Liesing", "Gasse 3, 1230 Wien", "200", "T3", "Verein Muster"],
]
STANDORTE_KOPF = ["KA-Nr., Standort-Code", "Standort Bezeichnung", "Gemeinde", "Ort", "Straße",
                  "ÖSG Abteilung", "Bettenanzahl"]
STANDORTE = [
    ["K901.1", "Bach KL", "10101 Eisenstadt", "7000 Eisenstadt", "Hauptstraße 1",
     "03 Chirurgie", "60"],
    ["K901.1", "Bach KL", "10101 Eisenstadt", "7000 Eisenstadt", "Hauptstraße 1",
     "05 Innere Medizin", "60"],
    ["K901.2", "Bach KL Nord", "10901 Bad Tatzmannsdorf", "7431 Bad Tatzmannsdorf",
     "Nordweg 5", "05 Innere Medizin", "10"],
]


def _blatt(zeilen: list[list[str]]) -> str:
    teile = []
    for r, zeile in enumerate(zeilen, 1):
        zellen = "".join(
            f'<c r="{chr(65 + i)}{r}" t="inlineStr"><is><t>{escape(v)}</t></is></c>'
            for i, v in enumerate(zeile))
        teile.append(f'<row r="{r}">{zellen}</row>')
    return ('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<sheetData>{"".join(teile)}</sheetData></worksheet>')


def mappe(pfad: Path) -> Path:
    ns = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    with zipfile.ZipFile(pfad, "w") as z:
        z.writestr("xl/workbook.xml",
                   f'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   f'{ns}><sheets><sheet name="KA gesamt" sheetId="1" r:id="rId1"/>'
                   '<sheet name="Rohdaten Standorte" sheetId="2" r:id="rId2"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
                   'relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/>'
                   '<Relationship Id="rId2" Target="worksheets/sheet2.xml"/></Relationships>')
        z.writestr("xl/worksheets/sheet1.xml", _blatt([KOPF] + KAS))
        z.writestr("xl/worksheets/sheet2.xml", _blatt([STANDORTE_KOPF] + STANDORTE))
    return pfad


def _zeilen(con: sqlite3.Connection) -> dict[str, tuple[str, str]]:
    return {k: (b, z) for b, k, z in con.execute(
        "SELECT begriff, kennung, zusatz FROM lexikon WHERE quelle = ?", (zd.QUELLE_KLINIKEN_AT,))}


def test_mappe_wird_gelesen_und_zugeordnet(tmp_path: Path) -> None:
    con = sqlite3.connect(tmp_path / "p.sqlite")
    bericht = zd.lade_kliniken_at(con, datei=mappe(tmp_path / "KA-Verzeichnis 2026-09-18.xlsx"))
    assert bericht.anzahl == 4 and bericht.ohne_gemeinde == 0 and bericht.stand == "2026-09-18"
    z = _zeilen(con)
    assert set(z) == {"AT:K901.1", "AT:K901.2", "AT:K902", "AT:K903"}
    assert '"gemeinde":"10101"' in z["AT:K901.1"][1]
    assert '"gemeinde":"10901"' in z["AT:K901.2"][1]  # second site, own municipality
    assert '"gemeinde":"10901"' in z["AT:K902"][1] and '"typ":"reha"' in z["AT:K902"][1]
    assert '"gemeinde":"92301"' in z["AT:K903"][1] and '"typ":"psychiatrie"' in z["AT:K903"][1]
    assert zd.geladene(con) == [(zd.QUELLE_KLINIKEN_AT, 4, zd.geladene(con)[0][2])]


def test_mappe_finden_auf_der_seite() -> None:
    html = ('<a href="/dam/jcr:abc/Logo.png">Logo</a> <a href="/dam/jcr:123/'
            'KA-Verzeichnis%202026-09-18.xlsx">Liste der Krankenanstalten</a>')
    url = zd.mappe_finden(html)
    assert url.startswith("https://www.sozialministerium.gv.at/dam/jcr:123/")
    with pytest.raises(zd.ZusatzFehler):
        zd.mappe_finden("<p>keine Mappe</p>")


def test_bleibt_beim_neuladen_der_mitgelieferten_daten(tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANONYMED_LEXIKON", "1")
    con = sqlite3.connect(tmp_path / "p.sqlite")
    zd.lade_kliniken_at(con, datei=mappe(tmp_path / "m.xlsx"))
    monkeypatch.setattr(lexikon, "QUELLEN", (lexikon.QUELLEN[2],))  # a small bundled source
    lexikon.sicherstellen(con, erzwingen=True)
    assert len(_zeilen(con)) == 4


def test_fenster_laedt_beim_ministerium(qapp: QApplication, tmp_path: Path,  # noqa: F811
                                        monkeypatch: pytest.MonkeyPatch) -> None:
    from anonymed.gui.hauptfenster import Hauptfenster
    from anonymed.gui.sitzung import Sitzung

    monkeypatch.setenv("ANONYMED_DATENORDNER", str(tmp_path / "daten"))
    daten = mappe(tmp_path / "m.xlsx").read_bytes()
    abrufe: list[str] = []

    def holen(url: str) -> bytes:
        abrufe.append(url)
        return (b'<a href="/dam/KA-Verzeichnis%202026-09-18.xlsx">Liste</a>'
                if url == zd.SEITE_KLINIKEN_AT else daten)

    fenster = Hauptfenster(Sitzung(speicherpfad=tmp_path / "p.sqlite"))
    seite = fenster.datenbank
    seite.synchron = True
    seite.holen = holen
    bericht = seite.kliniken_at_laden()
    assert bericht is not None and bericht.anzahl == 4 and len(abrufe) == 2
    assert "4 Einträge geladen" in seite.zusatz_status.text()
    zeilen = dict(seite.aktualisiere().zeilen())
    assert zeilen["Zusatzdaten (selbst geladen)"].startswith(f"{zd.QUELLE_KLINIKEN_AT}: 4 Einträge")


def test_kommandozeile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from anonymed.cli import app

    monkeypatch.setenv("ANONYMED_DATENORDNER", str(tmp_path / "daten"))
    ergebnis = CliRunner().invoke(app, ["datenbank", "zusatzdaten", "kliniken-at", "--datei",
                                        str(mappe(tmp_path / "m.xlsx")), "--speicher",
                                        str(tmp_path / "p.sqlite")])
    assert ergebnis.exit_code == 0, ergebnis.output
    assert "außer zu kommerziellen Zwecken" in ergebnis.output and "4 Einträge" in ergebnis.output
    falsch = CliRunner().invoke(app, ["datenbank", "zusatzdaten", "unbekannt"])
    assert falsch.exit_code != 0


def test_kaputte_datei(tmp_path: Path) -> None:
    datei = tmp_path / "kaputt.xlsx"
    datei.write_bytes(b"kein zip")
    with pytest.raises(zipfile.BadZipFile):
        zd.lade_kliniken_at(sqlite3.connect(":memory:"), datei=datei)
    leer = io.BytesIO()
    with zipfile.ZipFile(leer, "w") as z:
        z.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/'
                   'spreadsheetml/2006/main"><sheets/></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.'
                   'openxmlformats.org/package/2006/relationships"/>')
    (tmp_path / "leer.xlsx").write_bytes(leer.getvalue())
    with pytest.raises(zd.ZusatzFehler):
        zd.lade_kliniken_at(sqlite3.connect(":memory:"), datei=tmp_path / "leer.xlsx")
