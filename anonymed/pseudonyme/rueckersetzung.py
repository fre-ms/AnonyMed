"""Put the clear data back into text a language model produced from the
pseudonymised document."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from anonymed.kern.modell import Person

_PATIENT = re.compile(r"^(Patient|Patientin) ([A-Z]{1,3})$")
_ANGEHOERIGE = re.compile(r"^Angehörige ([A-Z]{1,3})(\d+)$")
_ZUWEISER = re.compile(r"^Dr\. Z(\d+)$")
_KLAMMER = re.compile(r"^\[(.+)\]$")

_REST_MUSTER = [
    re.compile(r"\bPatient(?:in|en)?\s+[A-Z]{1,3}(?![\w])"),
    re.compile(r"\bAngehörige[nr]?\s+[A-Z]{1,3}\d+(?!\d)"),
    re.compile(r"\bDr\.\s*Z\d+(?!\d)"),
    re.compile(r"\[(?:geb\.|Alter|Wohnort|Telefon|E-Mail|Versichertennummer|Patienten-ID|"
               r"Aktenzeichen|IBAN|Begriff|Geburtsdatum|Praxisanschrift)[^\]\n]{0,30}\]"),
]


@dataclass
class Rueckersetzung:
    text: str
    ersetzt: int = 0
    verbleibend: list[str] = field(default_factory=list)


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
        return re.compile(rf"\bAngehörige[nr]?\s+{b}{n}(?!\d)"), lambda t: klartext
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


def rueckersetzen(
    text: str,
    zuordnung: dict[str, str],
    info: dict[str, Any] | None = None,
    form: str = "vollname",
) -> Rueckersetzung:
    """Replace every known pseudonym in ``text`` by its clear data.

    ``form`` is "vollname" (always the full name) or "anrede" ("Herr/Herrn/Frau
    Nachname" by grammatical case when the gender is known).  Pseudonyms that
    look like ours but are not in the mapping are reported in ``verbleibend``.
    """
    person = _person_aus(info)
    ersetzt = 0
    for pseudonym in sorted(zuordnung, key=len, reverse=True):
        muster, ersetze = _ersetzer(pseudonym, zuordnung[pseudonym], person, form)
        text, n = muster.subn(ersetze, text)
        ersetzt += n
    verbleibend: list[str] = []
    for muster in _REST_MUSTER:
        for m in muster.finditer(text):
            if m.group(0) not in verbleibend:
                verbleibend.append(m.group(0))
    return Rueckersetzung(text=text, ersetzt=ersetzt, verbleibend=verbleibend)
