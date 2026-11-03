"""The semantic lexicon: loading, reloading, user rows, statistics, and the protection it
leaves alone."""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from anonymed.erkennung import lexikon
from anonymed.erkennung.vokabular import Vokabular


@pytest.fixture
def lexikon_an(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANONYMED_LEXIKON", "1")


def test_laedt_gebuendelte_klassen(tmp_path: Path, lexikon_an: None) -> None:
    with Vokabular(tmp_path / "p.sqlite") as v:
        klassen = {k: (r, n) for k, r, n in lexikon.je_klasse(v.verbindung)}
        for klasse in ("Anatomie", "Medikament", "Erkrankung", "Laborwert", "Befund", "Eponym"):
            assert klassen[klasse][0] == lexikon.FACH and klassen[klasse][1] > 0, klasse
        assert ("Laborwert", lexikon.FACH) in lexikon.klassen_von(v.verbindung, "NfL")
        assert ("Medikament", lexikon.FACH) in lexikon.klassen_von(v.verbindung, "Levetiracetam")
        meta = dict(v.verbindung.execute("SELECT quelle, anzahl FROM lexikon_meta"))
        assert meta["Wikidata"] > 30000 and meta["Laborwerte"] > 200
        for klasse in ("Beruf", "Erwerbsstatus", "Titel", "Klinik", "Studie"):
            assert klassen[klasse][0] == lexikon.MERKMAL and klassen[klasse][1] > 0, klasse
        assert ("Titel", lexikon.MERKMAL) in lexikon.klassen_von(v.verbindung, "Dr. med.")
        assert ("Erwerbsstatus", lexikon.MERKMAL) in lexikon.klassen_von(v.verbindung, "Rentnerin")
        zusatz = v.verbindung.execute("SELECT zusatz FROM lexikon WHERE begriff = 'Bauer'"
                                      " AND klasse = 'Beruf'").fetchone()[0]
        assert '"nachname":1' in zusatz


def test_nur_bei_geaenderten_daten_neu(tmp_path: Path, lexikon_an: None,
                                       monkeypatch: pytest.MonkeyPatch) -> None:
    datei = tmp_path / "berufe.csv.gz"
    with gzip.open(datei, "wt", encoding="utf-8") as f:
        f.write("# Quelle: synthetisch\nbegriff,klasse,kennung,grundform,nachname\n"
                "Bäckerin,Beruf,29202,Bäcker,\nBauer,Beruf,11104,Bauer,1\n")
    quellen = (lexikon.Quelle("Berufe", datei, lexikon._berufe),)
    monkeypatch.setattr(lexikon, "QUELLEN", quellen)
    with Vokabular(tmp_path / "p.sqlite") as v:
        v.verbindung.execute("INSERT INTO lexikon VALUES ('Eigene Klinik', 'Klinik', 'merkmal',"
                             " 'Nutzer', '', '')")
        v.verbindung.commit()
        assert lexikon.sicherstellen(v.verbindung) == {}
        assert ("Beruf", lexikon.MERKMAL) in lexikon.klassen_von(v.verbindung, "bäckerin")
        zusatz = v.verbindung.execute("SELECT zusatz FROM lexikon WHERE begriff = 'Bauer'"
                                      ).fetchone()[0]
        assert '"nachname":1' in zusatz
    with gzip.open(datei, "wt", encoding="utf-8") as f:
        f.write("# Quelle: synthetisch\nbegriff,klasse,kennung,grundform,nachname\n"
                "Rentnerin,Erwerbsstatus,,Rentner,\n")
    with Vokabular(tmp_path / "p.sqlite") as v:
        assert lexikon.klassen_von(v.verbindung, "Bäckerin") == []
        assert lexikon.klassen_von(v.verbindung, "Rentnerin") == [("Erwerbsstatus", "merkmal")]
        assert lexikon.klassen_von(v.verbindung, "Eigene Klinik") == [("Klinik", "merkmal")]


def test_abgeschaltet_ohne_tabelle(tmp_path: Path) -> None:
    with Vokabular(tmp_path / "p.sqlite") as v:
        assert lexikon.je_klasse(v.verbindung) == []


def test_aendert_den_schutz_nicht(tmp_path: Path, lexikon_an: None,
                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """Professions, titles, hospitals and studies must never become protected terms."""
    from anonymed.erkennung import fachbegriffe

    monkeypatch.setenv("ANONYMED_DATENORDNER", str(tmp_path))
    fachbegriffe.standard.cache_clear()
    with Vokabular() as v:
        v.verbindung.execute("INSERT INTO lexikon VALUES ('Bauer', 'Beruf', 'merkmal', 'Nutzer',"
                             " '', '')")
        v.verbindung.commit()
    try:
        matcher = fachbegriffe.standard()
        text = "Herr Bauer berichtet über Kopfschmerz."
        geschuetzt = {text[a:b] for a, b in matcher.spannen(text)}
        assert "Bauer" not in geschuetzt and "Kopfschmerz" in geschuetzt
    finally:
        fachbegriffe.standard.cache_clear()


def test_statistik_zeigt_lexikon(tmp_path: Path, lexikon_an: None) -> None:
    from anonymed.pseudonyme.sicherung import statistik

    pfad = tmp_path / "p.sqlite"
    Vokabular(pfad).close()
    zeilen = dict(statistik(pfad).zeilen())
    assert int(zeilen["Lexikon (Einträge)"]) > 30000
    assert "    Anatomie (Fachklasse)" in zeilen and "    Klinik (Identifikator)" in zeilen


def test_gemtex_typen() -> None:
    assert lexikon.gemtex_typ("Klinik") == "LOCATION_HOSPITAL"
    assert lexikon.gemtex_typ("Studie") == "ID"
    assert lexikon.gemtex_typ("Beruf") == "PROFESSION"
    assert lexikon.gemtex_typ("Erwerbsstatus") == ""
    assert lexikon.gemtex_typ("Titel", '{"art":"akademisch"}') == "NAME_TITLE"
    assert lexikon.gemtex_typ("Titel", '{"art":"adel"}') == ""
    assert lexikon.gemtex_typ("Anatomie") == ""
