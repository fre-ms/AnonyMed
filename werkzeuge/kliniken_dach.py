"""Build ``pseudonymed/daten/kliniken_dach.csv.gz``: hospitals and rehabilitation clinics in DE
and CH.

One row per site with name, type, address, official municipality code, operator and beds, so
that a clinic name found in a document can later be tied to a municipality and its population.

Sources and licences
--------------------
Germany (DE): Statistische Ämter des Bundes und der Länder, "Verzeichnis der Krankenhäuser und
Vorsorge- oder Rehabilitationseinrichtungen in Deutschland", reporting year 2024 (Stand
31.12.2024), workbook ``krankenhausverzeichnis-3500100247005.xlsx`` published by Destatis in
May 2026.  Sheet ``KHV_<Jahr>`` has one row per hospital site, sheet ``RHV_<Jahr>`` one row per
prevention or rehabilitation facility (Wirtschaftseinheit).  Licence: "Vervielfältigung und
Verbreitung, auch auszugsweise, mit Quellenangabe gestattet" (imprint of the workbook; the
general terms at https://www.destatis.de/DE/Service/Impressum/copyright-allgemein.html allow
commercial reuse and ask that changes be marked).  The workbook gives the municipality key
(Land, Kreis incl. Regierungsbezirk, Gemeinde) as of 31.12.2024; it is turned into the 12-digit
ARS of ``pseudonymed/daten/gemeinden_de.csv.gz`` (GV-ISys), with a fallback over place name and
postcode for municipalities that changed since.  Berlin and Hamburg carry district codes in
the source and map to the city as a whole.

Switzerland (CH): Bundesamt für Gesundheit (BAG), "Kennzahlen der Schweizer Spitäler 2023",
workbook ``kzp23_data.xlsx`` from https://opendata.swiss/de/dataset/kennzahlen-der-schweizer-
spitaler-2023; sheet ``KZ2023_KZP23`` (one row per hospital enterprise: type, beds) and sheet
``Standorte_KZ2023_KZP23`` (one row per site).  Licence: opendata.swiss "terms_by" (open use,
commercial use allowed, source must be named).  The 2024 edition is published under
"terms_by_ask" (commercial use only with permission) and is deliberately not used.  BFS
municipality numbers come from swisstopo's "Amtliches Ortschaftenverzeichnis mit
Postleitzahl" (``AMTOVZ_CSV_LV95``, opendata.swiss "terms_open").

Not included: Austria (the ministry's "Liste der Krankenanstalten in Österreich" allows
reprinting only for non-commercial purposes) and the Bundes-Klinik-Atlas export (no open
licence; the G-BA imprint reserves all rights).

Columns: land, kennung, name, typ, strasse, plz, ort, gemeinde_kennung, traeger, betten, quelle.
``kennung`` is the source's site key: Destatis has only a random hospital ID per edition
(``KH<id>-<n>`` for the n-th site of a hospital, ``RH<id>`` for rehabilitation facilities); the
BAG data have no key, so ``kzp-<hash>`` is derived from canton, enterprise, site and address.
``name`` is the site name; where the site label alone is not self-contained it is joined to the
hospital name with " – ".  ``typ`` is krankenhaus, psychiatrie (most beds in psychiatric,
child and adolescent psychiatric or psychosomatic departments; sites without beds by name, by
the hospital as a whole, or as day clinic/outpatient unit of a hospital with psychiatric beds;
CH by the BFS hospital type K211/K212) or reha (Destatis RHV, BFS K221).  ``betten`` is
empty for CH sites of multi-site enterprises (beds are given per enterprise only).

Usage: python -I werkzeuge/kliniken_dach.py [--destatis X.xlsx] [--bag Y.xlsx]
       [--ortschaften AMTOVZ.zip]   (without arguments the files are downloaded)
"""

from __future__ import annotations

import argparse
import collections
import csv
import difflib
import gzip
import hashlib
import io
import re
import sys
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path

URL_DESTATIS = ("https://www.destatis.de/DE/Themen/Gesellschaft-Umwelt/Gesundheit/Krankenhauser/"
                "Publikationen/Downloads-Krankenhaeuser/krankenhausverzeichnis-3500100247005.xlsx"
                "?__blob=publicationFile")
