"""Generic patterns that identify a person regardless of who the patient is.

Two kinds: values that are identifying by their shape (insurance number, IBAN,
e-mail address) and values that follow a telling label ("Versicherten-Nr.",
"Pat.-ID", "Aktenzeichen").  The latter use a named group ``wert`` so that only
the value, not the label, is replaced; they catch numbers that OCR misread.
"""

from __future__ import annotations

import re

from anonymed.kern.modell import Art

VERSICHERTENNUMMER = re.compile(r"(?<![A-Z0-9])[A-Z]\d{9}(?!\d)")
IBAN = re.compile(r"(?<![A-Z0-9])DE\d{2}(?:\s?\d{4}){4}\s?\d{2}(?![0-9])")
EMAIL = re.compile(
    r"(?<![\w.+-])[\w+-]+(?:\.\s?[\w+-]+)*\s?@\s?[\w-]+(?:\s?\.\s?[\w-]+)+(?![\w.-])"
)

_SEP = r"\s*[:.]?\s*"
_ANFANG = r"(?<![A-Za-z0-9])"  # the label starts a word; "KAZ2Q4W" is not "AZ 2Q4W"
VERSICHERTENNUMMER_MIT_ETIKETT = re.compile(
    _ANFANG
    + r"(?i)(?:Versicherten(?:-|\s)?(?:Nr|Nummer)\.?|Versichertennr\.?|KVNR|KV-?Nr\.?"
    + r"|Vers\.?-?Nr\.?)"
    + _SEP + r"(?P<wert>[A-Z0-9]{8,12})(?![A-Z0-9])"
)
PATIENTEN_ID_MIT_ETIKETT = re.compile(
    _ANFANG + r"(?i)(?:Pat(?:\.|ienten)?\s?-?\s?(?:ID|Nr|Nummer)\.?|Patientennummer)"
    + _SEP + r"(?P<wert>\d[\d\s]{3,14}\d)(?!\d)"
)
AKTENZEICHEN_MIT_ETIKETT = re.compile(
    _ANFANG
    + r"(?:Aktenzeichen|Fall(?:-|\s)?(?:Nr|Nummer)\.?|Fallzahl|Az\.|AZ|Vorgangs(?:-|\s)?(?:Nr|Nummer)\.?)"
    + _SEP + r"(?P<wert>[A-Z0-9][A-Z0-9/\-.]{3,24})(?![A-Z0-9/])"
)

GLOBALE_MUSTER: list[tuple[str, re.Pattern[str], Art]] = [
    ("muster.versichertennummer", VERSICHERTENNUMMER, Art.VERSICHERTENNUMMER),
    ("muster.iban", IBAN, Art.IBAN),
    ("muster.email", EMAIL, Art.EMAIL),
    ("muster.versichertennummer.etikett", VERSICHERTENNUMMER_MIT_ETIKETT, Art.VERSICHERTENNUMMER),
    ("muster.patienten_id.etikett", PATIENTEN_ID_MIT_ETIKETT, Art.PATIENTEN_ID),
    ("muster.aktenzeichen.etikett", AKTENZEICHEN_MIT_ETIKETT, Art.AKTENZEICHEN),
]
