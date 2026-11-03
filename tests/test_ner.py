"""Optional NER: unknown people and places, eponyms excluded, round trip through the store."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pymupdf
import pytest

from pseudonymed.ablauf import Ablaufoptionen, pseudonymisiere
from pseudonymed.dokument.textebene import lese_seite
from pseudonymed.erkennung import ner as nermodul
from pseudonymed.kern.modell import Patient
from pseudonymed.pruefung.bericht import als_text
from pseudonymed.pseudonyme.rueckersetzung import rueckersetzen
from pseudonymed.pseudonyme.speicher import Pseudonymspeicher
from pseudonymed.pseudonyme.vergabe import Pseudonymvergabe
from tests.synthetisch import erzeuge_befund

braucht_ner = pytest.mark.skipif(not nermodul.verfuegbar(), reason="spaCy oder Modell fehlt")


def test_plausibilitaet() -> None:
    assert nermodul._personenkern("Dr. med. Erika Beispiel") == "Erika Beispiel"
    assert nermodul._personenkern("Ehefrau Hildegund Flantz") == "Hildegund Flantz"
    assert nermodul._plausibler_name("Erika Beispiel")
    assert not nermodul._plausibler_name("Alzheimer")
    assert not nermodul._plausibler_name("Morbus Parkinson")
    assert not nermodul._plausibler_name("max mustermann")
    assert not nermodul._plausibler_name("Ab")


@braucht_ner
def test_finde_ner_auf_seite(patient: Patient) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    y = 80
    for zeile in ("Die Ehefrau Hildegund Flantz begleitete den Patienten nach Dresden.",
                  "Diagnose: Morbus Alzheimer, Verdacht auf Parkinson-Syndrom.",
                  "Befundbericht von Dr. med. Erika Hoffmann, Klinikum Chemnitz."):
        page.insert_text((56, y), zeile, fontsize=10.5, fontname="helv")
        y += 14
    seite = lese_seite(page)
    allein = dataclasses.replace(patient, angehoerige=[], zuweiser=[])
    v = Pseudonymvergabe(allein, "K7Q4MX", geheimnis=bytes(32))
    treffer = nermodul.finde_ner(seite, nermodul.NER(), v, [])
    texte = {f.text: f for f in treffer}
    assert "Hildegund Flantz" in texte and texte["Hildegund Flantz"].pseudonym.startswith("Person ")
    assert "Erika Hoffmann" in texte and texte["Erika Hoffmann"].regel == "ner.person"
    assert "Dresden" in texte and texte["Dresden"].pseudonym.startswith("[Ort ")
    assert not any("Alzheimer" in t or "Parkinson" in t for t in texte)
    assert v.zuordnung[texte["Hildegund Flantz"].pseudonym] == "Hildegund Flantz"
    # the same name gives the same code again
    assert v.person_unbekannt("Hildegund  Flantz") == texte["Hildegund Flantz"].pseudonym


@braucht_ner
def test_ner_im_ablauf(tmp_path: Path, patient: Patient, speicher: Pseudonymspeicher) -> None:
    allein = dataclasses.replace(patient, angehoerige=[], zuweiser=[])
    befund = erzeuge_befund(tmp_path / "befund.pdf", patient)
    ohne = pseudonymisiere(befund, allein, Ablaufoptionen(ner=False, suffix="_ohne"),
                        speicher=speicher)
    assert patient.angehoerige[0].vorname in ohne.text  # the rules do not know the wife
    mit = pseudonymisiere(befund, allein, Ablaufoptionen(ner=True, suffix="_mit"),
                          speicher=speicher)
    assert mit.freigegeben, als_text(mit.bericht)
    assert patient.angehoerige[0].vorname not in mit.text
    assert any(f.regel == "ner.person" for f in mit.fundstellen)
    assert any("NER (" in w for w in mit.bericht.hinweise)
    person = next(f for f in mit.fundstellen if f.regel == "ner.person"
                  and patient.angehoerige[0].nachname in f.text)
    z = speicher.zuordnung(allein.schluessel())
    r = rueckersetzen(f"{person.pseudonym} war dabei.", z, bekannte_kennungen=speicher.kennungen())
    assert patient.angehoerige[0].vollname in r.text and r.verbleibend == []


def test_ohne_modell_warnung(tmp_path: Path, befund: Path, patient: Patient,
                             speicher: Pseudonymspeicher, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(nermodul, "verfuegbar", lambda: False)
    erg = pseudonymisiere(befund, patient, Ablaufoptionen(ner=True), speicher=speicher)
    assert erg.freigegeben and any("NER gewünscht" in w for w in erg.bericht.warnungen)


def test_alltagssubstantive_sind_keine_namen() -> None:
    """Findings prose and lists: common and compound nouns are not people or places,
    salutations and prepositions still make unknown words count."""
    import pytest

    from pseudonymed.erkennung import ner as nermodul

    if not nermodul.verfuegbar():
        pytest.skip("Extra 'ner' nicht installiert")
    n = nermodul.NER()
    liste = "Konzentration: gut.\nHaltung: aufrecht.\nNierenlager: frei.\nStrichgang: unsicher."
    assert n.entitaeten(liste) == []
    prosa = ("Befund: Konzentration und Aufmerksamkeit regelrecht, Haltung aufrecht,"
             " Nierenlager frei, Strichgang unsicher.")
    assert n.entitaeten(prosa) == []
    namen = "Herr Flantz kam mit Frau Erika Hoffmann aus Dresden; sie wohnen in Riesa."
    texte = {e[3] for e in n.entitaeten(namen)}
    assert {"Erika Hoffmann", "Dresden"} <= texte
    assert "Flantz" in texte or "Herr Flantz" in texte  # unknown surname, salutation before
    assert any("Riesa" in t for t in texte)