URL_BAG = ("https://opendata.bagnet.ch/?r=/download&path="
           "L0taUC9renAyMy9renAyM19kYXRhLnhsc3g%3D")  # /KZP/kzp23/kzp23_data.xlsx
URL_ORTSCHAFTEN = ("https://data.geo.admin.ch/ch.swisstopo-vd.ortschaftenverzeichnis_plz/"
                   "ortschaftenverzeichnis_plz/ortschaftenverzeichnis_plz_2056.csv.zip")
DATEN = Path(__file__).resolve().parent.parent / "pseudonymed" / "daten"
ZIEL = DATEN / "kliniken_dach.csv.gz"
GEMEINDEN = DATEN / "gemeinden_de.csv.gz"
SPALTEN = ["land", "kennung", "name", "typ", "strasse", "plz", "ort", "gemeinde_kennung",
           "traeger", "betten", "quelle"]

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
_T = f"{{{NS['m']}}}t"

# Psychiatric departments (§ 301 SGB V keys): 29xx general psychiatry, 30xx child and
# adolescent psychiatry, 31xx psychosomatics/psychotherapy, 3756 addiction medicine.
_PSYCH_FA = re.compile(r"^FA_(29|30|31)\d\d$|^FA_3756$")
_PSYCH_WORT = re.compile(r"psychiatr|psychosomat|psychotherap|\bpsych\b|seelisch|nervenklinik|"
                         r"\bkjp\b|\bpia\b|institutsambulanz|sucht|entzug", re.IGNORECASE)
_TAGESKLINIK = re.compile(r"tageskl|nachtkl|\btk\b|ambulanz", re.IGNORECASE)
_BEHANDLUNG = re.compile(r"\s*[–-]\s*(?:stationäre|teilstationäre|tagesklinische)\s+behandlung"
                         r"\s*$|\s*\(stationär\)\s*$|^hauptstandort\s*[–-]\s*", re.IGNORECASE)
_ALLGEMEIN = {"", "hauptstandort", "standort"}
_RECHTSFORM = re.compile(r"\b(?:ggmbh|gmbh|mbh|gag|ag|kg|kgaa|gbr|aör|aoer|adör|kdör|ku|se|"
                         r"e\.?\s?v|co|sa|sarl)\b\.?")
STADTSTAATEN = {"02": "02000000", "11": "11000000"}  # Hamburg, Berlin: whole city
TYPEN_CH = {"K211": "psychiatrie", "K212": "psychiatrie", "K221": "reha"}


def lade(quelle: str | None, url: str) -> bytes:
    """Read a local file, or download ``url`` into memory when no file is given."""
    if quelle:
        return Path(quelle).read_bytes()
    anfrage = urllib.request.Request(url, headers={"User-Agent": "PseudonyMed"})
    with urllib.request.urlopen(anfrage, timeout=180) as antwort:
        return antwort.read()


def leerzeichen(text: str) -> str:
    return " ".join(text.split())


def einfach(text: str) -> str:
    """Casefolded letters and digits only, umlauts kept, accents dropped (for comparisons)."""
    text = text.casefold().replace("ß", "ss")
    text = "".join(c for c in unicodedata.normalize("NFKD", text)
                   if not unicodedata.combining(c) or c == "\u0308")
    text = unicodedata.normalize("NFC", text)
    return " ".join(re.sub(r"[^\w]+", " ", text).split())


# --- xlsx with the standard library -----------------------------------------------------------

