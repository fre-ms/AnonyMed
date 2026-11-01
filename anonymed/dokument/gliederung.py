"""Layout of a recognised page as Markdown: paragraphs, headings, lists and tables.

OCR hands over lines.  A language model wants the text the way the document meant
it: paragraphs in one piece, headings marked, lists and tables kept apart.  The
geometry of the lines tells the layout: a line that runs to the right margin goes on
in the next one; a gap taller than the usual line spacing ends a paragraph; a line in
a larger face, or a short bold line on its own, is a heading; a bullet starts a list
item; big gaps inside a line (tabs in the page text, see
:func:`~anonymed.dokument.ocr.seitentext_aus_woerter`) are table cells.

The roles are computed once from the OCR geometry and applied to the page text later,
raw or with the pseudonyms in place: both have the same lines.  Italics are not
recognised: the engines report no slant, and a stroke-angle guess is not reliable
enough to mark text with.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from statistics import median
from typing import Any

import pymupdf

from anonymed.dokument.textebene import Seitentext

_AUFZAEHLUNG = re.compile(r"^(?:(?P<symbol>[-•·▪◦●○■□*–—>])|(?P<nummer>\d{1,2}[.)])"
                          r"|(?P<buchstabe>[a-zA-Z][.)]))\s+(?=\S)")
# "Diagnosen:", "Medikation:", "Aktuelle Medikation:" at the start of a line open a block of
# their own even after a line that ran to the margin
_ETIKETT = re.compile(r"^[A-ZÄÖÜ][\wäöüß./-]{1,24}(?: [\wäöüß./-]{1,24}){0,2}:\s")
UEBERSCHRIFT_GROSS = 3  # Markdown levels below the page headings of the Markdown file
UEBERSCHRIFT_KLEIN = 4


@dataclass
class Zeile:
    """One OCR line: its span in the page text and its box in page points."""

    start: int
    ende: int
    text: str
    x0: float = 0.0
    y0: float = 0.0
    x1: float = 0.0
    y1: float = 0.0
    hoehe: float = 0.0

    @property
    def leer(self) -> bool:
        return not self.text.strip() or self.x1 <= self.x0

    @property
    def breite(self) -> float:
        return self.x1 - self.x0


@dataclass
class Rolle:
    """What a line is and how it attaches to the line before it."""

    art: str = "absatz"  # absatz, ueberschrift, liste, tabelle
    ebene: int = 0  # heading level
    fett: bool = False
    trenner: str = "absatz"  # absatz (blank line), zeile (newline), wort (space), silbe (join)
    tabelle: int = -1


@dataclass
class Gliederung:
    """The roles of the lines of one page; :meth:`rendere` turns a page text into
    Markdown.  Built by :func:`gliedere`."""

    rollen: list[Rolle] = field(default_factory=list)

    @property
    def tabellen(self) -> int:
        return len({r.tabelle for r in self.rollen if r.art == "tabelle"})

    @property
    def ueberschriften(self) -> int:
        return sum(1 for r in self.rollen if r.art == "ueberschrift")

    def rendere(self, text: str) -> str:
        """Markdown of ``text``, which must have the lines the roles were computed for
        (the raw OCR text or that text with pseudonyms).  Anything else comes back as
        it is."""
        segmente = text.split("\n")
        if len(segmente) == len(self.rollen) + 1 and segmente[-1] == "":
            segmente.pop()
        if len(segmente) != len(self.rollen):
            return text
        bloecke: list[str] = []
        absatz = _Absatz()
        i, n = 0, len(segmente)
        while i < n:
            r, t = self.rollen[i], segmente[i].strip()
            if r.art == "tabelle":
                bloecke.append(absatz.schliesse())
                reihen = []
                while i < n and self.rollen[i].tabelle == r.tabelle:
                    reihen.append([c.strip() for c in segmente[i].split("\t")])
                    i += 1
                bloecke.append(_tabelle(reihen))
                continue
            if r.art == "ueberschrift":
                bloecke.append(absatz.schliesse())
                if t:
                    bloecke.append("#" * r.ebene + " " + t)
                i += 1
                continue
            if r.art == "liste":
                bloecke.append(absatz.schliesse())
                punkte: list[str] = []
                while i < n and self.rollen[i].art == "liste":
                    rr, tt = self.rollen[i], segmente[i].strip()
                    if punkte and rr.trenner in ("wort", "silbe"):
                        punkte[-1] = _anhaengen(punkte[-1], tt, rr.trenner)
                    elif tt:
                        punkte.append(_listenpunkt(tt))
                    i += 1
                bloecke.append("\n".join(punkte))
                continue
            if t:
                if r.trenner == "absatz":
                    bloecke.append(absatz.schliesse())
                absatz.fuege_an(t, r.fett, r.trenner)
            i += 1
        bloecke.append(absatz.schliesse())
        return "\n\n".join(b for b in bloecke if b.strip()) + "\n"


class _Absatz:
    """Collects the pieces of one paragraph: joined with spaces, newlines or across a
    hyphen; bold runs are wrapped in ``**``."""

    def __init__(self) -> None:
        self.teile: list[tuple[str, str, bool]] = []  # (trenner, text, fett)

    def fuege_an(self, text: str, fett: bool, trenner: str) -> None:
        if not self.teile:
            trenner = "absatz"
        self.teile.append((trenner, text, fett))

    def schliesse(self) -> str:
        if not self.teile:
            return ""
        aus = ""
        offen = False  # inside a bold run
        for trenner, text, fett in self.teile:
            if trenner == "silbe" and aus.rstrip().endswith("-"):
                aus = aus.rstrip()[:-1]
                sep = ""
            elif trenner == "wort":
                sep = " "
            elif trenner == "zeile":
                sep = "\n"
            else:
                sep = ""
            if offen and not (fett and sep == " "):
                aus += "**"
                offen = False
            if sep == "\n" and offen:
                aus += "**"
                offen = False
            aus += sep
            if fett and not offen:
                aus += "**"
                offen = True
            aus += text
        if offen:
            aus += "**"
        self.teile = []
        return aus


def _anhaengen(bisher: str, text: str, trenner: str) -> str:
    if trenner == "silbe" and bisher.rstrip().endswith("-"):
        return bisher.rstrip()[:-1] + text
    return bisher + " " + text


def _listenpunkt(text: str) -> str:
    m = _AUFZAEHLUNG.match(text)
    if m is None:
        return "- " + text
    rest = text[m.end():]
    if m.group("nummer"):
        return m.group("nummer").rstrip(")") .rstrip(".") + ". " + rest
    if m.group("buchstabe"):
        return "- " + m.group("buchstabe") + " " + rest
    return "- " + rest


def _tabelle(reihen: list[list[str]]) -> str:
    spalten = max(len(r) for r in reihen)
    reihen = [r + [""] * (spalten - len(r)) for r in reihen]
    zelle = lambda c: c.replace("|", "\\|")  # noqa: E731
    zeilen = ["| " + " | ".join(zelle(c) for c in reihen[0]) + " |",
              "|" + " --- |" * spalten]
    zeilen += ["| " + " | ".join(zelle(c) for c in r) + " |" for r in reihen[1:]]
    return "\n".join(zeilen)


# -- geometry --------------------------------------------------------------------------------------
def zeilen_aus(seite: Seitentext) -> list[Zeile]:
    """The lines of a page text with their boxes (from the character boxes)."""
    zeilen: list[Zeile] = []
    start = 0
    segmente = seite.text.split("\n")
    if seite.text.endswith("\n"):
        segmente.pop()
    for seg in segmente:
        ende = start + len(seg)
        z = Zeile(start, ende, seg)
        boxen = [(seite.boxen[i], seite.text[i]) for i in range(start, min(ende, len(seite.boxen)))
                 if seite.boxen[i] is not None]
        if boxen:
            z.x0 = min(b[0] for b, _c in boxen)
            z.y0 = min(b[1] for b, _c in boxen)
            z.x1 = max(b[2] for b, _c in boxen)
            z.y1 = max(b[3] for b, _c in boxen)
            hoehen = [b[3] - b[1] for b, c in boxen if c.isalnum()] or \
                [b[3] - b[1] for b, _c in boxen]
            z.hoehe = median(hoehen)
        zeilen.append(z)
        start = ende + 1
    return zeilen


def fette_zeilen(zeilen: list[Zeile], grau: Any, skala: float) -> set[int]:
    """Indices of lines whose ink coverage stands well above the page's median: bold
    face.  ``grau`` is a 2-D uint8 array at ``skala`` pixels per point."""
    import numpy as np

    h, w = grau.shape[:2]
    dichten: dict[int, float] = {}
    for idx, z in enumerate(zeilen):
        if z.leer or sum(c.isalnum() for c in z.text) < 3:
            continue
        x0, y0 = max(0, int(z.x0 * skala)), max(0, int(z.y0 * skala))
        x1, y1 = min(w, int(z.x1 * skala) + 1), min(h, int(z.y1 * skala) + 1)
        if x1 - x0 < 3 or y1 - y0 < 3:
            continue
        band = np.asarray(grau[y0:y1, x0:x1])
        dichten[idx] = float((band < 128).mean())
    if len(dichten) < 4:
        return set()
    mitte = median(dichten.values())
    if mitte <= 0:
        return set()
    return {i for i, d in dichten.items() if d >= 1.45 * mitte and d - mitte >= 0.06}


def gliedere(seite: Seitentext, fett: set[int] | None = None) -> Gliederung:
    """Roles of the lines of an OCR page from their geometry (see the module notes)."""
    zeilen = zeilen_aus(seite)
    fett = fett or set()
    rollen = [Rolle() for _ in zeilen]
    gedruckt = [z for z in zeilen if not z.leer]
    if not gedruckt:
        return Gliederung(rollen)
    hoehen = [z.hoehe for z in gedruckt if z.hoehe > 0]
    H = median(hoehen) if hoehen else 10.0
    xs0 = sorted(z.x0 for z in gedruckt)
    xs1 = sorted(z.x1 for z in gedruckt)
    links = xs0[len(xs0) // 10]
    rechts = xs1[(len(xs1) * 9) // 10]
    breite = max(rechts - links, H)
    luecken = [b.y0 - a.y1 for a, b in zip(gedruckt, gedruckt[1:], strict=False)]
    normal = [g for g in luecken if -0.3 * H < g < 1.0 * H]
    G = median(normal) if normal else 0.3 * H

    # tables: runs of lines with cell gaps, or one line with at least three cells
    tab_id, i = 0, 0
    while i < len(zeilen):
        if "\t" in zeilen[i].text:
            j = i
            while j < len(zeilen) and "\t" in zeilen[j].text:
                j += 1
            if j - i >= 2 or zeilen[i].text.count("\t") >= 2:
                for k in range(i, j):
                    rollen[k].art, rollen[k].tabelle = "tabelle", tab_id
                tab_id += 1
            i = j
        else:
            i += 1

    # headings, list items, bold
    for idx, z in enumerate(zeilen):
        r = rollen[idx]
        if z.leer or r.art == "tabelle":
            continue
        t = z.text.strip()
        alnum = sum(c.isalnum() for c in t)
        buchstaben = [c for c in t if c.isalpha()]
        versal = len(buchstaben) >= 4 and all(c.isupper() for c in buchstaben)
        kurz = z.breite < 0.6 * breite
        if alnum >= 2 and z.hoehe >= 1.3 * H:
            r.art = "ueberschrift"
            r.ebene = UEBERSCHRIFT_GROSS if z.hoehe >= 1.6 * H else UEBERSCHRIFT_KLEIN
        elif _AUFZAEHLUNG.match(t):
            r.art = "liste"
        elif kurz and alnum >= 3 and (idx in fett or versal) and not t.endswith((".", ",")):
            r.art, r.ebene = "ueberschrift", UEBERSCHRIFT_KLEIN
        r.fett = idx in fett and r.art != "ueberschrift"

    # how each line attaches to the previous printed one
    vor_idx: int | None = None
    for idx, z in enumerate(zeilen):
        if z.leer:
            continue
        r = rollen[idx]
        if vor_idx is None:
            r.trenner = "absatz"
            vor_idx = idx
            continue
        vor, rv = zeilen[vor_idx], rollen[vor_idx]
        t = z.text.strip()
        gap = z.y0 - vor.y1
        silbe = vor.text.rstrip().endswith("-") and t[:1].islower()
        if r.art == "tabelle" or rv.art == "tabelle":
            r.trenner = "zeile" if r.tabelle == rv.tabelle and r.art == "tabelle" else "absatz"
        elif r.art == "ueberschrift" or rv.art == "ueberschrift":
            r.trenner = "absatz"
        elif r.art == "liste":
            r.trenner = "zeile" if rv.art == "liste" else "absatz"
        elif rv.art == "liste":
            if gap <= G + 0.5 * H and z.x0 >= vor.x0 + 0.8 * H:
                r.art, r.trenner = "liste", ("silbe" if silbe else "wort")
            else:
                r.trenner = "absatz"
        else:
            vor_voll = vor.x1 >= rechts - 0.12 * breite
            eingerueckt = z.x0 - links > 1.2 * H and vor.x0 - links <= 0.6 * H
            kurz = z.breite < 0.6 * breite
            etikett = _ETIKETT.match(t) is not None and not vor.text.rstrip().endswith("-")
            if gap > G + 0.5 * H or eingerueckt:
                r.trenner = "absatz"
            elif vor_voll:
                r.trenner = "absatz" if etikett else ("silbe" if silbe else "wort")
            elif vor.text.rstrip().endswith(":") and not kurz:
                r.trenner = "absatz"
            else:
                r.trenner = "zeile"
        vor_idx = idx
    return Gliederung(rollen)


def gliedere_seite(page: pymupdf.Page, ocr: Seitentext, dpi: int = 100) -> Gliederung:
    """Roles of an OCR page including bold detection on a grey rendering of the page
    (needs NumPy; without it there is no bold)."""
    zeilen = zeilen_aus(ocr)
    fett: set[int] = set()
    try:
        import numpy as np

        skala = dpi / 72.0
        pix = page.get_pixmap(matrix=pymupdf.Matrix(skala, skala), colorspace=pymupdf.csGRAY,
                              alpha=False)
        grau = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
        fett = fette_zeilen(zeilen, grau, skala)
    except ImportError:
        pass
    return gliedere(ocr, fett)
