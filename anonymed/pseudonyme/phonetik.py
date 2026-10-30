"""Kölner Phonetik (Postel 1969): a phonetic code for German names.

"Müller", "Mueller", "Muller" and "Miller" share the code 657; "Meier",
"Maier", "Mayer" and "Meyer" share 67.  Used to canonicalise names before
hashing and as one similarity signal in the record linkage.
"""

from __future__ import annotations

import re
import unicodedata

_VOKALE = set("AEIJOUY")
_ERSATZ = {"Ä": "A", "Ö": "O", "Ü": "U", "ß": "S", "É": "E", "È": "E", "Ç": "C", "Ñ": "N"}


def _vorbereiten(text: str) -> str:
    text = text.upper()
    text = "".join(_ERSATZ.get(c, c) for c in text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^A-Z]", "", text)


def koelner_phonetik(text: str) -> str:
    """Return the Kölner Phonetik code of ``text`` (digits), "" for empty input."""
    w = _vorbereiten(text)
    if not w:
        return ""
    codes: list[str] = []
    n = len(w)
    for i, c in enumerate(w):
        vorher = w[i - 1] if i > 0 else ""
        nachher = w[i + 1] if i + 1 < n else ""
        if c in _VOKALE:
            code = "0"
        elif c == "H":
            code = ""
        elif c == "B":
            code = "1"
        elif c == "P":
            code = "3" if nachher == "H" else "1"
        elif c in "DT":
            code = "8" if nachher in ("C", "S", "Z") and nachher else "2"
        elif c in "FVW":
            code = "3"
        elif c in "GKQ":
            code = "4"
        elif c == "C":
            if i == 0:
                code = "4" if nachher in set("AHKLOQRUX") and nachher else "8"
            elif vorher in ("S", "Z"):
                code = "8"
            elif nachher in set("AHKOQUX") and nachher:
                code = "4"
            else:
                code = "8"
        elif c == "X":
            code = "8" if vorher in ("C", "K", "Q") else "48"
        elif c == "L":
            code = "5"
        elif c in "MN":
            code = "6"
        elif c == "R":
            code = "7"
        elif c in "SZ":
            code = "8"
        else:
            code = ""
        codes.append(code)
    roh = "".join(codes)
    if not roh:
        return ""
    # collapse runs of the same digit, then drop "0" except at the start
    zusammen = re.sub(r"(.)\1+", r"\1", roh)
    return zusammen[0] + zusammen[1:].replace("0", "")