class Arbeitsmappe:
    """Minimal reader for the sheets of an .xlsx (a zip of SpreadsheetML)."""

    def __init__(self, daten: bytes) -> None:
        self.z = zipfile.ZipFile(io.BytesIO(daten))
        self.strings: list[str] = []
        if "xl/sharedStrings.xml" in self.z.namelist():
            ss = ET.fromstring(self.z.read("xl/sharedStrings.xml"))
            self.strings = ["".join(t.text or "" for t in si.iter(_T))
                            for si in ss.findall("m:si", NS)]
        wb = ET.fromstring(self.z.read("xl/workbook.xml"))
        rels = ET.fromstring(self.z.read("xl/_rels/workbook.xml.rels"))
        ziele = {r.get("Id"): r.get("Target", "") for r in rels}
        self.blaetter: dict[str, str] = {}
        blaetter = wb.find("m:sheets", NS)
        for s in blaetter if blaetter is not None else []:
            ziel = ziele[s.get(f"{{{NS['r']}}}id")].lstrip("/")
            self.blaetter[(s.get("name") or "").strip()] = (
                ziel if ziel.startswith("xl/") else "xl/" + ziel)

    def blatt(self, muster: str) -> str:
        """Name of the first sheet whose name matches the regular expression ``muster``."""
        for name in self.blaetter:
            if re.fullmatch(muster, name):
                return name
        raise KeyError(f"kein Tabellenblatt passt zu {muster!r}: {list(self.blaetter)}")

    def roh(self, name: str) -> Iterator[dict[str, str]]:
        """Rows of a sheet as {column letter: text}."""
        daten = ET.fromstring(self.z.read(self.blaetter[name])).find("m:sheetData", NS)
        for zeile in daten if daten is not None else []:
            aus: dict[str, str] = {}
            for c in zeile.findall("m:c", NS):
                spalte = "".join(ch for ch in c.get("r", "") if ch.isalpha())
                v = c.findtext("m:v", default="", namespaces=NS)
                if c.get("t") == "s" and v:
                    v = self.strings[int(v)]
                elif c.get("t") == "inlineStr":
                    v = "".join(t.text or "" for t in c.iter(_T))
                aus[spalte] = v.strip()
            yield aus

    def tabelle(self, name: str, ist_kopf: Callable[[dict[str, str]], bool]
                ) -> Iterator[dict[str, str]]:
        """Rows below the first header row (``ist_kopf``) as {header: text}; empty rows skipped."""
        kopf: dict[str, str] | None = None
        for zeile in self.roh(name):
            if kopf is None:
                if ist_kopf(zeile):
                    kopf = {k: v for k, v in zeile.items() if v}
                continue
            if any(zeile.values()):
                yield {kopf[k]: v for k, v in zeile.items() if k in kopf}


def ganzzahl(text: str) -> int:
    try:
        return round(float(text))
    except ValueError:
        return 0


# --- municipality keys ------------------------------------------------------------------------

class Gemeindeschluessel:
    """AGS (Destatis, 31.12.2024) → ARS of ``gemeinden_de.csv.gz``, fallback by place name/PLZ."""

    def __init__(self, pfad: Path) -> None:
        with gzip.open(pfad, "rt", encoding="utf-8", newline="") as f:
            self.quelle = f.readline().lstrip("# ").strip()
            zeilen = list(csv.DictReader(f))
        self.nach_ags = {z["ars"][:5] + z["ars"][9:]: z for z in zeilen}
        self.nach_name: dict[str, list[dict[str, str]]] = collections.defaultdict(list)
        self.nach_plz: dict[str, list[dict[str, str]]] = collections.defaultdict(list)
        for z in zeilen:
            # "Hanau, Brüder-Grimm-Stadt": index the name before the comma as well
            for name in {einfach(z["name"]), einfach(z["name"].split(",")[0])}:
                self.nach_name[name].append(z)
            self.nach_plz[z["plz"]].append(z)

    def ars(self, ags: str, plz: str, ort: str) -> tuple[str, str]:
        """(ARS, method); method is schluessel, ort, ort+plz, ort+kreis, ort+land, plz or ''."""
        ags = STADTSTAATEN.get(ags[:2], ags)
        if ags in self.nach_ags:
            return self.nach_ags[ags]["ars"], "schluessel"
        kandidaten = self.nach_name.get(einfach(ort), [])
        if not kandidaten:
            # postal place names may carry additions ("Bad Homburg v.d. Höhe", "Halle (Saale)")
            kurz = einfach(re.split(r"[,(/]", ort)[0])
            kandidaten = self.nach_name.get(kurz, [])
        if len(kandidaten) == 1:
            return kandidaten[0]["ars"], "ort"
        for verfahren, passt in (("ort+plz", lambda z: z["plz"] == plz),
                                 ("ort+kreis", lambda z: z["ars"][:5] == ags[:5]),
                                 ("ort+land", lambda z: z["ars"][:2] == ags[:2])):
            engere = [z for z in kandidaten if passt(z)]
            if len(engere) == 1:
                return engere[0]["ars"], verfahren
        if not kandidaten and len(self.nach_plz.get(plz, [])) == 1:
            return self.nach_plz[plz][0]["ars"], "plz"
        return "", ""


