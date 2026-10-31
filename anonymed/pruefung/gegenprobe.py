"""Re-check the anonymised document with the same rules that produced it.

Any hit here means the export must not be released.  OCR of the result
(for rasterised output and scans) follows in milestone M3.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

import pymupdf

from anonymed.dokument.textebene import lese_dokument
from anonymed.erkennung.deterministisch import Regel, finde, finde_im_text
from anonymed.erkennung.varianten import exakt_muster
from anonymed.kern.modell import Gegenprobe
from anonymed.schwaerzen.pdf import ERLAUBTE_METADATEN, VERMERK_PRAEFIX


def toleranzschluessel(regel: str, text: str) -> tuple[str, str]:
    """Key of an occurrence the user deliberately kept: rule and normalised text."""
    return regel, " ".join(text.split()).casefold()


def pseudonymspannen(text: str, pseudonyme: Iterable[str]) -> list[tuple[int, int]]:
    """Where the inserted pseudonyms sit in ``text``; rules must not fire inside them
    (a code such as "KAZ2Q4W" could otherwise look like a file number)."""
    spannen: list[tuple[int, int]] = []
    for p in pseudonyme:
        if not p:
            continue
        start = 0
        while True:
            i = text.find(p, start)
            if i < 0:
                break
            spannen.append((i, i + len(p)))
            start = i + 1
    return spannen


def in_pseudonym(a: int, b: int, spannen: list[tuple[int, int]]) -> bool:
    return any(a < y and b > x for x, y in spannen)


_CODE = re.compile(r"\b[A-Z2-9]{6}(?:-\d+)?\b")


def pseudonymspannen_tolerant(text: str, pseudonyme: Iterable[str]) -> list[tuple[int, int]]:
    """Like :func:`pseudonymspannen`, for text that OCR read back: the codes inside the
    pseudonyms are also matched with the usual OCR confusions and regardless of the
    brackets and words around them, so that a re-read "(Ort AZT63Z)" still counts as
    the pseudonym and not as a file number."""
    pseudonyme = [p for p in pseudonyme if p]
    spannen = pseudonymspannen(text, pseudonyme)
    codes = {m.group(0) for p in pseudonyme for m in _CODE.finditer(p)}
    for code in codes:
        muster = exakt_muster(code)
        if muster:
            spannen += [(m.start(), m.end()) for m in re.finditer(muster, text)]
    return spannen


def pruefe(
    doc: pymupdf.Document, regeln: list[Regel], zusatztexte: Iterable[str] = (),
    toleriert: Iterable[tuple[str, str]] = (), pseudonyme: Iterable[str] = (),
) -> Gegenprobe:
    """Re-run the rules on the output.  ``toleriert`` lists occurrences (rule, text)
    the user decided to keep; ``pseudonyme`` are the strings the redaction inserted,
    inside which no rule counts."""
    ergebnis = Gegenprobe()
    gesehen: set[tuple[int, str, str]] = set()
    toleriert = set(toleriert)
    pseudonyme = [p for p in pseudonyme if p]

    def erlaubt(regel: str, text: str) -> bool:
        return toleranzschluessel(regel, text) in toleriert

    for seite in lese_dokument(doc):
        spannen = pseudonymspannen(seite.text, pseudonyme)
        for f in finde(seite, regeln):
            gesehen.add((f.seite, f.regel, f.text))
            if in_pseudonym(f.start, f.ende, spannen) or erlaubt(f.regel, f.text):
                continue
            ergebnis.resttreffer.append(f.ohne_klartext())

    for i, page in enumerate(doc):
        text = page.get_text("text")
        spannen = pseudonymspannen(text, pseudonyme)
        for a, b, regel in finde_im_text(text, regeln):
            schluessel = (i, regel.id, text[a:b])
            if schluessel in gesehen or erlaubt(regel.id, text[a:b]) \
                    or in_pseudonym(a, b, spannen):
                continue
            gesehen.add(schluessel)
            ergebnis.resttreffer.append(
                {"seite": i + 1, "art": str(regel.art), "regel": regel.id,
                 "pseudonym": regel.pseudonym, "rects": [], "quelle": "plaintext"}
            )

    for n, text in enumerate(zusatztexte, 1):
        spannen = pseudonymspannen(text, pseudonyme)
        for a, b, regel in finde_im_text(text, regeln):
            if erlaubt(regel.id, text[a:b]) or in_pseudonym(a, b, spannen):
                continue
            ergebnis.resttreffer.append(
                {"seite": 0, "art": str(regel.art), "regel": regel.id,
                 "pseudonym": regel.pseudonym, "rects": [], "quelle": f"zusatztext{n}"}
            )

    for k, wert in doc.metadata.items():
        if k in ("format", "encryption") or not wert:
            continue
        if ERLAUBTE_METADATEN.get(k) == wert:
            continue
        if k == "subject" and wert.startswith(VERMERK_PRAEFIX):
            continue
        ergebnis.metadaten_leer = False
        ergebnis.anmerkungen.append(f"Metadatum '{k}' ist nicht leer")
    if doc.xref_xml_metadata() > 0:
        ergebnis.metadaten_leer = False
        ergebnis.anmerkungen.append("XMP-Metadaten vorhanden")

    if doc.embfile_count() > 0:
        ergebnis.struktur_sauber = False
        ergebnis.anmerkungen.append(f"{doc.embfile_count()} eingebettete Datei(en)")
    if doc.get_toc():
        ergebnis.struktur_sauber = False
        ergebnis.anmerkungen.append("Lesezeichen vorhanden")
    annots = sum(len(list(p.annots())) for p in doc)
    widgets = sum(len(list(p.widgets())) for p in doc)
    if annots:
        ergebnis.struktur_sauber = False
        ergebnis.anmerkungen.append(f"{annots} Anmerkung(en) vorhanden")
    if widgets:
        ergebnis.struktur_sauber = False
        ergebnis.anmerkungen.append(f"{widgets} Formularfeld(er) vorhanden")
    return ergebnis
