"""Fuzzy token matching for OCR text.

OCR misreads single characters ("Jull" for "Juli", "Fiantz" for "Flantz"),
which defeats exact patterns.  For pages read by OCR every identifier is also
searched as a sequence of tokens with a small Levenshtein tolerance that grows
which defeats exact patterns.  For pages read by OCR every identifier is also
searched as a sequence of tokens with a small Levenshtein tolerance that grows
with the token length (1 for 4–6 characters, 2 up to 11, 3 up to 19, 4 beyond).
a misread date of birth visible is not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pseudonymed.dokument.textebene import Seitentext
from pseudonymed.erkennung.varianten import ohne_akzente
from pseudonymed.kern.modell import Art, Fundstelle, Patient, Person, Rolle
from pseudonymed.pseudonyme.vergabe import Pseudonymvergabe

if TYPE_CHECKING:
    from pseudonymed.erkennung.fachbegriffe import Fachbegriffe

_MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September",
           "Oktober", "November", "Dezember"]
_RAND = ",.;:()*\"'[]«»"
_TOKEN = re.compile(r"\S+")
_TRANS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


@dataclass
class Ziel:
    tokens: list[str]
    pseudonym: str
    art: Art
    regel: str
    rolle: Rolle | None = None


def _kern(token: str) -> str:
    return token.strip(_RAND).casefold()


def _formen(token: str) -> set[str]:
    k = _kern(token)
    return {k, k.translate(_TRANS), ohne_akzente(k)} - {""}


def toleranz(token: str) -> int:
    """Allowed edits by token length: 0 below 4, 1 up to 6, 2 up to 11, 3 up to 19, else 4."""
    n = len(_kern(token))
    if n < 4:
        return 0
    if n < 7:
        return 1
    if n < 12:
        return 2
    return 3 if n < 20 else 4


def distanz(a: str, b: str, maximum: int) -> int:
    """Levenshtein distance, stopping early once it exceeds ``maximum``."""
    if abs(len(a) - len(b)) > maximum:
        return maximum + 1
    vorher = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        aktuell = [i]
        bestes = i
        for j, cb in enumerate(b, 1):
            wert = min(vorher[j] + 1, aktuell[j - 1] + 1, vorher[j - 1] + (ca != cb))
            aktuell.append(wert)
            bestes = min(bestes, wert)
        if bestes > maximum:
            return maximum + 1
        vorher = aktuell
    return vorher[-1]


def _passt(ziel_token: str, ocr_token: str) -> bool:
    t = toleranz(ziel_token)
    ziel_formen = _formen(ziel_token)
    ocr_formen = _formen(ocr_token)
    if not ziel_formen or not ocr_formen:
        return False
    return any(distanz(z, o, t) <= t for z in ziel_formen for o in ocr_formen)


def ziele_fuer(patient: Patient, vergabe: Pseudonymvergabe) -> list[Ziel]:
    """Token sequences worth a fuzzy search, with their pseudonyms."""
    ziele: list[Ziel] = []

    def namen(prefix: str, person: Person, pseudonym: str, rolle: Rolle) -> None:
        teile = [t for t in re.split(r"[\s\-]+", person.nachname) if len(t) >= 4]
        vornamen = [v for v in person.vornamen if len(v) >= 4]
        if vornamen and person.nachname:
            ziele.append(Ziel([vornamen[0], person.nachname], pseudonym, Art.NAME,
                              f"{prefix}.vollname", rolle))
        for t in {person.nachname, *teile}:
            if len(t) >= 4:
                ziele.append(Ziel([t], pseudonym, Art.NACHNAME, f"{prefix}.nachname", rolle))
        for v in vornamen:
            ziele.append(Ziel([v], pseudonym, Art.VORNAME, f"{prefix}.vorname", rolle))
        if len(person.geburtsname) >= 4:
            ziele.append(Ziel([person.geburtsname], pseudonym, Art.GEBURTSNAME,
                              f"{prefix}.geburtsname", rolle))

    def adressen(prefix: str, person: Person, rolle: Rolle) -> None:
        if person.strasse:
            tokens = person.strasse.split()
            if tokens and len(tokens[0]) >= 4:
                ziele.append(Ziel(tokens, vergabe.strasse(person), Art.ADRESSE,
                                  f"{prefix}.strasse", rolle))
                ziele.append(Ziel([tokens[0]], vergabe.strasse(person), Art.ADRESSE,
                                  f"{prefix}.strasse", rolle))
        if len(person.ort) >= 4:
            ziele.append(Ziel([person.ort], vergabe.ort(person), Art.ORT, f"{prefix}.ort", rolle))
            if person.plz:
                ziele.append(Ziel([person.plz, person.ort], vergabe.plz_ort(person), Art.ORT,
                                  f"{prefix}.plz_ort", rolle))

    p = patient
    ps = vergabe.hauptperson()
    namen("name", p, ps, Rolle.PATIENT)
    adressen("adresse", p, Rolle.PATIENT)
    if p.geburtsdatum:
        d = p.geburtsdatum
        pseudo = vergabe.geburtsdatum()
        # no "%-d": that strftime flag is a glibc/BSD extension and raises on Windows
        for form in (d.strftime("%d.%m.%Y"), f"{d.day}.{d.month}.{d.year}", d.strftime("%d.%m.%y")):
            ziele.append(Ziel([form], pseudo, Art.GEBURTSDATUM, "datum.geburtsdatum",
                              Rolle.PATIENT))
        monat = _MONATE[d.month - 1]
        ziele.append(Ziel([f"{d.day}.", monat, str(d.year)], pseudo, Art.GEBURTSDATUM,
                          "datum.geburtsdatum", Rolle.PATIENT))
        ziele.append(Ziel([f"{d.day}.", monat[:3] + ".", str(d.year)], pseudo, Art.GEBURTSDATUM,
                          "datum.geburtsdatum", Rolle.PATIENT))
    if len(p.versichertennummer) >= 6:
        ziele.append(Ziel([p.versichertennummer], vergabe.versichertennummer(),
                          Art.VERSICHERTENNUMMER, "versichertennummer", Rolle.PATIENT))
    if len(p.patienten_id) >= 6:
        ziele.append(Ziel([p.patienten_id], vergabe.patienten_id(), Art.PATIENTEN_ID,
                          "patienten_id", Rolle.PATIENT))
    for e in p.emails:
        if len(e) >= 8:
            ziele.append(Ziel([e], vergabe.email(), Art.EMAIL, "email", Rolle.PATIENT))
    for a in p.aktenzeichen:
        ziele.append(Ziel(a.split(), vergabe.aktenzeichen(), Art.AKTENZEICHEN, "aktenzeichen",
                          Rolle.PATIENT))
    for i, person in enumerate(p.angehoerige, 1):
        namen(f"angehoerige{i}", person, vergabe.angehoerige(i, person), Rolle.ANGEHOERIGE)
        adressen(f"angehoerige{i}", person, Rolle.ANGEHOERIGE)
    for i, person in enumerate(p.zuweiser, 1):
        namen(f"zuweiser{i}", person, vergabe.zuweiser(i, person), Rolle.ZUWEISER)
        adressen(f"zuweiser{i}", person, Rolle.ZUWEISER)
    return ziele


_GROSS = (Art.NAME, Art.NACHNAME, Art.VORNAME, Art.GEBURTSNAME, Art.ORT, Art.ADRESSE)


def finde_unscharf(
    seite: Seitentext, ziele: list[Ziel], vorhandene: list[Fundstelle],
    fachbegriffe: Fachbegriffe | None = None, grossschreibung: bool = False,
    quelle: str = "ocr",
) -> list[Fundstelle]:
    """Fuzzy hits on an OCR page (or a transcript) that do not overlap exact hits.

    An inexact match whose text is a medical term ("Bauch" for a patient
    called Bauer) is not a hit; exact spellings still are.  With
    ``grossschreibung`` names and places must start with a capital letter,
    which keeps "lang" or "hoch" safe in correctly cased text (OCR text is
    not trusted that far).
    """
    tokens = [(m.start(), m.end(), m.group(0)) for m in _TOKEN.finditer(seite.text)]
    belegt = [(f.start, f.ende) for f in vorhandene if f.ende > f.start]
    kandidaten: list[tuple[int, int, Ziel]] = []
    schutz = fachbegriffe.spannen(seite.text) if fachbegriffe is not None else []

    def merke(i: int, j: int, ziel: Ziel) -> None:
        a, b = tokens[i][0], tokens[j][1]
        while a < b and seite.text[a] in _RAND:  # keep punctuation out of the span
            a += 1
        while b > a and seite.text[b - 1] in _RAND:
            b -= 1
        gefunden = seite.text[a:b]
        if grossschreibung and ziel.art in _GROSS and not gefunden[:1].isupper():
            return
        genau = _kern(gefunden) == _kern(" ".join(ziel.tokens))
        if fachbegriffe is not None and not genau \
                and fachbegriffe.geschuetzt(seite.text, a, b, schutz, ganz=True):
            return
        kandidaten.append((a, b, ziel))

    for ziel in ziele:
        n = len(ziel.tokens)
        if n == 0 or any(not _kern(t) for t in ziel.tokens):
            continue
        for i in range(len(tokens) - n + 1):
            if all(_passt(ziel.tokens[k], tokens[i + k][2]) for k in range(n)):
                merke(i, i + n - 1, ziel)
        if n == 1 and len(_kern(ziel.tokens[0])) >= 12:
            # OCR splits long tokens ("gotthard. flantz@example. org"): try joined neighbours
            for breite in (2, 3):
                for i in range(len(tokens) - breite + 1):
                    if any(seite.zeile_von(tokens[i][0]) != seite.zeile_von(tokens[i + k][0])
                           for k in range(1, breite)):
                        continue
                    zusammen = "".join(tokens[i + k][2] for k in range(breite))
                    if len(_kern(zusammen)) > len(_kern(ziel.tokens[0])) + 2:
                        continue  # a whole neighbouring word glued on is not a split token
                    if _passt(ziel.tokens[0], zusammen):
                        merke(i, i + breite - 1, ziel)
    kandidaten.sort(key=lambda k: (-(k[1] - k[0]), k[0]))
    treffer: list[Fundstelle] = []
    for a, b, ziel in kandidaten:
        if any(a < y and b > x for x, y in belegt):
            continue
        belegt.append((a, b))
        treffer.append(Fundstelle(
            seite=seite.nummer, start=a, ende=b, text=seite.text[a:b], art=ziel.art,
            regel=f"unscharf.{ziel.regel}", pseudonym=ziel.pseudonym,
            rects=[tuple(r) for r in seite.rects(a, b)], schriftgroesse=seite.schriftgroesse(a, b),
            rolle=ziel.rolle, quelle=quelle,
        ))
    treffer.sort(key=lambda f: f.start)
    return treffer