class Ortschaften:
    """swisstopo Amtliches Ortschaftenverzeichnis: (PLZ, place, canton) → BFS municipality no."""

    def __init__(self, daten: bytes) -> None:
        z = zipfile.ZipFile(io.BytesIO(daten))
        name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        jahr, monat, tag = z.getinfo(name).date_time[:3]
        self.stand = f"{jahr:04d}-{monat:02d}-{tag:02d}"
        text = z.read(name).decode("utf-8-sig")
        self.nach_plz: dict[str, list[dict[str, str]]] = collections.defaultdict(list)
        self.gemeinden: dict[str, list[dict[str, str]]] = collections.defaultdict(list)
        self.ortschaften: dict[str, list[dict[str, str]]] = collections.defaultdict(list)
        for r in csv.DictReader(io.StringIO(text), delimiter=";"):
            self.nach_plz[r["PLZ4"]].append(r)
            for variante in self._gemeinde(r):
                self.gemeinden[variante].append(r)
            self.ortschaften[einfach(r["Ortschaftsname"])].append(r)

    @staticmethod
    def _gemeinde(r: dict[str, str]) -> set[str]:
        """Municipality name with and without canton suffix ("Wil (SG)" → "wil sg", "wil")."""
        return {einfach(r["Gemeindename"]),
                einfach(re.sub(r"\s*\([A-Z]{2}\)$", "", r["Gemeindename"]))}

    @staticmethod
    def _anteil(r: dict[str, str]) -> float:
        try:
            return float(r.get("Adressenanteil", "0").replace("%", "").strip() or 0)
        except ValueError:
            return 0.0

    @staticmethod
    def _eindeutig(zeilen: list[dict[str, str]], kanton: str) -> str:
        nummern = {r["BFS-Nr"] for r in zeilen if not kanton or r["Kantonskürzel"] == kanton}
        return nummern.pop() if len(nummern) == 1 else ""

    def bfs(self, plz: str, ort: str, kanton: str = "") -> tuple[str, str]:
        """(BFS number, method).

        Methods: ``plz`` (postcode lies in one municipality), ``plz+gemeinde`` (place name is
        the municipality's name), ``plz+ort`` (place name is a locality of one municipality),
        ``gemeinde-statt-plz`` (the place is a municipality of the same canton that the
        postcode does not cover: the name wins), ``plz-anteil`` (still ambiguous: the
        municipality with the largest share of the postcode's addresses), ``gemeinde``/``ort``
        (postcode unknown, e.g. a post-box or large-customer postcode, found by name within
        the canton), or '' (not found).
        """
        o = einfach(re.sub(r"\s+\d+$", "", ort))  # "Bern 60", "Luzern 16"
        kandidaten = self.nach_plz.get(plz, [])
        if len({r["BFS-Nr"] for r in kandidaten}) == 1:
            return kandidaten[0]["BFS-Nr"], "plz"
        if kandidaten:
            gemeinde = [r for r in kandidaten if o in self._gemeinde(r)]
            if nummer := self._eindeutig(gemeinde, ""):
                return nummer, "plz+gemeinde"
            ortschaft = [r for r in kandidaten if einfach(r["Ortschaftsname"]) == o]
            if nummer := self._eindeutig(ortschaft, ""):
                return nummer, "plz+ort"
            if not gemeinde and not ortschaft:
                if nummer := self._eindeutig(self.gemeinden.get(o, []), kanton):
                    return nummer, "gemeinde-statt-plz"
            pool = gemeinde or ortschaft or kandidaten
            pool = [r for r in pool if r["Kantonskürzel"] == kanton] or pool
            return max(pool, key=self._anteil)["BFS-Nr"], "plz-anteil"
        if nummer := self._eindeutig(self.gemeinden.get(o, []), kanton):
            return nummer, "gemeinde"
        if nummer := self._eindeutig(self.ortschaften.get(o, []), kanton):
            return nummer, "ort"
        return "", ""


