"""Rules on page text: longest match wins, lower case words stay, neighbours merge."""

from __future__ import annotations

import datetime as dt

import pymupdf

from anonymed.dokument.textebene import lese_seite
from anonymed.erkennung.deterministisch import finde, finde_im_text, regeln_fuer
from anonymed.kern.modell import Patient
from anonymed.pseudonyme.vergabe import Pseudonymvergabe


def _patient() -> Patient:
    return Patient(nachname="Müller", vorname="Max", geschlecht="m",
                   geburtsdatum=dt.date(1990, 2, 1), strasse="Musterstraße 12", plz="80331",
                   ort="München", emails=["max@example.org"])


def test_laengster_treffer_gewinnt() -> None:
    p = _patient()
    regeln = regeln_fuer(p, Pseudonymvergabe(p, "A"))
    text = "Herr Max Müller, geb. 01.02.1990, max. 3 Tabletten, Müllerstraße"
    treffer = finde_im_text(text, regeln)
    ids = [r.id for _a, _b, r in treffer]
    assert ids == ["name.vollname", "datum.geburtsdatum"]
    a, b, _ = treffer[0]
    assert text[a:b] == "Max Müller"


def test_benachbarte_treffer_verschmelzen() -> None:
    p = _patient()
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Anschrift: Musterstraße 12, 80331 München, Tel.", fontname="helv")
    regeln = regeln_fuer(p, Pseudonymvergabe(p, "A"))
    fundstellen = finde(lese_seite(page), regeln)
    assert [f.pseudonym for f in fundstellen] == ["[Wohnort]"]
    assert fundstellen[0].text == "Musterstraße 12, 80331 München"


def test_generische_muster_und_rollen() -> None:
    p = _patient()
    regeln = regeln_fuer(p, Pseudonymvergabe(p, "A"))
    text = "KVNR X123456789, IBAN DE12 3456 7890 1234 5678 90, fremd@example.com"
    ids = sorted(r.id for _a, _b, r in finde_im_text(text, regeln))
    assert ids == ["muster.email", "muster.iban", "muster.versichertennummer"]
