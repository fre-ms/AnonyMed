"""Open PDFs and images as a PyMuPDF document."""

from __future__ import annotations

from pathlib import Path

import pymupdf

BILDENDUNGEN = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".gif", ".webp", ".pnm"}
HEIC_ENDUNGEN = {".heic", ".heif"}


def oeffne(pfad: str | Path) -> pymupdf.Document:
    """Open a PDF or an image; images are wrapped into a one-page PDF.

    HEIC/HEIF photos need the optional ``ocr`` extra (pillow-heif) and are
    handled in milestone M3.
    """
    pfad = Path(pfad)
    endung = pfad.suffix.lower()
    if endung == ".pdf":
        return pymupdf.open(pfad)
    if endung in BILDENDUNGEN:
        bild = pymupdf.open(pfad)
        try:
            pdf_bytes = bild.convert_to_pdf()
        finally:
            bild.close()
        return pymupdf.open("pdf", pdf_bytes)
    if endung in HEIC_ENDUNGEN:
        raise NotImplementedError("HEIC/HEIF photos are planned for milestone M3 (pillow-heif).")
    raise ValueError(f"Unsupported file type: {pfad.suffix}")
