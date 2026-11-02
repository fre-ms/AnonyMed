"""Medical vocabulary that must never be redacted by guesswork.

Before the NER model and the fuzzy OCR search may touch a word, the page is
scanned for medical terms: diseases, symptoms, anatomy, drugs, procedures,
scores, eponyms.  Spans covered by such a term are protected from those two
heuristics (the deterministic rules for the patient's own identifiers are
not affected, a patient really called "Herz" is still redacted).

The terms live in the ``vokabular`` table of the pseudonym database
(:mod:`anonymed.erkennung.vokabular`): the bundled lists (German labels and aliases
from Wikidata for items with MeSH, ICD-10, ATC or INN identifiers, CC0; body parts;
findings terms; the eponyms of :mod:`anonymed.erkennung.ner`) are loaded into it on
first use, and the user adds, deletes and imports terms there (vocabulary step,
``anonymed fachbegriffe``).  :func:`laden` still reads the bundled files directly, as
the fallback when the database cannot be opened.

Matching is case-insensitive, tolerates German inflection (-s, -n, -en, -e,
-es, -er, -em, -ern), hyphenated compounds ("Parkinson-Syndrom") and umlaut
transliteration; multi-word terms are matched as a whole.
"""

from __future__ import annotations

import csv
import gzip
import os
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from platformdirs import user_data_path

BASIS = Path(__file__).resolve().parent.parent / "daten" / "fachbegriffe_de.txt.gz"
KOERPERTEILE = Path(__file__).resolve().parent.parent / "daten" / "koerperteile_de.txt"
BEFUNDBEGRIFFE = Path(__file__).resolve().parent.parent / "daten" / "befundbegriffe_de.txt"
_TOKEN = re.compile(r"[^\W\d_][\w\-]*|\d+[\w\-]*")
_SUFFIXE = ("ern", "en", "es", "er", "em", "e", "n", "s")
_TRANS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
_KLAMMER = re.compile(r"\s*\([^)]*\)\s*$")
MAX_WOERTER = 5
# Head nouns after which the first part of a hyphenated term is an eponym that appears on
# its own in reports ("Babinski-Zeichen" -> "Babinski positiv").  Anatomy and chemistry heads
# are deliberately absent: "Müller-Gang" or "Berlin-Blau" must not protect the surname Müller
# or the town Berlin from the name recognition.
_EPONYM_KOEPFE = {
    "zeichen", "syndrom", "test", "probe", "manöver", "manoever", "reflex", "phänomen",
    "phaenomen", "skala", "score", "index", "trias", "kriterien", "klassifikation", "lagerung",
    "griff", "punkt", "position", "stadium", "formel", "gleichung", "koeffizient", "regel",
    "gesetz", "effekt", "reaktion", "methode", "verfahren", "technik", "operation", "naht",
    "schnitt", "zugang",
}


def datenordner() -> Path:
    ordner = os.environ.get("ANONYMED_DATENORDNER")
    basis = Path(ordner) if ordner else user_data_path("AnonyMed", appauthor=False)
    return basis / "fachbegriffe"


def _norm(wort: str) -> str:
    return wort.casefold().strip(" .,;:!?\"'()[]")


def _formen(begriff: str) -> set[str]:
    n = _norm(begriff)
    return {n, n.translate(_TRANS)} - {""}


