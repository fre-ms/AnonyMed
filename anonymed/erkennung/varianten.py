"""Regular-expression builders for one patient's identifiers.

Every builder returns a pattern *string* (or ``None`` when the input is too
short to be safe).  Names are matched in their written form, in capitals and
in title case, with umlauts transliterated (Müller, Mueller, Muller) and an
optional genitive s.  Lower-case forms are deliberately not matched so that
surnames which are also ordinary words ("klein", "koch", "ernst") do not fire
in running text.
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata

# Word boundaries based on Unicode letters (``\\w`` would also cover digits).
L = r"(?<![^\W\d_])"
R = r"(?![^\W\d_])"
SEP = r"[\s\-‐‑–]+"
GENITIV = r"(?:s|'s|’s)?"

_TRANS = str.maketrans(
    {"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss", "ẞ": "SS"}
)
_TITEL = (
    r"(?:(?:Univ\.-Prof\.|Prof\.|PD|Priv\.-Doz\.|Dr\.|med\.|dent\.|phil\.|rer\.|nat\.|h\.c\."
    r"|Dipl\.-\w+\.?|Mag\.|MUDr\.|MD|PhD)\s*)*"
)
_MONATE = {
    1: ("Januar", "Jan", "Jänner"),
    2: ("Februar", "Feb"),
    3: ("März", "Maerz", "Mrz", "Mär"),
    4: ("April", "Apr"),
    5: ("Mai",),
    6: ("Juni", "Jun"),
    7: ("Juli", "Jul"),
    8: ("August", "Aug"),
    9: ("September", "Sept", "Sep"),
    10: ("Oktober", "Okt"),
    11: ("November", "Nov"),
    12: ("Dezember", "Dez"),
}
_STRASSEN_SUFFIX = re.compile(r"[\s\-]?(stra(?:ß|ss)e|str\.?)$", re.IGNORECASE)
_NAMENSPARTIKEL = {"von", "van", "der", "den", "de", "zu", "zur", "zum", "da", "dos", "del",
                   "la", "le", "und"}


def ohne_akzente(text: str) -> str:
    zerlegt = unicodedata.normalize("NFKD", text.replace("ß", "ss"))
    return "".join(c for c in zerlegt if not unicodedata.combining(c))


def formen(text: str) -> set[str]:
    """Spelling variants of a name: as written, transliterated, without accents,
    each also in capitals and in title case."""
    basis = text.strip()
    if not basis:
        return set()
    varianten = {basis, basis.translate(_TRANS), ohne_akzente(basis)}
    out: set[str] = set()
    for v in varianten:
        out.update({v, v.upper(), v.title()})
    return {v for v in out if v}


def flex(form: str) -> str:
    """Escape a form, allowing any whitespace or hyphen between its parts."""
    teile = [re.escape(t) for t in re.split(r"[\s\-‐‑–]+", form) if t]
    return SEP.join(teile)


def alternative(formen_: set[str]) -> str:
    return "(?:" + "|".join(flex(f) for f in sorted(formen_, key=len, reverse=True)) + ")"


def name_muster(name: str, genitiv: bool = True, mindestlaenge: int = 2) -> str | None:
    """A single name (surname, first name, birth name) as a whole word."""
    if len(name.strip()) < mindestlaenge:
        return None
    return f"{L}{alternative(formen(name))}{GENITIV if genitiv else ''}{R}"


def vollname_muster(vornamen: list[str], nachname: str) -> str | None:
    """"Max Mustermann", "Max Peter Mustermann", "Mustermann, Max", "M. Mustermann"."""
    vornamen = [v for v in vornamen if v.strip()]
    if not vornamen or not nachname.strip():
        return None
    v_formen: set[str] = set()
    for v in vornamen:
        v_formen |= formen(v)
    V = alternative(v_formen)
    N = alternative(formen(nachname))
    initialen = alternative({f[0] for f in formen(vornamen[0]) if f})
    varianten = [
        rf"{V}(?:{SEP}{V})*{SEP}{N}{GENITIV}",
        rf"{N},?\s+{V}(?:{SEP}{V})*",
        rf"{initialen}\.\s?{N}{GENITIV}",
    ]
    return f"{L}(?:{'|'.join(varianten)}){R}"


def titel_name_muster(vornamen: list[str], nachname: str) -> str | None:
    """Name with optional academic titles in front ("Dr. med. Anna Schmidt", "Dr. Schmidt").

    Used for referring physicians so that the title is replaced together with the
    name and the pseudonym "Dr. Z1" does not end up as "Dr. Dr. Z1".
    """
    if not nachname.strip():
        return None
    kern = [name_muster(nachname) or ""]
    voll = vollname_muster(vornamen, nachname)
    if voll:
        kern.insert(0, voll)
    inner = "|".join(f"(?:{k})" for k in kern if k)
    return rf"(?:{L}{_TITEL})?(?:{inner})"


def nachname_teile(nachname: str) -> list[str]:
    """Parts of a double-barrelled or compound surname worth matching on their own."""
    teile = [t for t in re.split(r"[\s\-‐‑–]+", nachname.strip()) if t]
    if len(teile) < 2:
        return []
    return [t for t in teile if len(t) >= 4 and t.lower() not in _NAMENSPARTIKEL]


def datum_muster(d: dt.date) -> str:
    """All common German spellings of one calendar date."""
    t, m, j = d.day, d.month, d.year
    T, M = rf"0?{t}", rf"0?{m}"
    JJ = rf"(?:{j}|{j % 100:02d})"
    mon = "|".join(re.escape(x) + r"\.?" for x in _MONATE[m])
    alts = [
        rf"{T}\s?\.\s?{M}\s?\.\s?{JJ}",
        rf"{T}\s?/\s?{M}\s?/\s?{JJ}",
        rf"{T}\s?-\s?{M}\s?-\s?{j}",
        rf"{j}-{m:02d}-{t:02d}",
        rf"{j}/{m:02d}/{t:02d}",
        rf"{T}\.?\s*(?i:{mon})\s*{j}",
    ]
    return r"(?<![\d.])(?:" + "|".join(f"(?:{a})" for a in alts) + r")(?!\d)"


def telefon_muster(nummer: str) -> str | None:
    """A phone number with any separators and +49/0049/0 prefix variants."""
    ziffern = re.sub(r"\D", "", nummer)
    if ziffern.startswith("0049"):
        ziffern = ziffern[4:]
    elif ziffern.startswith("49") and len(ziffern) > 10:
        ziffern = ziffern[2:]
    if ziffern.startswith("0"):
        ziffern = ziffern[1:]
    if len(ziffern) < 5:
        return None
    S = r"[\s\-/().]*"
    kern = S.join(ziffern)
    prefix = r"\(?\s*(?:\+\s?49|0049|0)\s*\)?\s*(?:\(0\))?"
    return rf"(?<![\d+]){prefix}{S}{kern}(?!\d)"


def email_muster(email: str) -> str | None:
    email = email.strip()
    if "@" not in email:
        return None
    return rf"(?<![\w.+-])(?i:{re.escape(email)})(?![\w.-])"


def adresse_muster(strasse: str) -> str | None:
    """Street with optional house number; "straße", "strasse" and "str." are interchangeable."""
    strasse = strasse.strip()
    if len(strasse) < 3:
        return None
    m = re.match(r"^(.*?)[\s,]*(\d+\s?[a-zA-Z]?(?:\s?[-/]\s?\d+\s?[a-zA-Z]?)?)?\s*$", strasse)
    name = (m.group(1) if m else strasse).strip(" ,")
    nummer = (m.group(2) or "").strip() if m else ""
    sm = _STRASSEN_SUFFIX.search(name)
    if sm:
        basis = name[: sm.start()].rstrip(" -")
        if not basis:
            return None
        strassen_re = (
            alternative(formen(basis))
            + r"[\s\-]?(?:[Ss]tra(?:ß|ss)e|[Ss]tr\.?|STRA(?:SS|ß)E|STR\.?)"
        )
    else:
        strassen_re = alternative(formen(name))
    nummer_re = ""
    if nummer:
        ziffern = re.match(r"\d+", nummer).group(0)
        nummer_re = rf"(?:\s*,?\s*{ziffern}\s?[a-zA-Z]?(?:\s?[-/]\s?\d+\s?[a-zA-Z]?)?)?"
    return rf"{L}{strassen_re}{nummer_re}(?![^\W_])"


def ort_muster(plz: str, ort: str) -> str | None:
    """"12345 Musterstadt", "D-12345 Musterstadt" or the town on its own."""
    ort = ort.strip()
    if len(ort) < 3:
        return None
    plz = re.sub(r"\D", "", plz)
    plz_re = rf"(?:(?:D[\-\s])?{plz}\s*)?" if plz else ""
    return rf"(?<![\w]){plz_re}{alternative(formen(ort))}{R}"


def exakt_muster(wert: str) -> str | None:
    """Alphanumeric code (insurance number, patient id, file number) as written,
    tolerant to spaces inside and case."""
    wert = wert.strip()
    if len(wert) < 4:
        return None
    teile = [re.escape(t) for t in re.split(r"\s+", wert) if t]
    return rf"(?<![\w])(?i:{r'\s*'.join(teile)})(?![\w])"


def nummer_muster(wert: str) -> str | None:
    ziffern = re.sub(r"\D", "", wert)
    if len(ziffern) < 4:
        return None
    return rf"(?<!\d){ziffern}(?!\d)"


def begriff_muster(begriff: str) -> str | None:
    """A free term from the user's lists, case-insensitive, flexible whitespace."""
    begriff = begriff.strip()
    if len(begriff) < 3:
        return None
    return rf"{L}(?i:{flex(begriff)}){R}"
