"""Reference data the user loads on purpose, straight from the publisher.

Some lists may not travel with PseudonyMed under its free licence.  The Austrian ministry of
health permits reprinting its directory of hospitals "except for commercial purposes", which
rules out bundling it in an AGPL repository that anyone may sell.  PseudonyMed therefore does not
ship it: on the user's request it fetches the current workbook from the ministry and writes
it into the user's own lexicon under a source of its own, which the reloading of the bundled
data leaves alone.  Nothing is passed on.
"""

from __future__ import annotations

import csv
import datetime as dt
import gzip
import hashlib
import json
import re
import sqlite3
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from pseudonymed import __version__
from pseudonymed.erkennung import lexikon

QUELLE_KLINIKEN_AT = "Kliniken AT (BMASGPK)"
SEITE_KLINIKEN_AT = (
    "https://www.sozialministerium.gv.at/Themen/Gesundheit/Gesundheitssystem/Krankenanstalten/"
    "Krankenanstalten-und-selbststaendige-Ambulatorien-in-Oesterreich/"
    "Krankenanstalten-in-Oesterreich.html")
HINWEIS_KLINIKEN_AT = (
    "Quelle: Bundesministerium für Arbeit, Soziales, Gesundheit, Pflege und Konsumentenschutz"
    " (BMASGPK), Liste der Krankenanstalten in Österreich. Das Ministerium gestattet den"
    " Nachdruck mit Quellenangabe außer zu kommerziellen Zwecken. PseudonyMed lädt die Liste nur"
    " in Ihre lokale Datenbank und gibt sie nicht weiter.")
AGENT = f"PseudonyMed/{__version__} (https://github.com/fre-ms/PseudonyMed)"
_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
       "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
_PLZ_ORT = re.compile(r"(?:^|,\s*)(\d{4})\s+([^,]+?)\s*$")


class ZusatzFehler(RuntimeError):
    pass


@dataclass
class Ladebericht:
    quelle: str
    anzahl: int
    ohne_gemeinde: int
    stand: str
    herkunft: str

    def beschreibung(self) -> str:
        teil = f", {self.ohne_gemeinde} ohne Gemeinde" if self.ohne_gemeinde else ""
        return (f"{self.quelle}: {self.anzahl} Einträge geladen{teil}"
                f" (Stand {self.stand or 'unbekannt'}, {self.herkunft}).")


def abrufen(url: str, zeit: int = 60) -> bytes:
    """Plain HTTPS GET with PseudonyMed's own user agent; nothing else is sent."""
    if not url.startswith("https://"):
        raise ZusatzFehler(f"nur https erlaubt: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": AGENT})
    with urllib.request.urlopen(req, timeout=zeit) as antwort:
        return antwort.read()


def xlsx_blaetter(daten: bytes) -> dict[str, list[list[str]]]:
    """Every sheet of a workbook as rows of cell texts (shared and inline strings, values)."""
    z = zipfile.ZipFile(BytesIO(daten))
    strings: list[str] = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", _NS):
            strings.append("".join(t.text or "" for t in si.iter(f"{{{_NS['m']}}}t")))
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = {r.get("Id"): r.get("Target", "")
            for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
    aus: dict[str, list[list[str]]] = {}
    blaetter = wb.find("m:sheets", _NS)
    for blatt in (list(blaetter) if blaetter is not None else []):
        ziel = rels.get(blatt.get(f"{{{_NS['r']}}}id"), "").lstrip("/")
        ziel = ziel if ziel.startswith("xl/") else "xl/" + ziel
        zeilen: list[list[str]] = []
        daten_xml = ET.fromstring(z.read(ziel)).find("m:sheetData", _NS)
        for row in (daten_xml.findall("m:row", _NS) if daten_xml is not None else []):
            werte: dict[int, str] = {}
            for c in row.findall("m:c", _NS):
                spalte = 0
                for ch in re.sub(r"\d", "", c.get("r", "")):
                    spalte = spalte * 26 + ord(ch) - 64
                if c.get("t") == "inlineStr":
                    v = "".join(t.text or "" for t in c.iter(f"{{{_NS['m']}}}t"))
                else:
                    v = c.findtext("m:v", default="", namespaces=_NS)
                    if c.get("t") == "s" and v:
                        v = strings[int(v)]
                werte[max(spalte, 1) - 1] = " ".join(v.split())
            zeilen.append([werte.get(i, "") for i in range(max(werte, default=-1) + 1)])
        aus[blatt.get("name", "")] = zeilen
    return aus


def mappe_finden(html: str, basis: str = SEITE_KLINIKEN_AT) -> str:
    """The link to the current workbook on the ministry's page."""
    links = re.findall(r'href="([^"]+\.xlsx[^"]*)"', html, flags=re.IGNORECASE)
    passend = [u for u in links if re.search(r"verzeichnis|krankenanstalt", urllib.parse.unquote(u),
                                              flags=re.IGNORECASE)] or links
    if not passend:
        raise ZusatzFehler("Auf der Seite des Ministeriums wurde keine Excel-Mappe gefunden.")
    return urllib.parse.urljoin(basis, passend[0].replace("&amp;", "&"))


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.casefold())
    return re.sub(r"[^a-z0-9]+", " ", "".join(c for c in text if not unicodedata.combining(c))
                  ).strip()