@dataclass
class Fachbegriffe:
    """The merged vocabulary with its matcher."""

    einzel: set[str] = field(default_factory=set)
    mehrwort: dict[str, set[tuple[str, ...]]] = field(default_factory=dict)
    quellen: list[tuple[str, int]] = field(default_factory=list)

    @property
    def anzahl(self) -> int:
        return len(self.einzel) + sum(len(v) for v in self.mehrwort.values())

    def fuege_hinzu(self, begriffe: Iterable[str], quelle: str = "") -> int:
        n = 0
        for roh in begriffe:
            roh = _KLAMMER.sub("", roh.strip())
            if len(roh) < 3 or roh.startswith("#"):
                continue
            for form in _formen(roh):
                tokens = tuple(t for t in re.split(r"[\s/]+", form) if t)
                if not tokens:
                    continue
                if len(tokens) == 1:
                    self.einzel.add(tokens[0])
                    teile = tokens[0].split("-")
                    if len(teile) == 2 and teile[1] in _EPONYM_KOEPFE and len(teile[0]) >= 4:
                        # "Babinski-Zeichen" protects "Babinski" too; "Berlin-Blau" does not,
                        # nor does "Konigsmark-Hollander-Berlin-Syndrom" (the town matters more)
                        self.einzel.add(teile[0])
                elif len(tokens) <= MAX_WOERTER:
                    # whole phrases only: parts of "Berliner Blau" must not protect "Berlin"
                    self.mehrwort.setdefault(tokens[0], set()).add(tokens)
            n += 1
        if quelle:
            self.quellen.append((quelle, n))
        return n

    def ist_fachbegriff(self, wort: str) -> bool:
        """Single word, inflected forms and hyphen parts included."""
        for form in _formen(wort):
            if not form:
                continue
            if form in self.einzel:
                return True
            for suffix in _SUFFIXE:
                if form.endswith(suffix) and len(form) - len(suffix) >= 4 \
                        and form[: -len(suffix)] in self.einzel:
                    return True
            if "-" in form and any(len(t) >= 3 and (t in self.einzel or self._gestammt(t))
                                   for t in form.split("-")):
                return True
            if form.endswith("-") and form[:-1] in self.einzel:
                return True
        return False

    def _gestammt(self, t: str) -> bool:
        return any(t.endswith(s) and len(t) - len(s) >= 4 and t[: -len(s)] in self.einzel
                   for s in _SUFFIXE)

    def spannen(self, text: str) -> list[tuple[int, int]]:
        """Character spans of medical terms in ``text`` (multi-word first, then single)."""
        tokens = [(m.start(), m.end(), m.group(0)) for m in _TOKEN.finditer(text)]
        ergebnis: list[tuple[int, int]] = []
        belegt: list[bool] = [False] * len(tokens)
        normen = [_norm(t[2]) for t in tokens]
        for i, (a, _b, _w) in enumerate(tokens):
            kandidaten = self.mehrwort.get(normen[i]) or self.mehrwort.get(
                normen[i].translate(_TRANS))
            if not kandidaten:
                continue
            for folge in sorted(kandidaten, key=len, reverse=True):
                n = len(folge)
                if i + n <= len(tokens) and all(
                        normen[i + k] == folge[k] or normen[i + k].translate(_TRANS) == folge[k]
                        for k in range(n)):
                    ergebnis.append((a, tokens[i + n - 1][1]))
                    for k in range(n):
                        belegt[i + k] = True
                    break
        for i, (a, b, w) in enumerate(tokens):
            if belegt[i] or not self.ist_fachbegriff(w):
                continue
            if len(w) <= 3 and not w[:1].isupper():
                continue  # "hat" is a verb here; "Ohr", "Arm", "HAT" are terms
            ergebnis.append((a, b))
        ergebnis.sort()
        return ergebnis

    def geschuetzt(self, text: str, a: int, b: int, spannen: list[tuple[int, int]] | None = None,
                   ganz: bool = False) -> bool:
        """Does ``text[a:b]`` touch a protected term (or, with ``ganz``, consist of them)?"""
        spannen = self.spannen(text) if spannen is None else spannen
        beruehrt = [s for s in spannen if a < s[1] and b > s[0]]
        if not beruehrt:
            return False
        if not ganz:
            return True
        # every token of the range must lie inside a protected span (phrases count as a whole)
        for m in _TOKEN.finditer(text, a, b):
            if not any(s[0] <= m.start() and m.end() <= s[1] for s in beruehrt):
                return False
        return True


# -- loading ---------------------------------------------------------------------------------------
def _lies_liste(pfad: Path) -> Iterator[str]:
    oeffnen = gzip.open if pfad.suffix == ".gz" else open
    with oeffnen(pfad, "rt", encoding="utf-8", errors="replace") as f:
        for zeile in f:
            zeile = zeile.strip()
            if zeile and not zeile.startswith("#"):
                yield zeile


