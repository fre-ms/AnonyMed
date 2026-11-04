"""Medications and laboratory values, redacted only on request.

Both belong to the medical content and stay by default.  The analysis offers one option
each; with it, the terms of the lexicon become hits that the visual review may discard:

* medications (``Medikament``): the drug names of the lexicon (Wikidata, ATC and INN), less
  those that also name a disease, finding, lab value, body part or organism ("ASS" is also
  an autism spectrum disorder, "TSH" and "Kalium" are lab values), plus every term of the
  medical vocabulary filed under the category "Medikament" (trade names such as Marcumar
  are missing from Wikidata);
* laboratory values (``Laborwert``): the curated lab list of the lexicon plus the vocabulary
  category "Laborwert", and only together with a measurement right after the name
  ("TSH 2,3 mU/l", "CRP: < 5 mg/l", "HbA1c von 6,2 %").  A lab name without a value stays,
  the Wikidata lab rows are too noisy to use ("Zuname", "Arizona").

Entries of up to three characters and abbreviations (two capitals or a digit, up to six
characters: "Hb", "LSD", "HbA1c") count only in the listed spelling.  Hits become
"[Medikament <code>]" and "[Laborwert <code>]", keyed on the text, and are restored by the
re-identification like any pseudonym.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from pseudonymed.dokument.textebene import Seitentext
from pseudonymed.erkennung import lexikon
from pseudonymed.erkennung.identifikatoren import Erkenner
from pseudonymed.kern.modell import Art, Fundstelle
from pseudonymed.pseudonyme.vergabe import Pseudonymvergabe

KLASSE_MEDIKAMENT = "Medikament"
KLASSE_LABORWERT = "Laborwert"
REGEL = {KLASSE_MEDIKAMENT: "medizin.medikament", KLASSE_LABORWERT: "medizin.laborwert"}
VOKABULAR_KATEGORIEN = {
    KLASSE_MEDIKAMENT: ("medikament", "medikamente"),
    KLASSE_LABORWERT: ("laborwert", "laborwerte"),
}
QUELLE_LABORWERTE = "Laborwerte"  # the curated list; the Wikidata lab rows are left out
# A drug name that also names one of these is no safe medication hit.
_ANDERE_KLASSEN = ("Erkrankung", "Symptom", "Befund", "Anatomie", "Laborwert", "Physiologie",
                   "Psyche", "Organismus", "Eponym")
# Everyday substances that the drug classes of Wikidata list too.
_KEIN_MEDIKAMENT = {"sauerstoff", "salz", "kochsalz", "wasser", "zucker", "alkohol", "ethanol",
                    "koffein", "nikotin", "luft", "licht", "kaffee", "tee"}
_ABKUERZUNG = re.compile(r"^(?=.{2,6}$)(?=(?:.*[A-ZÄÖÜ]){2}|.*\d)")

_EINHEIT = (r"(?:%|‰|mmHg|mm/h|fl|fL|pg|INR|Ratio|(?:Tsd\.?|G|T|Mio\.?)/[µμunp]?[lL]"
            r"|/[µμn]?[lL]|[mµμunpkf]?(?:g|mol|U|IU|E|Eq|kat)(?:/(?:[mµμnd]?[lL]|h|min|kg|g"
            r"|24[ \t]?h|mmol))?)(?![\w/])")
_MESSWERT = re.compile(
    r"[ \t]*(?:[:=][ \t]*)?(?:(?:von|bei|mit|auf|ca\.)[ \t]+)?(?:(?:[<>≤≥]=?)[ \t]*)?"
    r"\d+(?:[.,]\d+)?(?![\d.,/:]*\d)(?:[ \t]*" + _EINHEIT + r")?")


def _abkuerzung(begriff: str) -> bool:
    """Short entries and abbreviations count only as listed ("Hb", "LSD", "HbA1c")."""
    return len(begriff) <= 3 or bool(_ABKUERZUNG.match(begriff))


def messwert_ende(text: str, ende: int) -> int | None:
    """End of a measurement right after a lab name ending at ``ende``, or None."""
    m = _MESSWERT.match(text, ende)
    return m.end() if m else None


def _zeilen(verbindung: sqlite3.Connection, medikamente: bool,
            laborwerte: bool) -> list[tuple[str, str]]:
    verbindung.executescript(lexikon.SCHEMA)
    zeilen: list[tuple[str, str]] = []
    if medikamente:
        andere = ",".join("?" * len(_ANDERE_KLASSEN))
        zeilen += [(b, KLASSE_MEDIKAMENT) for (b,) in verbindung.execute(
            "SELECT DISTINCT l.begriff FROM lexikon l WHERE l.klasse = ? AND l.rolle = ?"
            " AND NOT EXISTS (SELECT 1 FROM lexikon m WHERE m.begriff = l.begriff"
            f" AND m.rolle = ? AND m.klasse IN ({andere}))",
            (KLASSE_MEDIKAMENT, lexikon.FACH, lexikon.FACH, *_ANDERE_KLASSEN))]
    if laborwerte:
        zeilen += [(b, KLASSE_LABORWERT) for (b,) in verbindung.execute(
            "SELECT DISTINCT begriff FROM lexikon WHERE klasse = ? AND quelle = ?",
            (KLASSE_LABORWERT, QUELLE_LABORWERTE))]
    for klasse, gewaehlt in ((KLASSE_MEDIKAMENT, medikamente), (KLASSE_LABORWERT, laborwerte)):
        if not gewaehlt:
            continue
        namen = VOKABULAR_KATEGORIEN[klasse]
        try:
            zeilen += [(b, klasse) for (b,) in verbindung.execute(
                f"SELECT begriff FROM vokabular WHERE lower(kategorie) IN"
                f" ({','.join('?' * len(namen))})", namen)]
        except sqlite3.OperationalError:
            pass
    return zeilen


def erkenner_aus(zeilen: list[tuple[str, str]]) -> Erkenner:
    e = Erkenner(registernummern=False)
    for begriff, klasse in zeilen:
        if klasse == KLASSE_MEDIKAMENT and (len(begriff) < 3
                                            or begriff.casefold() in _KEIN_MEDIKAMENT):
            continue
        e.fuege_hinzu(begriff, klasse, exakt=_abkuerzung(begriff))
    return e


def finde(seite: Seitentext, erkenner: Erkenner, vergabe: Pseudonymvergabe,
          vorhandene: list[Fundstelle], quelle: str = "text") -> list[Fundstelle]:
    """Medication names, and lab names with their measurement, that no other hit covers."""
    text = seite.text
    treffer: list[Fundstelle] = []
    for k in erkenner.kandidaten(text):
        a, b = k.start, k.ende
        if k.klasse == KLASSE_LABORWERT:
            ende = messwert_ende(text, b)
            if ende is None:
                continue  # a lab name without a value says nothing about the person
            b = ende
        if any(f.ende > f.start and a < f.ende and b > f.start for f in vorhandene + treffer):
            continue
        klartext = text[a:b]
        treffer.append(Fundstelle(
            seite=seite.nummer, start=a, ende=b, text=klartext, art=Art.MEDIZIN,
            regel=REGEL[k.klasse],
            pseudonym=vergabe.identifikator(k.klasse, klartext, Art.MEDIZIN),
            rects=[tuple(r) for r in seite.rects(a, b)], schriftgroesse=seite.schriftgroesse(a, b),
            quelle=quelle))
    return treffer


# -- the matcher used by the analysis ----------------------------------------------------------
_cache: dict[tuple[bool, bool], tuple[Path, int, int, Erkenner]] = {}


def standard(medikamente: bool, laborwerte: bool) -> Erkenner | None:
    """The matcher for the chosen classes (None when neither is chosen), rebuilt only when
    the lexicon or the vocabulary changed."""
    if not (medikamente or laborwerte):
        return None
    from pseudonymed.erkennung.vokabular import Vokabular

    schluessel = (medikamente, laborwerte)
    try:
        with Vokabular() as v:
            versionen = (lexikon.version(v.verbindung), v.version())
            alt = _cache.get(schluessel)
            if alt is not None and alt[0] == v.pfad and alt[1:3] == versionen:
                return alt[3]
            erkenner = erkenner_aus(_zeilen(v.verbindung, medikamente, laborwerte))
            pfad = v.pfad
    except (sqlite3.Error, OSError):
        return Erkenner(registernummern=False)
    _cache[schluessel] = (pfad, *versionen, erkenner)
    return erkenner


def _cache_clear() -> None:
    _cache.clear()


standard.cache_clear = _cache_clear  # type: ignore[attr-defined]
