"""Plain-text inputs: transcripts of consultations, notes, subtitles.

A text file is one "page" without geometry.  Detection runs on an analysis
copy of exactly the same length in which the timestamps and cue numbers of
subtitle formats (SRT, WebVTT, Whisper's console style) are blanked, so that
every offset maps one to one onto the original and the pseudonyms can be
written back into it without touching anything else.
"""

from __future__ import annotations

import re
from pathlib import Path

from anonymed.dokument.textebene import Seitentext
from anonymed.kern.modell import Fundstelle

TEXTENDUNGEN = {".txt", ".md", ".markdown", ".srt", ".vtt"}
UNTERTITEL = {".srt", ".vtt"}

_ZEIT = r"(?:\d{1,2}:)?\d{1,2}:\d{2}[.,]\d{1,3}"
_TIMING = re.compile(rf"^[ \t]*{_ZEIT}[ \t]*-->[ \t]*{_ZEIT}.*$", re.MULTILINE)
_CUE_NUMMER = re.compile(r"^[ \t]*\d+[ \t]*$", re.MULTILINE)
_STEMPEL = re.compile(rf"\[{_ZEIT}[ \t]*-->[ \t]*{_ZEIT}\]")  # "[00:00.000 --> 00:04.000]"


def ist_textdatei(pfad: str | Path) -> bool:
    return Path(pfad).suffix.lower() in TEXTENDUNGEN


def lese_textdatei(pfad: str | Path) -> str:
    """Read UTF-8 (with or without BOM), fall back to Windows-1252; normalise line ends."""
    roh = Path(pfad).read_bytes()
    for kodierung in ("utf-8-sig", "cp1252"):
        try:
            text = roh.decode(kodierung)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = roh.decode("latin-1")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def analysetext(text: str, endung: str) -> str:
    """Copy of ``text`` with the same length in which subtitle timing lines, cue numbers,
    the WebVTT header and Whisper-style stamps are blanked out."""
    zeichen = list(text)

    def blende(a: int, b: int) -> None:
        for i in range(a, b):
            if zeichen[i] != "\n":
                zeichen[i] = " "

    muster = [_STEMPEL]
    if endung.lower() in UNTERTITEL:
        muster += [_TIMING, _CUE_NUMMER]
        if text.startswith("WEBVTT"):
            ende = text.find("\n")
            blende(0, len(text) if ende < 0 else ende)
    for m in muster:
        for t in m.finditer(text):
            blende(t.start(), t.end())
    return "".join(zeichen)


def textseite(text: str, nummer: int = 0) -> Seitentext:
    """A :class:`Seitentext` without boxes: the rules, the fuzzy search, NER and the
    vocabulary only need the string and the line of every character."""
    seite = Seitentext(nummer=nummer, breite=0.0, hoehe=0.0, text=text, quelle="text")
    zeilen: list[int] = []
    zeile = 0
    for c in text:
        zeilen.append(zeile)
        if c == "\n":
            zeile += 1
    n = len(text)
    seite.boxen = [None] * n
    seite.zeilen = zeilen
    seite.groessen = [0.0] * n
    seite.basislinien = [0.0] * n
    return seite


def ersetze(text: str, fundstellen: list[Fundstelle]) -> str:
    """Replace every hit by its pseudonym, from the end so that offsets stay valid."""
    for f in sorted((f for f in fundstellen if f.ende > f.start), key=lambda f: -f.start):
        text = text[: f.start] + f.pseudonym + text[f.ende :]
    return text