def laden(zusatz: Iterable[Path] = (), mit_basis: bool = True,
          mit_nutzerdaten: bool = True) -> Fachbegriffe:
    from anonymed.erkennung.ner import EPONYME

    fb = Fachbegriffe()
    if mit_basis and BASIS.exists():
        fb.fuege_hinzu(_lies_liste(BASIS), f"Basis ({BASIS.name})")
    if mit_basis and KOERPERTEILE.exists():
        fb.fuege_hinzu(_lies_liste(KOERPERTEILE), "Körperteile")
    if mit_basis and BEFUNDBEGRIFFE.exists():
        fb.fuege_hinzu(_lies_liste(BEFUNDBEGRIFFE), "Befundbegriffe")
    fb.fuege_hinzu((e for e in EPONYME if len(e) >= 4), "Eponyme")
    pfade = list(zusatz)
    if mit_nutzerdaten and datenordner().is_dir():
        pfade += sorted(p for p in datenordner().iterdir()
                        if p.suffix in (".txt", ".gz") and p.is_file())
    for p in pfade:
        fb.fuege_hinzu(_lies_liste(p), p.name)
    return fb


_cache: tuple[Path, int, Fachbegriffe] | None = None


def standard() -> Fachbegriffe:
    """The vocabulary used by the pipeline: built from the database table, rebuilt only
    when the table's version changed; the bundled files when the database is unusable."""
    global _cache
    import sqlite3

    from anonymed.erkennung import vokabular as vokmodul

    try:
        pfad = vokmodul.standardpfad()
        with vokmodul.Vokabular(pfad) as v:
            version = v.version()
            if _cache is not None and _cache[0] == pfad and _cache[1] == version:
                return _cache[2]
            fb = Fachbegriffe()
            for kategorie, begriffe in v.je_kategorie().items():
                fb.fuege_hinzu(begriffe, kategorie)
    except (sqlite3.Error, OSError):
        return laden()
    _cache = (pfad, version, fb)
    return fb


def _cache_clear() -> None:
    global _cache
    _cache = None


standard.cache_clear = _cache_clear  # type: ignore[attr-defined]


# -- import ----------------------------------------------------------------------------------
def begriffe_aus_mesh_xml(pfad: Path) -> Iterator[str]:
    """Descriptor names and entry terms of a MeSH XML file (NLM or a translation)."""
    for _ereignis, element in ET.iterparse(pfad, events=("end",)):
        if element.tag in ("DescriptorRecord", "SupplementalRecord"):
            for s in element.iter("String"):
                if s.text and s.text.strip():
                    yield s.text.strip()
            element.clear()


def begriffe_aus_csv(pfad: Path, spalte: int, trennzeichen: str = ";") -> Iterator[str]:
    """One column of a CSV file, for ICD-10-GM, ATC or any other table."""
    with pfad.open(encoding="utf-8", errors="replace", newline="") as f:
        for zeile in csv.reader(f, delimiter=trennzeichen):
            if len(zeile) > spalte and zeile[spalte].strip():
                yield zeile[spalte].strip()


NUTZERLISTE = "nutzervokabular"


def ergaenze(pfad: Path, name: str = NUTZERLISTE) -> tuple[Path, int]:
    """Merge the terms of ``pfad`` (one per line or CSV) into the vocabulary table under
    the category ``name`` ("nutzervokabular" = Nutzervokabular); returns the database
    and how many were new.  This is what the exported ``.vokabular.txt`` files are for:
    the vocabulary grows with every review."""
    from anonymed.erkennung import vokabular as vokmodul

    kategorie = vokmodul.KATEGORIE_NUTZER if name == NUTZERLISTE else name
    with vokmodul.Vokabular() as v:
        neu, _gelesen = v.importiere_csv(pfad, kategorie)
        ziel = v.pfad
    standard.cache_clear()
    return ziel, neu


def importiere(pfad: Path, name: str, format_: str = "liste", spalte: int = 0,
               trennzeichen: str = ";") -> tuple[Path, int]:
    """Add the terms of ``pfad`` to the vocabulary table under the category ``name``;
    returns the database and how many were new."""
    if format_ == "mesh-xml":
        begriffe = begriffe_aus_mesh_xml(pfad)
    elif format_ == "csv":
        begriffe = begriffe_aus_csv(pfad, spalte, trennzeichen)
    elif format_ == "liste":
        begriffe = _lies_liste(pfad)
    else:
        raise ValueError(f"Unbekanntes Format: {format_!r} (liste, csv, mesh-xml)")
    from anonymed.erkennung import vokabular as vokmodul

    with vokmodul.Vokabular() as v:
        neu = v.fuege_hinzu(begriffe, name)
        ziel = v.pfad
    standard.cache_clear()
    return ziel, neu