# --- names and types --------------------------------------------------------------------------

def _kern(name: str) -> str:
    return " ".join(_RECHTSFORM.sub(" ", einfach(name)).split())


def standortname(haus: str, standort: str) -> str:
    """Self-contained site name from the hospital name and the source's site label."""
    haus, standort = leerzeichen(haus), _BEHANDLUNG.sub("", leerzeichen(standort)).strip()
    if einfach(standort) in _ALLGEMEIN or not haus:
        return haus or standort
    k_haus, k_standort = _kern(haus), _kern(standort)
    if k_haus and k_haus in k_standort:
        return standort
    w_haus, w_standort = k_haus.split(), k_standort.split()
    if len(w_haus) >= 2 and len(w_standort) >= 3 and w_haus[:2] == w_standort[:2]:
        return standort  # "Bezirkskrankenhaus Landshut Fachklinik Passau"
    if (k_standort in k_haus or set(k_standort.split()) <= set(k_haus.split())
            or difflib.SequenceMatcher(None, k_haus, k_standort).ratio() >= 0.9):
        return haus  # "Universitätsklinikum des Saarlandes (UKS)" under "UKS - Uni…"
    return f"{haus} – {standort}"


def _psychbetten(zeile: dict[str, str]) -> int:
    return sum(ganzzahl(v) for k, v in zeile.items() if v and _PSYCH_FA.match(k))


# --- sources ----------------------------------------------------------------------------------

def lies_destatis(daten: bytes) -> tuple[dict[str, str], list[dict[str, str]]]:
    """Hospital sites (KHV) and rehabilitation facilities (RHV) as output rows plus ``ags``."""
    mappe = Arbeitsmappe(daten)
    khv, rhv = mappe.blatt(r"KHV_\d{4}"), mappe.blatt(r"RHV_\d{4}")
    jahr = khv[-4:]
    impressum = [v for z in mappe.roh(mappe.blatt("Impressum")) for v in z.values() if v]
    erschienen = next((v for v in impressum if v.startswith("Erschienen")), "")
    copyright_ = next((leerzeichen(v) for v in impressum
                       if v.startswith("©") and "Statistisches Bundesamt" in v), "")
    meta = {"jahr": jahr, "erschienen": erschienen.rstrip("."), "copyright": copyright_}

    kh = [z for z in mappe.tabelle(khv, lambda z: z.get("A") == "KH_ID_Pseudo")
          if z.get("KH_ID_Pseudo")]
    rh = [z for z in mappe.tabelle(rhv, lambda z: z.get("A") == "RH_ID_Pseudo")
          if z.get("RH_ID_Pseudo")]

    # hospital-level share of psychiatric beds, for sites without beds of their own
    haus_psych: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0])
    for z in kh:
        haus_psych[z["KH_ID_Pseudo"]][0] += _psychbetten(z)
        haus_psych[z["KH_ID_Pseudo"]][1] += ganzzahl(z.get("Betten", ""))

    def gemeinsam(z: dict[str, str], art: str) -> dict[str, str]:
        plz = z.get("PLZ", "")
        return {
            "land": "DE",
            "strasse": leerzeichen(f"{z.get('Straße', '')} {z.get('Hausnummer', '')}"),
            "plz": plz.zfill(5) if plz.isdigit() else plz,
            "ort": leerzeichen(z.get("Ort", "")),
            "traeger": leerzeichen(z.get("Trägername", "")),
            "betten": str(ganzzahl(z.get("Betten", ""))),
            "quelle": f"destatis-{art}-{jahr}",
            "ags": (z.get("Land", "").zfill(2) + z.get("Kreis", "").zfill(3)
                    + z.get("Gemeinde", "").zfill(3)),
        }

    zeilen: list[dict[str, str]] = []
    laufend: collections.Counter[str] = collections.Counter()
    for z in kh:
        hid = z["KH_ID_Pseudo"]
        laufend[hid] += 1
        name = standortname(z.get("KH_Name", ""), z.get("Standortname", ""))
        betten, psych = ganzzahl(z.get("Betten", "")), _psychbetten(z)
        if betten > 0:
            typ = "psychiatrie" if 2 * psych > betten else "krankenhaus"
        else:
            # no beds (day clinic, outpatient unit): by name, else by the hospital as a whole;
            # a day clinic or outpatient unit of a hospital with psychiatric beds counts as
            # psychiatric, since somatic day care rarely forms a site of its own
            p, t = haus_psych[hid]
            psychiatrisch = (_PSYCH_WORT.search(name) or (t > 0 and 2 * p > t)
                             or (p > 0 and _TAGESKLINIK.search(name)))
            typ = "psychiatrie" if psychiatrisch else "krankenhaus"
        zeilen.append({**gemeinsam(z, "khv"), "kennung": f"KH{hid}-{laufend[hid]}",
                       "name": name, "typ": typ})
    for z in rh:
        zeilen.append({**gemeinsam(z, "rhv"), "kennung": f"RH{z['RH_ID_Pseudo']}",
                       "name": leerzeichen(z.get("RH_Name", "")), "typ": "reha"})
    meta["khv"], meta["rhv"] = str(len(kh)), str(len(rh))
    meta["krankenhaeuser"] = str(len({z["KH_ID_Pseudo"] for z in kh}))
    return meta, zeilen


