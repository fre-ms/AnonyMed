"""Generic patterns that identify a person regardless of who the patient is."""

from __future__ import annotations

import re

from anonymed.kern.modell import Art

VERSICHERTENNUMMER = re.compile(r"(?<![A-Z0-9])[A-Z]\d{9}(?!\d)")
IBAN = re.compile(r"(?<![A-Z0-9])DE\d{2}(?:\s?\d{4}){4}\s?\d{2}(?![0-9])")
EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+(?![\w.-])")

GLOBALE_MUSTER: list[tuple[str, re.Pattern[str], Art]] = [
    ("muster.versichertennummer", VERSICHERTENNUMMER, Art.VERSICHERTENNUMMER),
    ("muster.iban", IBAN, Art.IBAN),
    ("muster.email", EMAIL, Art.EMAIL),
]
