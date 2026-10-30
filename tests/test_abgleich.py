"""Error-tolerant record linkage: scores, thresholds and the store's decisions."""

from __future__ import annotations

import dataclasses
import datetime as dt

import pytest

from anonymed.kern.modell import Patient
from anonymed.pseudonyme.abgleich import (
    SCHWELLE_GLEICH,
    SCHWELLE_UNSICHER,
    dice,
    namensaehnlichkeit,
    vergleiche,
)
from anonymed.pseudonyme.speicher import Pseudonymspeicher, UnsichererAbgleich


def _p(nachname: str = "Flantz", vorname: str = "Gotthard", geb: str = "1959-07-21",
       geburtsname: str = "") -> Patient:
    return Patient(nachname=nachname, vorname=vorname, geburtsname=geburtsname,
                   geburtsdatum=dt.date.fromisoformat(geb))


def _zeile(p: Patient, kennung: str = "K7Q4MX") -> dict:
    return {"kennung": kennung, "schluessel": p.schluessel(), "nachname": p.nachname,
            "vorname": p.vorname, "geburtsname": p.geburtsname,
            "geburtsdatum": p.geburtsdatum.isoformat()}


def test_dice_und_namen() -> None:
    assert dice("Müller", "Mueller") == 1.0  # normalisation strips the umlaut
    assert dice("Flantz", "Flantzz") == pytest.approx(14 / 15)
    assert dice("abc", "xyz") == 0.0
    assert namensaehnlichkeit("Meier", "Mayer") >= 0.9  # phonetically equal
    assert namensaehnlichkeit("Müller-Lüdenscheidt", "Lüdenscheidt") == 1.0
    assert namensaehnlichkeit("Hans Peter", "Peter") == 1.0
    assert namensaehnlichkeit("", "Peter") == 0.0


def test_scores() -> None:
    p = _p()
    assert vergleiche(p, _zeile(p)).score == 1.0
    assert vergleiche(p, _zeile(_p(nachname="Flantzz"))).score >= SCHWELLE_GLEICH
    assert vergleiche(p, _zeile(_p(vorname="Gotthart"))).score >= SCHWELLE_GLEICH
    getauscht = vergleiche(p, _zeile(_p(nachname="Gotthard", vorname="Flantz")))
    assert getauscht.score == 1.0
    jahr = vergleiche(p, _zeile(_p(geb="1958-07-21")))
    assert SCHWELLE_UNSICHER <= jahr.score < SCHWELLE_GLEICH
    tag_monat = vergleiche(p, _zeile(_p(geb="1959-07-21")), )
    assert tag_monat.score == 1.0
    gedreht = vergleiche(_p(geb="1959-03-07"), _zeile(_p(geb="1959-07-03")))
    assert SCHWELLE_UNSICHER <= gedreht.score < SCHWELLE_GLEICH
    namensvetter = vergleiche(p, _zeile(_p(geb="1975-01-02")))
    assert namensvetter.score < SCHWELLE_UNSICHER
    fremd = vergleiche(p, _zeile(_p(nachname="Zahn", vorname="Ursel", geb="1977-02-01")))
    assert fremd.score < 0.5
    geburtsname = vergleiche(_p(nachname="Weber", geburtsname="Flantz"), _zeile(p))
    assert geburtsname.score == 1.0


def test_speicher_verknuepft_schreibvarianten(speicher: Pseudonymspeicher) -> None:
    p = _p()
    k = speicher.kennung_fuer(p).kennung
    variante = _p(nachname="Flantzz", vorname="GOTTHARD")
    erg = speicher.kennung_fuer(variante)
    assert erg.kennung == k and erg.verknuepft and erg.abgleich is not None
    assert erg.abgleich.score >= SCHWELLE_GLEICH
    assert speicher.kennung_fuer(variante).kennung == k  # alias now known exactly
    assert speicher.zuordnung(variante.schluessel()) == speicher.zuordnung(p.schluessel())
    assert speicher.patient_info(variante.schluessel())["kennung"] == k
    assert len(speicher.aliasse(p.schluessel())) == 1
    assert speicher.finde(suche="flantzz")[0]["kennung"] == k


def test_unsicherer_abgleich_braucht_entscheidung(speicher: Pseudonymspeicher) -> None:
    p = _p()
    k = speicher.kennung_fuer(p).kennung
    jahr_falsch = _p(geb="1958-07-21")
    with pytest.raises(UnsichererAbgleich) as e:
        speicher.kennung_fuer(jahr_falsch)
    assert e.value.kandidaten[0].kennung == k
    neu = speicher.kennung_fuer(jahr_falsch, entscheidung="neu")
    assert neu.neu and neu.kennung != k
    andere = _p(geb="1959-07-22")
    gleich = speicher.kennung_fuer(andere, entscheidung=f"gleich:{k.lower()}")
    assert gleich.kennung == k and gleich.verknuepft
    with pytest.raises(ValueError):
        speicher.kennung_fuer(_p(geb="1959-07-23"), entscheidung="gleich:ZZZZZZ")


def test_kollision_bekommt_variante(speicher: Pseudonymspeicher) -> None:
    """Same canonical identity, declared to be different people: distinct codes."""
    a = _p(nachname="Meier", vorname="Hans", geb="1960-01-01")
    b = _p(nachname="Mayer", vorname="Hanns", geb="1960-01-01")
    ka = speicher.kennung_fuer(a).kennung
    try:
        kb = speicher.kennung_fuer(b, entscheidung="neu").kennung
    except UnsichererAbgleich:  # pragma: no cover - depends on thresholds
        kb = speicher.kennung_fuer(b, entscheidung="neu").kennung
    assert kb != ka and len(kb) == 6


def test_namensvetter_ist_neue_person(speicher: Pseudonymspeicher) -> None:
    k1 = speicher.kennung_fuer(_p()).kennung
    erg = speicher.kennung_fuer(_p(geb="1975-01-02"))
    assert erg.neu and erg.kennung != k1


def test_dataclass_replace_hilft_beim_testen() -> None:
    p = _p()
    assert dataclasses.replace(p, nachname="X").nachname == "X"
