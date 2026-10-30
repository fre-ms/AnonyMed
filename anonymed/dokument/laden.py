"""Open PDFs and images as a PyMuPDF document."""

from __future__ import annotations

import io
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
        return _heic(pfad)
    raise ValueError(f"Unsupported file type: {pfad.suffix}")


def _heic(pfad: Path) -> pymupdf.Document:
    """HEIC/HEIF photos: decode with pillow-heif (extra 'ocr'), honour the EXIF orientation."""
    try:
        import pillow_heif
        from PIL import Image, ImageOps
    except ImportError as e:
        raise NotImplementedError(
            "HEIC/HEIF photos need the extra 'ocr' (pillow-heif): uv sync --extra ocr"
        ) from e
    pillow_heif.register_heif_opener()
    with Image.open(pfad) as bild:
        bild = ImageOps.exif_transpose(bild).convert("RGB")
        puffer = io.BytesIO()
        bild.save(puffer, format="PNG")
    zwischen = pymupdf.open("png", puffer.getvalue())
    try:
        pdf_bytes = zwischen.convert_to_pdf()
    finally:
        zwischen.close()
    return pymupdf.open("pdf", pdf_bytes)