def lies_bag(daten: bytes) -> tuple[dict[str, str], list[dict[str, str]]]:
    """Swiss hospital sites with the enterprise's type and (for single sites) beds."""
    mappe = Arbeitsmappe(daten)
    standorte_blatt = mappe.blatt(r"Standorte_KZ\d{4}_KZP\d\d")
    betriebe_blatt = mappe.blatt(standorte_blatt.removeprefix("Standorte_"))
    jahr = standorte_blatt[len("Standorte_KZ"):][:4]

    def ist_kopf(z: dict[str, str]) -> bool:
        return z.get("A") == "JAHR"

    betriebe = {(z["KT"], z["Inst"]): z for z in mappe.tabelle(betriebe_blatt, ist_kopf)
                if z.get("KT") and z.get("Inst")}
    zeilen = []
    for s in mappe.tabelle(standorte_blatt, ist_kopf):
        if not s.get("Inst"):
            continue
        b = betriebe[(s["KT"], s["Inst"])]
        plz, _, ort = s.get("Ort_Standort", "").partition(" ")
        betten = ""
        if b.get("AnzStand") == "1":
            betten = str(sum(ganzzahl(b.get(f"BettenStat{a}", "")) for a in "APRB"))
        schluessel = "|".join([s["KT"], s["Inst"], s.get("Standort", ""),
                               s.get("Adr_Standort", ""), s.get("Ort_Standort", "")])
        zeilen.append({
            "land": "CH",
            "kennung": "kzp-" + hashlib.sha1(schluessel.encode("utf-8")).hexdigest()[:10],
            "name": standortname(s["Inst"], s.get("Standort", "")),
            "typ": TYPEN_CH.get(b.get("Typ", ""), "krankenhaus"),
            "strasse": leerzeichen(s.get("Adr_Standort", "")),
            "plz": plz, "ort": leerzeichen(ort),
            "traeger": leerzeichen(s["Inst"]), "betten": betten,
            "quelle": f"bag-kzp-{jahr}",
            "kanton": s.get("KT_Standort") or s["KT"],
        })
    return {"jahr": jahr, "betriebe": str(len(betriebe)), "standorte": str(len(zeilen))}, zeilen