def _at_tabellen() -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Gemeinde code → name, Bezirk code → name (bundled demography, Statistik Austria) and
    postcode → Gemeinde code with the largest share (bundled postcode table, if present)."""
    gemeinden: dict[str, str] = {}
    bezirke: dict[str, str] = {}
    pfad = lexikon.DATEN / "demografie_dach.csv.gz"
    if pfad.exists():
        with gzip.open(pfad, "rt", encoding="utf-8") as f:
            for z in csv.DictReader(x for x in f if not x.startswith("#")):
                if z["land"] == "AT" and z["ebene"] == "kreis":
                    bezirke[z["kennung"]] = z["name"]
                elif z["land"] == "AT" and z["ebene"] == "gemeinde":
                    gemeinden[z["kennung"]] = z["name"]
    plz: dict[str, tuple[float, str]] = {}
    pfad = lexikon.DATEN / "plz_dach.csv.gz"
    if pfad.exists():
        with gzip.open(pfad, "rt", encoding="utf-8") as f:
            for z in csv.DictReader(x for x in f if not x.startswith("#")):
                if z.get("land") != "AT":
                    continue
                try:
                    anteil = float(z.get("anteil") or 0)
                except ValueError:
                    anteil = 0.0
                if anteil > plz.get(z["plz"], (-1.0, ""))[0]:
                    plz[z["plz"]] = (anteil, z["gemeinde_kennung"])
    return gemeinden, bezirke, {k: v for k, (_a, v) in plz.items()}


def _woerter(text: str) -> set[str]:
    return {w for w in _norm(text).split() if len(w) >= 4 and w not in ("stadt", "land")}


def _gemeinde_at(ort: str, plz: str, bezirk: str,
                 tabellen: tuple[dict[str, str], dict[str, str], dict[str, str]]) -> str:
    """Gemeinde code for a site without one: Vienna's district from the postcode, the
    postcode table, then the best word match inside the Bezirk, then a unique name."""
    gemeinden, bezirke, nach_plz = tabellen
    if re.fullmatch(r"1\d{2}0", plz) and 1 <= int(plz[1:3]) <= 23:
        kennung = f"9{int(plz[1:3]):02d}01"
        if kennung in gemeinden or not gemeinden:
            return kennung
    if plz in nach_plz:
        return nach_plz[plz]
    codes = {k for k, n in bezirke.items() if _norm(n) == _norm(bezirk)} or \
        {k for k, n in bezirke.items() if _woerter(n) & _woerter(bezirk)}
    woerter = _woerter(ort)
    beste = sorted(((len(woerter & _woerter(n)), k) for k, n in gemeinden.items()
                    if k[:3] in codes), reverse=True)
    if beste and beste[0][0] > 0 and (len(beste) == 1 or beste[1][0] < beste[0][0]):
        return beste[0][1]
    gleich = [k for k, n in gemeinden.items() if _norm(n) == _norm(ort)]
    return gleich[0] if len(gleich) == 1 else ""


def _spalten(kopf: list[str], *namen: str) -> list[int | None]:
    aus: list[int | None] = []
    for name in namen:
        aus.append(next((i for i, k in enumerate(kopf) if k.casefold().startswith(name.casefold())),
                        None))
    return aus


def _wert(zeile: list[str], i: int | None) -> str:
    return zeile[i] if i is not None and i < len(zeile) else ""


def kliniken_at_zeilen(daten: bytes) -> tuple[list[lexikon.Zeile], int]:
    """Lexicon rows from the ministry's workbook: one per site with its Gemeinde code, or one
    per hospital when the workbook lists no site; returns (rows, rows without Gemeinde)."""
    blaetter = xlsx_blaetter(daten)
    gesamt = next((z for n, z in blaetter.items() if n.casefold().startswith("ka gesamt")), None)
    if not gesamt:
        raise ZusatzFehler("Die Mappe hat kein Blatt „KA gesamt“.")
    kopf = gesamt[0]
    i_nr, i_name, i_sektor, i_bereich, i_bezirk, i_adresse, i_betten, i_land = _spalten(
        kopf, "KA-Nr", "Bezeichnung", "Versorgungssektor", "Versorgungsbereich", "Bezirk",
        "Adresse", "Bettenanzahl", "Bundesland")
    i_traeger = next((i for i, k in enumerate(kopf) if "träger" in k.casefold()
                      and "nr" not in k.casefold()), None)
    if i_nr is None or i_name is None:
        raise ZusatzFehler("Die Mappe hat nicht die erwarteten Spalten (KA-Nr., Bezeichnung).")
    standorte: dict[str, list[tuple[str, str, str, str]]] = {}
    roh = next((z for n, z in blaetter.items() if n.casefold().startswith("rohdaten")), [])
    if roh:
        s_code, s_gem, s_ort = _spalten(roh[0], "KA-Nr", "Gemeinde", "Ort")
        gesehen: set[str] = set()
        for z in roh[1:]:
            code = _wert(z, s_code)
            if not code or code in gesehen:
                continue
            gesehen.add(code)
            gem = _wert(z, s_gem)
            m = re.match(r"(\d{5})\s+(.*)", gem)
            plz_ort = re.match(r"(\d{4})\s+(.*)", _wert(z, s_ort))
            standorte.setdefault(code.split(".")[0], []).append(
                (code, m.group(1) if m else "", plz_ort.group(1) if plz_ort else "",
                 plz_ort.group(2) if plz_ort else (m.group(2) if m else "")))
    tabellen = _at_tabellen()
    zeilen: list[lexikon.Zeile] = []
    ohne = 0
    for z in gesamt[1:]:
        nr, name = _wert(z, i_nr), _wert(z, i_name)
        if not nr or not name:
            continue
        sektor, bereich = _wert(z, i_sektor), _wert(z, i_bereich)
        typ = ("reha" if "rehab" in sektor.casefold() else
               "psychiatrie" if "psych" in (bereich + " " + name).casefold() else "krankenhaus")
        m = _PLZ_ORT.search(_wert(z, i_adresse))
        plz, ort = (m.group(1), m.group(2)) if m else ("", "")
        try:
            betten = int(float(_wert(z, i_betten))) if _wert(z, i_betten) else None
        except ValueError:
            betten = None
        orte = standorte.get(nr) or [(nr, "", plz, ort)]
        for code, gkz, s_plz, s_ort in orte:
            if not gkz:
                gkz = _gemeinde_at(s_ort, s_plz, _wert(z, i_bezirk), tabellen)
            ohne += 0 if gkz else 1
            zusatz = json.dumps({k: v for k, v in {
                "typ": typ, "ort": s_ort, "plz": s_plz, "gemeinde": gkz, "land": "AT",
                "bundesland": _wert(z, i_land), "betten": betten,
                "traeger": _wert(z, i_traeger)}.items() if v not in ("", None)},
                ensure_ascii=False, separators=(",", ":"))
            zeilen.append((name, "Klinik", lexikon.MERKMAL, f"AT:{code}", zusatz))
    return zeilen, ohne


def lade_kliniken_at(verbindung: sqlite3.Connection, datei: Path | None = None,
                     holen: Callable[[str], bytes] = abrufen) -> Ladebericht:
    """Fetch (or read) the ministry's workbook and replace the source in the lexicon."""
    if datei is None:
        seite = holen(SEITE_KLINIKEN_AT).decode("utf-8", errors="replace")
        url = mappe_finden(seite)
        daten, herkunft = holen(url), url
    else:
        daten, herkunft = Path(datei).read_bytes(), str(datei)
    zeilen, ohne = kliniken_at_zeilen(daten)
    if not zeilen:
        raise ZusatzFehler("Die Mappe enthält keine Krankenanstalten.")
    datum = re.search(r"(\d{4}-\d{2}-\d{2})", urllib.parse.unquote(herkunft))
    stand = datum.group(1) if datum else ""
    verbindung.executescript(lexikon.SCHEMA)
    verbindung.execute("DELETE FROM lexikon WHERE quelle = ?", (QUELLE_KLINIKEN_AT,))
    vorher = verbindung.total_changes
    verbindung.executemany(
        "INSERT OR IGNORE INTO lexikon (begriff, klasse, rolle, quelle, kennung, zusatz)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        ((b, k, r, QUELLE_KLINIKEN_AT, ke, zu) for b, k, r, ke, zu in zeilen))
    n = verbindung.total_changes - vorher
    verbindung.execute(
        "INSERT OR REPLACE INTO lexikon_meta (quelle, stand, anzahl, geladen) VALUES (?, ?, ?, ?)",
        (QUELLE_KLINIKEN_AT, f"{stand} {hashlib.sha256(daten).hexdigest()[:16]}".strip(), n,
         dt.datetime.now().isoformat(timespec="seconds")))
    verbindung.commit()
    return Ladebericht(QUELLE_KLINIKEN_AT, n, ohne, stand, herkunft)


def geladene(verbindung: sqlite3.Connection) -> list[tuple[str, int, str]]:
    """User-loaded sources: (source, rows, loaded at)."""
    gebuendelt = {q.name for q in lexikon.QUELLEN}
    try:
        return [(q, n, g) for q, n, g in verbindung.execute(
            "SELECT quelle, anzahl, geladen FROM lexikon_meta ORDER BY quelle")
            if q not in gebuendelt]
    except sqlite3.OperationalError:
        return []
