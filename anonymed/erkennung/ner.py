"""Named-entity recognition (optional extra ``ner``): unknown people and places.

The deterministic rules only know the identifiers in the patient record.  A
relative, a colleague or a town that nobody entered slips through them.  A
spaCy model trained on German news tags person and place names in free
text; those hits get their own reproducible pseudonyms ("Person K7Q4MX",
"[Ort 2F7QV4]").  NER guesses, so it is off by default, never runs in the
re-check, and medical eponyms (Alzheimer, Parkinson, Babinski ...) are
excluded; everything it finds is shown in the review step for a decision.

Install: ``uv sync --extra ner`` and a model, for example
``uv pip install https://github.com/explosion/spacy-models/releases/download/de_core_news_md-3.8.0/de_core_news_md-3.8.0-py3-none-any.whl``.
"""

from __future__ import annotations

import importlib.util
import re
from typing import TYPE_CHECKING

from anonymed.dokument.textebene import Seitentext
from anonymed.kern.modell import Art, Fundstelle
from anonymed.pseudonyme.vergabe import Pseudonymvergabe

if TYPE_CHECKING:
    from anonymed.erkennung.fachbegriffe import Fachbegriffe

MODELLE = ("de_core_news_lg", "de_core_news_md", "de_core_news_sm")
_TITEL = {"dr", "prof", "med", "dent", "phil", "pd", "dipl", "univ", "herr", "herrn", "frau",
          "fr", "hr", "mr", "mrs", "ms", "kollege", "kollegin", "ehefrau", "ehemann", "gattin",
          "gatte", "tochter", "sohn", "mutter", "vater", "schwester", "bruder", "partner",
          "partnerin", "betreuer", "betreuerin", "patient", "patientin", "pat"}
# Diseases, signs, scores and syndromes named after people: not patients.
EPONYME = {
    "alzheimer", "parkinson", "huntington", "crohn", "hodgkin", "basedow", "hashimoto", "wilson",
    "bechterew", "dupuytren", "addison", "cushing", "down", "asperger", "tourette", "korsakow",
    "wernicke", "broca", "babinski", "romberg", "lasègue", "lasegue", "glasgow", "barthel",
    "hamilton", "beck", "epworth", "apgar", "rankin", "trendelenburg", "kernig", "brudzinski",
    "hoffmann", "tinel", "phalen", "schober", "lhermitte", "charcot", "guillain", "barré", "barre",
    "bell", "horner", "menière", "ménière", "meniere", "raynaud", "sjögren", "sjogren", "behçet",
    "behcet", "kaposi", "burkitt", "wegener", "takayasu", "paget", "perthes", "scheuermann",
    "osgood", "schlatter", "marfan", "ehlers", "danlos", "turner", "klinefelter", "prader",
    "willi", "angelman", "rett", "lewy", "pick", "binswanger", "creutzfeldt", "jakob",
    "gerstmann", "landau", "kleffner", "west", "lennox", "gastaut", "dravet", "rolando",
    "jackson", "todd", "wallenberg", "weber", "millard", "gubler", "foville", "brown", "séquard",
    "sequard", "adie", "argyll", "robertson", "marcus", "gunn", "uhthoff", "hallervorden",
    "spatz", "fahr", "steele", "richardson", "olszewski", "shy", "drager", "meige", "sydenham",
    "friedreich", "duchenne", "becker", "erb", "lambert", "eaton", "miller", "fisher",
    "morbus", "syndrom", "zeichen", "test", "score", "skala",
}


def verfuegbares_modell() -> str | None:
    for m in MODELLE:
        if importlib.util.find_spec(m) is not None:
            return m
    return None


def verfuegbar() -> bool:
    return importlib.util.find_spec("spacy") is not None and verfuegbares_modell() is not None


class NER:
    """A loaded spaCy pipeline reduced to tokenisation and entity recognition."""

    def __init__(self, modell: str | None = None) -> None:
        try:
            import spacy
        except ImportError as e:
            raise RuntimeError("NER braucht das Extra 'ner' (spaCy).") from e
        name = modell or verfuegbares_modell()
        if not name:
            raise RuntimeError("Kein deutsches spaCy-Modell installiert (de_core_news_md).")
        self.name = name
        # the morphologizer stays: its part of speech tells a sentence-initial verb
        # ("Überwiesen hat Sie …") from a place name
        self.nlp = spacy.load(name, exclude=["parser", "lemmatizer", "attribute_ruler", "tagger"])
        self.nlp.max_length = 2_000_000

    def entitaeten(self, text: str) -> list[tuple[int, int, str, str]]:
        """Entities of the model minus the noise German clinical prose produces.

        Dropped: heads tagged as verbs or function words; ordinary words that are
        only capitalised because they start a sentence ("Überwiesen hat Sie …", known
        lower-case but not capitalised); single words the tagger calls common nouns
        ("Konzentration: gut"); and single words the model has never seen (compound
        nouns such as "Nierenlager", "Strichgang") unless the context says person or
        place: a salutation or title before a person, a preposition before a place.
        A lone unknown surname without such context is left to the rules, which
        know the patient's names anyway.
        """
        vokabular = self.nlp.vocab
        ergebnis = []
        for e in self.nlp(text).ents:
            kopf = e.root
            if kopf.pos_ in _KEIN_NAME:
                continue
            bekannt_gross = vokabular.has_vector(kopf.text)
            bekannt_klein = vokabular.has_vector(kopf.text.lower())
            if kopf.text[:1].isupper() and not bekannt_gross and bekannt_klein:
                continue
            if len(e) == 1 and e.label_ in ("PER", "LOC", "GPE"):
                if kopf.pos_ == "NOUN":
                    continue
                if not bekannt_gross and not bekannt_klein:
                    davor = text[max(0, e.start_char - 30):e.start_char]
                    muster = _PERSONENKONTEXT if e.label_ == "PER" else _ORTSKONTEXT
                    if not muster.search(davor):
                        continue
            ergebnis.append((e.start_char, e.end_char, e.label_, e.text))
        return ergebnis


