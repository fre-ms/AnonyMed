"""Put the clear data back into text a language model produced from the
pseudonymised document."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from anonymed.kern.modell import Person
from anonymed.pseudonyme.kennung import ist_hashkennung, kennung_gueltig

_KENNUNG = r"(?:[A-Z]{1,3}|[A-Z2-9]{4,9})"
_PATIENT = re.compile(rf"^(Patient|Patientin) ({_KENNUNG})$")
_ANGEHOERIGE = re.compile(rf"^Angehörige ({_KENNUNG})-(\d+)$")
_ZUWEISER = re.compile(r"^Dr\. Z(\d+)$")
_KLAMMER = re.compile(r"^\[(.+)\]$")

_REST_MUSTER = [
    re.compile(rf"\bPatient(?:in|en)?\s+{_KENNUNG}(?![\w])"),
    re.compile(rf"\bAngehörige[nr]?\s+{_KENNUNG}-\d+(?!\d)"),
    re.compile(r"\bDr\.\s*Z\d+(?!\d)"),
    re.compile(r"\[(?:geb\.|Alter|Wohnort|Straße|Strasse|PLZ Ort|Ort|Telefon|E-Mail|"
               r"Versichertennummer|Patienten-ID|Aktenzeichen|IBAN|Begriff|Geburtsdatum|"
               r"Praxisanschrift)[^\]\n]{0,30}\]"),
]


@dataclass
class Rueckersetzung:
    text: str
    ersetzt: int = 0
    verbleibend: list[str] = field(default_factory=list)
    hinweise: dict[str, str] = field(default_factory=dict)  # leftover -> what is wrong with it


def _person_aus(info: dict[str, Any] | None) -> Person | None:
    if not info:
        return None
    return Person(
        nachname=info.get("nachname", ""),
        vorname=info.get("vorname", ""),
        geschlecht=info.get("geschlecht", ""),
        titel=info.get("titel", ""),
    )


def _ersetzer(pseudonym: str, klartext: str, person: Person | None, form: str):
    """Return (pattern, replacement function) for one pseudonym."""
    m = _PATIENT.match(pseudonym)
    if m:
        wort, b = m.groups()
        muster = re.compile(rf"\b(?:Patient(in|en)?|Pat\.)\s+{re.escape(b)}(?![\w])")

        def ersetze(t: re.Match[str]) -> str:
            if form == "anrede" and person is not None and (person.weiblich or person.maennlich):
                kasus = "obliquus" if t.group(1) == "en" else "nominativ"
                return person.anredeform(kasus)
            return klartext.split("; ")[0]

        return muster, ersetze
    m = _ANGEHOERIGE.match(pseudonym)
    if m:
        b, n = m.groups()
        return re.compile(rf"\bAngehörige[nr]?\s+{re.escape(b)}-?{n}(?!\d)"), lambda t: klartext
    m = _ZUWEISER.match(pseudonym)
    if m:
        n = m.group(1)
        return re.compile(rf"(?:\bDr\.\s*)?(?<![\w])Z{n}(?!\d)"), lambda t: klartext
    m = _KLAMMER.match(pseudonym)
    if m:
        teile = [re.escape(t) for t in m.group(1).split()]
        return re.compile(r"\[\s*" + r"\s+".join(teile) + r"\s*\]"), lambda t: klartext
    teile = [re.escape(t) for t in pseudonym.split()]
    return re.compile(r"(?<![\w])" + r"\s+".join(teile) + r"(?![\w])"), lambda t: klartext


def _abstand_eins(a: str, b: str) -> bool:
    """One substitution or one transposition of neighbours apart."""
    if len(a) != len(b):
        return False
    diff = [i for i, (x, y) in enumerate(zip(a, b, strict=True)) if x != y]
    if len(diff) == 1:
        return True
    return len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] \
        and a[diff[1]] == b[diff[0]]


def rueckersetzen(
    text: str,
    zuordnung: dict[str, str],
    info: dict[str, Any] | None = None,
    form: str = "vollname",
    bekannte_kennungen: list[str] | None = None,
) -> Rueckersetzung:
    """Replace every known pseudonym in ``text`` by its clear data.

    ``form`` is "vollname" (always the full name) or "anrede" ("Herr/Herrn/Frau
    Nachname" by grammatical case when the gender is known).  Pseudonyms that
    look like ours but are not in the mapping are reported in ``verbleibend``;
    for hash codes the check character tells whether the model mangled the
    code, and a stored code one edit away is suggested.
    """
    person = _person_aus(info)
    ersetzt = 0
    for pseudonym in sorted(zuordnung, key=len, reverse=True):
        muster, ersetze = _ersetzer(pseudonym, zuordnung[pseudonym], person, form)
        text, n = muster.subn(ersetze, text)
        ersetzt += n
    verbleibend: list[str] = []
    hinweise: dict[str, str] = {}
    for muster in _REST_MUSTER:
        for m in muster.finditer(text):
            rest = m.group(0)
            if rest in verbleibend:
                continue
            verbleibend.append(rest)
            code = rest.strip("[]").split()[-1].split("-")[0]
            if ist_hashkennung(code):
                aehnlich = [k for k in (bekannte_kennungen or []) if _abstand_eins(code, k)]
                if not kennung_gueltig(code):
                    hinweis = "Prüfzeichen falsch, vermutlich verschrieben"
                    if len(aehnlich) == 1:
                        hinweis += f"; gemeint wohl {aehnlich[0]}"
                    hinweise[rest] = hinweis
                elif len(aehnlich) == 1:
                    hinweise[rest] = f"unbekannt; ähnlich: {aehnlich[0]}"
    return Rueckersetzung(text=text, ersetzt=ersetzt, verbleibend=verbleibend, hinweise=hinweise)
