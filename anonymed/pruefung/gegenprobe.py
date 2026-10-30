"""Re-check the anonymised document with the same rules that produced it.

Any hit here means the export must not be released.  OCR of the result
(for rasterised output and scans) follows in milestone M3.
"""

from __future__ import annotations

from collections.abc import Iterable

import pymupdf

from anonymed.dokument.textebene import lese_dokument
from anonymed.erkennung.deterministisch import Regel, finde, finde_im_text
from anonymed.kern.modell import Gegenprobe
from anonymed.schwaerzen.pdf import ERLAUBTE_METADATEN, VERMERK_PRAEFIX


def toleranzschluessel(regel: str, text: str) -> tuple[str, str]:
    """Key of an occurrence the user deliberately kept: rule and normalised text."""
    return regel, " ".join(text.split()).casefold()


def pruefe(
    doc: pymupdf.Document, regeln: list[Regel], zusatztexte: Iterable[str] = (),
    toleriert: Iterable[tuple[str, str]] = (),
) -> Gegenprobe:
    """Re-run the rules on the output.  ``toleriert`` lists occurrences (rule, text)
    the user decided to keep; they do not count as leftovers."""
    ergebnis = Gegenprobe()
    gesehen: set[tuple[int, str, str]] = set()
    toleriert = set(toleriert)

    def erlaubt(regel: str, text: str) -> bool:
        return toleranzschluessel(regel, text) in toleriert

    for seite in lese_dokument(doc):
        for f in finde(seite, regeln):
            gesehen.add((f.seite, f.regel, f.text))
            if not erlaubt(f.regel, f.text):
                ergebnis.resttreffer.append(f.ohne_klartext())

    for i, page in enumerate(doc):
        text = page.get_text("text")
        for a, b, regel in finde_im_text(text, regeln):
            schluessel = (i, regel.id, text[a:b])
            if schluessel in gesehen or erlaubt(regel.id, text[a:b]):
                continue
            gesehen.add(schluessel)
            ergebnis.resttreffer.append(
                {"seite": i + 1, "art": str(regel.art), "regel": regel.id,
                 "pseudonym": regel.pseudonym, "rects": [], "quelle": "plaintext"}
            )

    for n, text in enumerate(zusatztexte, 1):
        for a, b, regel in finde_im_text(text, regeln):
            if erlaubt(regel.id, text[a:b]):
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