_KEIN_NAME = {"VERB", "AUX", "ADJ", "ADV", "ADP", "DET", "PRON", "SCONJ", "CCONJ", "PART"}
# what must stand right before a lone unknown word for it to count as a person or a place
_PERSONENKONTEXT = re.compile(
    r"(?:\b(?:Herrn?|Frau|Hr\.|Fr\.|Dr\.|Prof\.|PD|med\.|Dipl\.|Patient(?:in)?|Pat\.|Kolleg(?:e|in)"
    r"|Schwester|Pfleger(?:in)?|Ehemann|Ehefrau|Mutter|Vater|Tochter|Sohn|Bruder|Schwester"
    r"|Betreuer(?:in)?|Familie|Fam\.)\s*$)|(?:[A-ZÄÖÜ][a-zäöüß]+\s+$)", re.IGNORECASE)
_ORTSKONTEXT = re.compile(r"\b(?:in|aus|nach|bei|von|wohnhaft in|Ort|Wohnort|Stadt|Gemeinde|PLZ"
                          r"|\d{5})\s*$", re.IGNORECASE)


def _personenkern(text: str) -> str:
    tokens = [t for t in re.split(r"\s+", text.strip(" .,;:-–()")) if t]
    while tokens and tokens[0].rstrip(".").lower() in _TITEL:
        tokens.pop(0)
    return " ".join(tokens)


def _plausibler_name(kern: str) -> bool:
    """Capitalised tokens, and not just an eponym: "Erika Hoffmann" is a person,
    "Hoffmann", "Morbus Parkinson" and "Parkinson-Syndrom" are not."""
    tokens = kern.split()
    if not tokens or len(kern) < 3:
        return False
    woerter = [w.lower() for t in tokens for w in re.split(r"[-/]", t.strip(".,")) if w]
    if woerter and all(w in EPONYME for w in woerter):
        return False
    buchstaben = [t for t in tokens if t[:1].isalpha()]
    return bool(buchstaben) and all(t[:1].isupper() for t in buchstaben)


_TEILNAMEN = (Art.NACHNAME, Art.VORNAME, Art.GEBURTSNAME)


def finde_ner(
    seite: Seitentext, ner: NER, vergabe: Pseudonymvergabe, vorhandene: list[Fundstelle],
    quelle: str = "text", fachbegriffe: Fachbegriffe | None = None,
    verschont: list[str] | None = None,
) -> list[Fundstelle]:
    """People and places the rules did not know.

    Entities made up of medical terms of ``fachbegriffe`` are left alone (and
    listed in ``verschont`` when given): "Morbus Wilson" or "Babinski" is not
    a patient, "Erika Hoffmann" still is although Hoffmann names a sign.
    A hit that only overlaps existing hits is dropped,
    with one exception: when the entity completely contains hits on a lone
    surname or first name (the rules saw "Flantz" in "Hildegund Flantz"), those
    partial hits are removed from ``vorhandene`` and the full name takes their
    place, because it is a different person than the one the rules assumed.
    """
    treffer: list[Fundstelle] = []
    schutz = fachbegriffe.spannen(seite.text) if fachbegriffe is not None else []
    for start, ende, label, text in ner.entitaeten(seite.text):
        if label == "PER":
            kern = _personenkern(text)
            if not _plausibler_name(kern):
                continue
            pseudonym, art, regel = vergabe.person_unbekannt(kern), Art.NAME, "ner.person"
        elif label in ("LOC", "GPE"):
            kern = text.strip(" .,;:-–()")
            if len(kern) < 3 or kern.lower() in EPONYME or not kern[:1].isupper():
                continue
            pseudonym, art, regel = vergabe.ort_unbekannt(kern), Art.ORT, "ner.ort"
        else:
            continue
        a = seite.text.find(kern, start, ende + 1)
        if a < 0:
            continue
        b = a + len(kern)
        if fachbegriffe is not None and fachbegriffe.geschuetzt(seite.text, a, b, schutz,
                                                                ganz=True):
            if verschont is not None:
                verschont.append(kern)
            continue
        ueberlappend = [f for f in vorhandene + treffer
                        if f.ende > f.start and a < f.ende and b > f.start]
        ersetzbar = [f for f in ueberlappend if f in vorhandene and f.art in _TEILNAMEN
                     and a <= f.start and f.ende <= b and (f.ende - f.start) < (b - a)]
        if len(ersetzbar) != len(ueberlappend):
            continue
        for f in ersetzbar:
            vorhandene.remove(f)
        treffer.append(Fundstelle(
            seite=seite.nummer, start=a, ende=b, text=kern, art=art, regel=regel,
            pseudonym=pseudonym, rects=[tuple(r) for r in seite.rects(a, b)],
            schriftgroesse=seite.schriftgroesse(a, b), quelle=quelle,
        ))
    return treffer
