"""German number words to digits, for transcripts.

A speech recogniser sometimes writes "einundzwanzigster Juli neunzehnhundert-
neunundfünfzig" or "Sieringgasse zweiundvierzig" where the rules expect
"21. Juli 1959" and "Sieringgasse 42".  :func:`normalisiere` rewrites such
words in a copy of the text **of the same length**: the digits take the place
of the word (or of a group of words) and the rest is padded with spaces, so
that every offset still points into the original.  The spans it rewrote are
returned so that a hit touching one can be widened to the whole word or group.

Recognised: cardinals up to the millions as one word ("zweitausendsechs-
undzwanzig"), ordinals ("erste", "einundzwanzigsten", "siebte", "achte"),
runs of single digits ("acht zwei vier sechs zwei" -> 82462), year pairs
("neunzehn neunundfünfzig" -> 1959) and dates (ordinal day, month as name or
ordinal, year as word or digits).  The articles "ein", "eine", "einen" … are
left alone; inside a compound ("einundzwanzig") they count.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_EINER = {"eins": 1, "ein": 1, "zwei": 2, "zwo": 2, "drei": 3, "vier": 4, "fünf": 5, "fuenf": 5,
          "sechs": 6, "sieben": 7, "acht": 8, "neun": 9}
_ZEHN_BIS_NEUNZEHN = {"zehn": 10, "elf": 11, "zwölf": 12, "zwoelf": 12, "dreizehn": 13,
                      "vierzehn": 14, "fünfzehn": 15, "fuenfzehn": 15, "sechzehn": 16,
                      "siebzehn": 17, "achtzehn": 18, "neunzehn": 19}
_ZEHNER = {"zwanzig": 20, "dreißig": 30, "dreissig": 30, "vierzig": 40, "fünfzig": 50,
           "fuenfzig": 50, "sechzig": 60, "siebzig": 70, "achtzig": 80, "neunzig": 90}
_ORDINAL_UNREGELMAESSIG = {"erst": 1, "dritt": 3, "siebt": 7}
_ARTIKEL = {"ein", "eine", "einen", "einem", "einer", "eines"}
_MONATE = {"januar": 1, "jänner": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4, "mai": 5,
           "juni": 6, "juli": 7, "august": 8, "september": 9, "oktober": 10, "november": 11,
           "dezember": 12}
_WORT = re.compile(r"[A-Za-zÄÖÜäöüß]+|\d+")


def _unter_hundert(w: str) -> int | None:
    if w in _EINER:
        return _EINER[w]
    if w in _ZEHN_BIS_NEUNZEHN:
        return _ZEHN_BIS_NEUNZEHN[w]
    if w in _ZEHNER:
        return _ZEHNER[w]
    einer, und, zehner = w.partition("und")
    if und and einer in _EINER and einer != "eins" and zehner in _ZEHNER:
        return _EINER[einer] + _ZEHNER[zehner]
    return None


def _unter_tausend(w: str) -> int | None:
    vor, hundert, rest = w.partition("hundert")
    if not hundert:
        return _unter_hundert(w)
    h = 1 if vor in ("", "ein") else _unter_hundert(vor)
    if h is None or vor == "eins":
        return None
    if not rest:
        return h * 100
    r = _unter_hundert(rest)
    return None if r is None else h * 100 + r


def kardinal(wort: str) -> int | None:
    """Value of a cardinal number word, or None.  "ein" alone is an article."""
    w = wort.casefold()
    if w == "null":
        return 0
    if w in _ARTIKEL:
        return None
    vor, tausend, rest = w.partition("tausend")
    if tausend:
        t = 1 if vor in ("", "ein") else _unter_tausend(vor)
        if t is None or vor == "eins":
            return None
        if not rest:
            return t * 1000
        r = _unter_tausend(rest)
        return None if r is None else t * 1000 + r
    return _unter_tausend(w)


def ordinal(wort: str) -> int | None:
    """Value of an ordinal number word ("einundzwanzigsten" -> 21), or None."""
    w = wort.casefold()
    for stamm, wert in _ORDINAL_UNREGELMAESSIG.items():
        if re.fullmatch(stamm + r"e[rsnm]?", w):
            return wert
    m = re.fullmatch(r"(.+?)(?:ste|te)[rsnm]?", w)
    if m:
        wert = kardinal(m.group(1))  # "zweite", "einundzwanzigsten", "hundertste"
        if wert is not None:
            return wert
    m = re.fullmatch(r"(.+?)e[rsnm]?", w)  # "achte", "achten"
    if m:
        wert = kardinal(m.group(1))
        if wert is not None and m.group(1) not in _ARTIKEL:
            return wert
    return None


@dataclass
class Normalisiert:
    text: str
    spannen: list[tuple[int, int]] = field(default_factory=list)  # rewritten regions

    def erweitere(self, start: int, ende: int) -> tuple[int, int]:
        """Widen ``[start, ende)`` to cover every rewritten region it touches."""
        for a, b in self.spannen:
            if start < b and ende > a:
                start, ende = min(start, a), max(ende, b)
        return start, ende


@dataclass
class _Token:
    start: int
    ende: int
    wort: str
    kardinal: int | None = None
    ordinal: int | None = None
    monat: int | None = None
    ziffern: bool = False

    @property
    def zahl(self) -> bool:
        return self.kardinal is not None or self.ordinal is not None


def _tokens(text: str) -> list[_Token]:
    tokens: list[_Token] = []
    for m in _WORT.finditer(text):
        w = m.group(0)
        t = _Token(m.start(), m.end(), w)
        if w.isdigit():
            t.ziffern = True
        else:
            t.ordinal = ordinal(w)
            t.kardinal = None if t.ordinal is not None else kardinal(w)
            t.monat = _MONATE.get(w.casefold())
        tokens.append(t)
    return tokens


def _nur_leerraum(text: str, a: int, b: int) -> bool:
    return text[a:b].strip(" \t") == ""


def _datum(tokens: list[_Token], i: int, text: str) -> tuple[int, str] | None:
    """A date starting at token i: ordinal day, month (name or ordinal), optional year
    (word or digits).  Returns (number of tokens, digits) or None."""
    tag = tokens[i]
    if tag.ordinal is None or not 1 <= tag.ordinal <= 31 or i + 1 >= len(tokens):
        return None
    monat = tokens[i + 1]
    if not _nur_leerraum(text, tag.ende, monat.start):
        return None
    if monat.monat is not None:
        m_text = text[monat.start:monat.ende]
    elif monat.ordinal is not None and 1 <= monat.ordinal <= 12:
        m_text = f"{monat.ordinal}."
    else:
        return None
    n = 2
    jahr = ""
    if i + 2 < len(tokens):
        j = tokens[i + 2]
        if _nur_leerraum(text, monat.ende, j.start):
            k = tokens[i + 3] if i + 3 < len(tokens) else None
            if j.ziffern and len(j.wort) in (2, 4):
                jahr, n = j.wort, 3
            elif (j.kardinal is not None and 10 <= j.kardinal <= 20 and k is not None
                  and k.kardinal is not None and 10 <= k.kardinal <= 99
                  and _nur_leerraum(text, j.ende, k.start)):
                jahr, n = f"{j.kardinal}{k.kardinal:02d}", 4  # "neunzehn neunundfünfzig"
            elif j.kardinal is not None and (1000 <= j.kardinal <= 2999 or 0 <= j.kardinal <= 99):
                jahr, n = str(j.kardinal), 3
    if monat.monat is not None:
        ersatz = f"{tag.ordinal}. {m_text}" + (f" {jahr}" if jahr else "")
    else:
        ersatz = f"{tag.ordinal}.{m_text}" + jahr
    return n, ersatz


def normalisiere(text: str) -> Normalisiert:
    """Rewrite number words as digits, keeping the length of ``text``."""
    zeichen = list(text)
    spannen: list[tuple[int, int]] = []

    def schreibe(a: int, b: int, ersatz: str) -> None:
        if len(ersatz) > b - a:
            return  # cannot happen for German number words, but never corrupt offsets
        for k in range(a, b):
            zeichen[k] = " " if text[k] != "\n" else "\n"
        for k, c in enumerate(ersatz):
            zeichen[a + k] = c
        spannen.append((a, b))

    tokens = _tokens(text)
    i = 0
    while i < len(tokens):
        t = tokens[i]
        datum = _datum(tokens, i, text)
        if datum is not None:
            n, ersatz = datum
            schreibe(t.start, tokens[i + n - 1].ende, ersatz)
            i += n
            continue
        if t.kardinal is not None:
            # a run of single digits -> one number ("acht zwei vier sechs zwei")
            j = i
            while (j + 1 < len(tokens) and tokens[j + 1].kardinal is not None
                   and 0 <= tokens[j + 1].kardinal <= 9 and 0 <= tokens[i].kardinal <= 9
                   and _nur_leerraum(text, tokens[j].ende, tokens[j + 1].start)):
                j += 1
            if j > i:
                ersatz = "".join(str(tokens[k].kardinal) for k in range(i, j + 1))
                schreibe(t.start, tokens[j].ende, ersatz)
                i = j + 1
                continue
            # a year in two parts ("neunzehn neunundfünfzig")
            if (10 <= t.kardinal <= 20 and i + 1 < len(tokens)
                    and tokens[i + 1].kardinal is not None and 0 <= tokens[i + 1].kardinal <= 99
                    and not (0 <= tokens[i + 1].kardinal <= 9)
                    and _nur_leerraum(text, t.ende, tokens[i + 1].start)):
                schreibe(t.start, tokens[i + 1].ende, f"{t.kardinal}{tokens[i + 1].kardinal:02d}")
                i += 2
                continue
            schreibe(t.start, t.ende, str(t.kardinal))
        elif t.ordinal is not None:
            schreibe(t.start, t.ende, f"{t.ordinal}.")
        i += 1
    return Normalisiert("".join(zeichen), spannen)
