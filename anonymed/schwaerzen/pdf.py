"""Redact hits in a PDF page and write the pseudonym into the gap.

MuPDF removes every character whose glyph box is *touched* by a redaction
rectangle, and glyph boxes of neighbouring lines overlap even at ordinary
line spacing.  The removal band is therefore clipped against the boxes of all
other lines before it is applied.  The pseudonym is then drawn by us on the
original baseline, widened into free space to the right where possible and
shrunk otherwise, so that it stays legible.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from anonymed import WERKZEUG
from anonymed.dokument.textebene import Seitentext, lese_seite
from anonymed.kern.modell import Fundstelle

ERLAUBTE_METADATEN = {"producer": WERKZEUG, "creator": WERKZEUG}
VERMERK_PRAEFIX = "anonymisiert mit AnonyMed"


@dataclass
class Seitenergebnis:
    """What happened to one page: number of boxes and whether the remaining
    text kept its position (MuPDF repositions glyphs after a removal; a wrong
    offset would hide or overlap neighbouring characters)."""

    anzahl: int = 0
    lage_intakt: bool = True


@dataclass
class Schwaerzoptionen:
    fuellfarbe: tuple[float, float, float] = (0.88, 0.88, 0.88)
    textfarbe: tuple[float, float, float] = (0.0, 0.0, 0.0)
    schrift: str = "helv"
    min_schrift: float = 4.0
    bilder: int = pymupdf.PDF_REDACT_IMAGE_PIXELS
    grafik: int = pymupdf.PDF_REDACT_LINE_ART_NONE
    vermerk: bool = True
    rastern: bool = False
    dpi: int = 200


def _band(rect: pymupdf.Rect, zeile: int, seite: Seitentext) -> pymupdf.Rect:
    """Shrink ``rect`` so that it touches no character of another line."""
    band = pymupdf.Rect(rect)
    mitte = (band.y0 + band.y1) / 2
    for z, box in seite.zeilenboxen.items():
        if z == zeile:
            continue
        andere = pymupdf.Rect(box)
        if not andere.intersects(band) or andere.x1 <= band.x0 or andere.x0 >= band.x1:
            continue
        andere_mitte = (andere.y0 + andere.y1) / 2
        if andere_mitte < mitte:
            band.y0 = max(band.y0, andere.y1 + 0.2)
        else:
            band.y1 = min(band.y1, andere.y0 - 0.2)
    if band.y1 - band.y0 < 1.0:  # pathological overlap: keep the middle sliver
        band.y0, band.y1 = mitte - 0.5, mitte + 0.5
    band.x0 += 0.2
    band.x1 -= 0.2
    return band


def _zeile_von_rect(seite: Seitentext, f: Fundstelle, rect: pymupdf.Rect) -> int:
    for i in range(f.start, min(f.ende, len(seite.text))):
        box = seite.boxen[i]
        if box is not None and pymupdf.Rect(box).intersects(rect):
            return seite.zeilen[i]
    return -1


def _passende_groesse(text: str, breite: float, start: float, opt: Schwaerzoptionen) -> float:
    groesse = max(start, opt.min_schrift)
    while groesse > opt.min_schrift and pymupdf.get_text_length(
        text, fontname=opt.schrift, fontsize=groesse
    ) > breite:
        groesse -= 0.5
    return groesse


def lage_intakt(
    vorher: Seitentext, nachher: Seitentext, fundstellen: list[Fundstelle], toleranz: float = 0.6
) -> bool:
    """True when every character outside the hits is still where it was."""
    entfernt: set[int] = set()
    for f in fundstellen:
        entfernt.update(range(f.start, f.ende))
    erwartet = sorted(
        ((vorher.text[i], vorher.boxen[i]) for i in range(len(vorher.text))
         if vorher.boxen[i] is not None and i not in entfernt and not vorher.text[i].isspace()),
        key=lambda e: (round(e[1][1]), e[1][0]),
    )
    ist = sorted(
        ((nachher.text[i], nachher.boxen[i]) for i in range(len(nachher.text))
         if nachher.boxen[i] is not None and not nachher.text[i].isspace()),
        key=lambda e: (round(e[1][1]), e[1][0]),
    )
    if len(erwartet) != len(ist):
        return False
    for (c1, b1), (c2, b2) in zip(erwartet, ist, strict=True):
        if c1 != c2 or abs(b1[0] - b2[0]) > toleranz or abs(b1[1] - b2[1]) > toleranz:
            return False
    return True


def _einblendung(
    page: pymupdf.Page, fuell: pymupdf.Rect, basis: float, groesse: float, pseudonym: str,
    opt: Schwaerzoptionen,
) -> None:
    page.draw_rect(fuell, color=None, fill=opt.fuellfarbe, overlay=True)
    page.insert_text(
        (fuell.x0 + 0.75, basis),
        pseudonym,
        fontsize=groesse,
        fontname=opt.schrift,
        color=opt.textfarbe,
        overlay=True,
    )


def rastere_seite(
    doc: pymupdf.Document,
    nummer: int,
    original: pymupdf.Document,
    seite: Seitentext,
    fundstellen: list[Fundstelle],
    opt: Schwaerzoptionen,
) -> None:
    """Replace page ``nummer`` of ``doc`` by an image of the *original* page with
    the hits painted over and the pseudonyms written on top.  Used when the
    text-layer redaction disturbed the layout; nothing of the text layer survives."""
    temp = pymupdf.open()
    temp.insert_pdf(original, from_page=nummer, to_page=nummer)
    tp = temp[0]
    for f in fundstellen:
        for k, roh in enumerate(f.rects):
            rect = pymupdf.Rect(roh)
            if k == 0:
                basis = seite.basislinie(f.start, f.ende) if f.quelle == "text" else None
                if basis is None:
                    basis = rect.y1 - 0.22 * rect.height
                groesse = _passende_groesse(f.pseudonym, rect.width - 1.5, f.schriftgroesse, opt)
                _einblendung(tp, rect, basis, groesse, f.pseudonym, opt)
            else:
                tp.draw_rect(rect, color=None, fill=opt.fuellfarbe, overlay=True)
    pix = tp.get_pixmap(dpi=opt.dpi, alpha=False)
    breite, hoehe = tp.rect.width, tp.rect.height
    temp.close()
    neu = doc.new_page(pno=nummer, width=breite, height=hoehe)
    neu.insert_image(neu.rect, pixmap=pix)
    doc.delete_page(nummer + 1)


OCR_RAND = 1.5  # points of padding around OCR word boxes before pixels are blanked


def schwaerze_seite(
    page: pymupdf.Page,
    seite: Seitentext | None,
    fundstellen: list[Fundstelle],
    opt: Schwaerzoptionen | None = None,
) -> Seitenergebnis:
    """Apply all hits of one page.  Text-layer hits (``quelle == "text"``) use the
    clipped band and the layout check; OCR and code hits blank the image pixels
    under a padded box.  Returns the number of boxes and whether the remaining
    text kept its position; the caller rasterises the page if not."""
    opt = opt or Schwaerzoptionen()
    einblendungen: list[tuple[pymupdf.Rect, float, float, str]] = []
    anzahl = 0
    texttreffer = [f for f in fundstellen if f.quelle == "text"]
    for f in fundstellen:
        if not f.rects:
            continue
        for k, roh in enumerate(f.rects):
            rect = pymupdf.Rect(roh)
            if f.quelle == "text" and seite is not None:
                zeile = _zeile_von_rect(seite, f, rect)
                band = _band(rect, zeile, seite) if zeile >= 0 else rect
            else:
                rect = pymupdf.Rect(rect.x0 - OCR_RAND, rect.y0 - OCR_RAND,
                                    rect.x1 + OCR_RAND, rect.y1 + OCR_RAND) & page.rect
                band = pymupdf.Rect(rect)
            fuell = pymupdf.Rect(rect)
            if k == 0:
                textquelle = f.quelle == "text" and seite is not None
                basis = seite.basislinie(f.start, f.ende) if textquelle else None
                if basis is None:
                    basis = rect.y1 - 0.22 * rect.height
                frei = seite.freier_raum_rechts(f.ende) if textquelle else rect.x1
                benoetigt = pymupdf.get_text_length(
                    f.pseudonym, fontname=opt.schrift, fontsize=f.schriftgroesse
                ) + 2.0
                x1 = max(rect.x1, min(rect.x0 + benoetigt, frei - 1.0))
                fuell.x1 = band.x1 = max(band.x1, x1 - 0.2)
                groesse = _passende_groesse(f.pseudonym, fuell.width - 1.5, f.schriftgroesse, opt)
                einblendungen.append((fuell, basis, groesse, f.pseudonym))
            page.add_redact_annot(band, fill=opt.fuellfarbe, cross_out=False)
            anzahl += 1
    if not anzahl:
        return Seitenergebnis(0, True)
    page.apply_redactions(
        images=opt.bilder, graphics=opt.grafik, text=pymupdf.PDF_REDACT_TEXT_REMOVE
    )
    if seite is not None and texttreffer:
        if not lage_intakt(seite, lese_seite(page, seite.nummer), texttreffer):
            return Seitenergebnis(anzahl, False)
    for fuell, basis, groesse, pseudonym in einblendungen:
        _einblendung(page, fuell, basis, groesse, pseudonym, opt)
    return Seitenergebnis(anzahl, True)


def bereinige(doc: pymupdf.Document, vermerk: bool = True, datum: dt.date | None = None) -> None:
    """Remove metadata, XMP, attachments, bookmarks, annotations and form fields."""
    doc.scrub(
        attached_files=True,
        clean_pages=True,
        embedded_files=True,
        hidden_text=True,
        javascript=True,
        metadata=True,
        redactions=True,
        redact_images=0,
        remove_links=True,
        reset_fields=True,
        reset_responses=True,
        thumbnails=True,
        xml_metadata=True,
    )
    for page in doc:
        for widget in list(page.widgets()):
            page.delete_widget(widget)
        for annot in list(page.annots()):
            page.delete_annot(annot)
    doc.set_toc([])
    meta = {k: "" for k in ("title", "author", "subject", "keywords", "creator", "producer",
                            "creationDate", "modDate")}
    if vermerk:
        datum = datum or dt.date.today()
        meta.update(ERLAUBTE_METADATEN)
        meta["subject"] = f"{VERMERK_PRAEFIX} am {datum.strftime('%d.%m.%Y')}"
    doc.set_metadata(meta)


def rastere(doc: pymupdf.Document, dpi: int = 200) -> pymupdf.Document:
    """Return a new document whose pages are plain images of the given one."""
    neu = pymupdf.open()
    for page in doc:
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        seite = neu.new_page(width=page.rect.width, height=page.rect.height)
        seite.insert_image(seite.rect, pixmap=pix)
    return neu


def speichere(doc: pymupdf.Document, ziel: Path) -> None:
    """Full (non-incremental) save with garbage collection so that nothing removed survives."""
    doc.save(
        ziel,
        garbage=4,
        deflate=True,
        clean=True,
        incremental=False,
        encryption=pymupdf.PDF_ENCRYPT_NONE,
    )