# --- main -------------------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--destatis", help="Krankenhausverzeichnis .xlsx (sonst Download)")
    ap.add_argument("--bag", help="kzp23_data.xlsx (sonst Download)")
    ap.add_argument("--ortschaften", help="ortschaftenverzeichnis_plz_2056.csv.zip")
    ap.add_argument("--ziel", type=Path, default=ZIEL)
    args = ap.parse_args(argv[1:])

    de_meta, de = lies_destatis(lade(args.destatis, URL_DESTATIS))
    schluessel = Gemeindeschluessel(GEMEINDEN)
    verfahren_de: collections.Counter[str] = collections.Counter()
    offen_de = []
    for z in de:
        ars, verfahren = schluessel.ars(z.pop("ags"), z["plz"], z["ort"])
        z["gemeinde_kennung"] = ars
        verfahren_de[verfahren or "offen"] += 1
        if verfahren not in ("schluessel", ""):
            offen_de.append(f"  {verfahren}: {z['plz']} {z['ort']} → {ars} ({z['name']})")
        elif not verfahren:
            offen_de.append(f"  offen: {z['plz']} {z['ort']} ({z['name']})")

    ch_meta, ch = lies_bag(lade(args.bag, URL_BAG))
    orte = Ortschaften(lade(args.ortschaften, URL_ORTSCHAFTEN))
    verfahren_ch: collections.Counter[str] = collections.Counter()
    offen_ch = []
    for z in ch:
        nummer, verfahren = orte.bfs(z["plz"], z["ort"], z.pop("kanton"))
        z["gemeinde_kennung"] = nummer
        verfahren_ch[verfahren or "offen"] += 1
        if verfahren in ("plz-anteil", "gemeinde-statt-plz", ""):
            offen_ch.append(f"  {verfahren or 'offen'}: {z['plz']} {z['ort']} → {nummer}"
                            f" ({z['name']})")

    zeilen = de + ch
    gebietsstand = (re.search(r"Gebietsstand ([\d-]+)", schluessel.quelle) or [None, "?"])[1]
    kopf = (
        f"# Quelle: DE Statistische Ämter des Bundes und der Länder, Verzeichnis der "
        f"Krankenhäuser und Vorsorge- oder Rehabilitationseinrichtungen in Deutschland "
        f"{de_meta['jahr']} (Destatis, {de_meta['erschienen']}), verändert: Standortnamen "
        f"zusammengeführt, Typ abgeleitet, ARS aus Destatis GV-ISys ergänzt (Gebietsstand "
        f"{gebietsstand}); CH Bundesamt für Gesundheit (BAG), Kennzahlen der Schweizer "
        f"Spitäler {ch_meta['jahr']}, Standorte, verändert: BFS-Nr. aus swisstopo Amtliches "
        f"Ortschaftenverzeichnis ({orte.stand}) ergänzt; AT nicht enthalten; "
        f"Lizenz: DE Vervielfältigung und Verbreitung mit Quellenangabe gestattet "
        f"({de_meta['copyright']}), CH opendata.swiss terms_by (freie "
        f"Nutzung, Quellenangabe Pflicht), swisstopo terms_open; "
        f"Stand: DE 31.12.{de_meta['jahr']}, CH {ch_meta['jahr']}\n"
    )
    args.ziel.parent.mkdir(parents=True, exist_ok=True)
    with open(args.ziel, "wb") as roh, \
            gzip.GzipFile(filename="", mode="wb", fileobj=roh, mtime=0) as gz, \
            io.TextIOWrapper(gz, encoding="utf-8", newline="") as f:
        f.write(kopf)
        w = csv.DictWriter(f, fieldnames=SPALTEN, lineterminator="\n", extrasaction="raise")
        w.writeheader()
        w.writerows(zeilen)

    typen = collections.Counter((z["land"], z["typ"]) for z in zeilen)
    print(f"DE: {de_meta['khv']} Krankenhausstandorte ({de_meta['krankenhaeuser']} Häuser), "
          f"{de_meta['rhv']} Vorsorge-/Reha-Einrichtungen, Berichtsjahr {de_meta['jahr']}")
    print(f"CH: {ch_meta['standorte']} Standorte von {ch_meta['betriebe']} Spitalbetrieben, "
          f"Datenjahr {ch_meta['jahr']}")
    for (land, typ), n in sorted(typen.items()):
        print(f"  {land} {typ}: {n}")
    print("Gemeindeschlüssel DE:", dict(verfahren_de.most_common()))
    print("Gemeindeschlüssel CH:", dict(verfahren_ch.most_common()))
    print("DE ohne direkten Schlüssel:", *sorted(set(offen_de)), sep="\n")
    print("CH mehrdeutig oder offen:", *offen_ch, sep="\n")
    print(f"{len(zeilen)} Zeilen nach {args.ziel} geschrieben"
          f" ({args.ziel.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
