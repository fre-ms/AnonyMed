"""Build ``anonymed/daten/plz_dach.csv.gz``: postcode -> municipality -> district, DACH.

The residual-risk check estimates the catchment area of the practice (the districts its
patients come from) locally from the postcodes in the address book.  This file maps every
postcode of Germany, Austria and Switzerland to the municipalities it covers, with the
district of each municipality and the estimated share of the postcode's residents living
there.  The keys match ``gemeinden_de.csv.gz`` and ``demografie_dach.csv.gz``.

Sources and licences
--------------------
Germany (DE): street directory of the OpenPLZ API project, file
``src/de/osm/streets.updated.csv`` of https://github.com/openpotato/openplzapi.data, an
extract from OpenStreetMap that lists every named street with the postcode area and the
municipality (8-digit AGS) its centre lies in.  Licence: Open Database License (ODbL) 1.0,
"© OpenStreetMap contributors".  Municipality keys (ARS), names and populations come from
``gemeinden_de.csv.gz`` (Destatis, Datenlizenz Deutschland - Namensnennung - 2.0); keys that
changed after the OSM extract are followed through the Destatis lists of territorial changes
("Gebietsänderungen", ``DE_AENDERUNGEN``; streets of dissolved uninhabited areas count as
streets, not as residential units).  Populated municipalities without any OSM street get one
unit at the postcode of their seat, and postcodes of a municipal seat for which OSM has no
area (mostly PO box or large-customer postcodes) are added from the same directory.

Austria (AT): BEV - Bundesamt für Eich- und Vermessungswesen, "Adresse Relationale Tabellen -
Stichtagsdaten" (every address with postcode, municipality and census district; buildings
with their prevailing use), https://data.bev.gv.at.  Licence: CC BY 4.0, attribution
"© Österreichisches Adressregister, Stichtagsdaten vom DD.MM.YYYY".  Vienna is split into
its 23 districts (Gemeindebezirke 90101-92301, districts 901-923) by the census district
(Zählsprengel) of each address.

Switzerland (CH): Federal Office of Topography swisstopo, "Amtliches
Gebäudeadressverzeichnis" (every building address with postcode, BFS municipality number,
building category and status) and, as cross-check and fallback, "Amtliches
Ortschaftenverzeichnis mit Postleitzahl und Perimeter", both from https://data.geo.admin.ch.
Terms: swisstopo OGD - free use including commercial use, source "Bundesamt für
Landestopografie swisstopo" compulsory
(https://www.swisstopo.admin.ch/de/nutzungsbedingungen-kostenlose-geodaten-und-geodienste).
Districts and mergers from the BFS "Amtliches Gemeindeverzeichnis der Schweiz" (REST service
https://www.agvchapp.bfs.admin.ch; BFS terms OPEN BY).  Municipalities are written in the
territorial state of ``demografie_dach.csv.gz`` (STATPOP reference date), so that every key
can be joined: an address of a commune merged since then goes to the former commune named
like its postal locality, else to the surviving former commune, else to all former communes
by population.

Share (``anteil``)
------------------
For a postcode P and a municipality G the residents of P living in G are estimated as
``population(G) * units(P, G) / units(G)``: the population of the municipality, spread over
its postcodes in proportion to its residential units.  Units are residential addresses in AT
(addresses with a building of use 01-03) and CH (building categories residential,
other_residential and partly_residential, status real), and named streets in DE.
``anteil`` is that estimate divided by its sum over all municipalities of P, written with four
decimals and summing to exactly 1 per postcode; pairs without estimated residents and shares
below 0.00005 are dropped.  If no
municipality of P has residents and residential units, all addresses (DE: streets) weighted by
population are used, then unweighted addresses, then the fallback weights (DE seat
postcodes: population; CH perimeter-only postcodes: swisstopo address share).
Populations: DE ``gemeinden_de.csv.gz``, AT and CH municipality totals of
``demografie_dach.csv.gz``.

Output
------
A comment line ``# Quelle: ...; Lizenz: ...; Stand: ...``, then the columns
``land,plz,ort,gemeinde_kennung,gemeinde_name,kreis_kennung,anteil``.  ``gemeinde_kennung``
is the 12-digit ARS (DE), the 5-digit Gemeindekennziffer (AT, Vienna by district) or the BFS
number (CH); ``kreis_kennung`` the 5-digit AGS of the Kreis (DE), the 3-digit political
district (AT) or the BFS district number (CH).  ``ort`` is the most frequent postal locality
of the pair (DE: OSM locality, AT: Zustellort, CH: locality of the postcode label).  gzip
without time stamp, so equal downloads give a byte-identical file.  Because the German rows
are derived from OpenStreetMap, the file as a whole is distributed under the ODbL 1.0.

Usage: python -I werkzeuge/plz_dach.py [--roh ORDNER] [--ziel DATEI]

With ``--roh`` the downloads are kept in (and re-read from) ``ORDNER/<source>/``.  The
certificate of data.bev.gv.at chains to "HARICA TLS RSA Root CA 2021", which older CA bundles
(e.g. macOS ``/etc/ssl/cert.pem``) lack; then point ``SSL_CERT_FILE`` at a current bundle.
Certificate checks are never switched off.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import html
import io
import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

DATEN = Path(__file__).resolve().parent.parent / "anonymed" / "daten"
ZIEL = DATEN / "plz_dach.csv.gz"
GEMEINDEN_DE = DATEN / "gemeinden_de.csv.gz"
DEMOGRAFIE = DATEN / "demografie_dach.csv.gz"
KOPF = ["land", "plz", "ort", "gemeinde_kennung", "gemeinde_name", "kreis_kennung", "anteil"]
AGENT = {"User-Agent": "AnonyMed build script (https://github.com/fre-ms/AnonyMed)"}
STELLEN = 10_000  # shares are written in units of 1/10,000

DE_REPO = "openpotato/openplzapi.data"
DE_PFAD = "src/de/osm/streets.updated.csv"
DE_COMMITS = f"https://api.github.com/repos/{DE_REPO}/commits?path={DE_PFAD}&per_page=1"
DE_ROH = f"https://raw.githubusercontent.com/{DE_REPO}/{{sha}}/{DE_PFAD}"
DE_AENDERUNGEN = ("https://www.destatis.de/DE/Themen/Laender-Regionen/Regionales/"
                  "Gemeindeverzeichnis/Namens-Grenz-Aenderung/")

AT_ADRESSEN = ("https://data.bev.gv.at/download/Adressregister/"
               "Adresse_Relationale_Tabellen_Stichtagsdaten.zip")
AT_WOHNEN = {"01", "02", "03"}  # one dwelling, two or more dwellings, communal residence
AT_WIEN = "90001"  # Vienna as one municipality in the address register

CH_ADRESSEN = ("https://data.geo.admin.ch/ch.swisstopo.amtliches-gebaeudeadressverzeichnis/"
               "amtliches-gebaeudeadressverzeichnis_ch/"
               "amtliches-gebaeudeadressverzeichnis_ch_2056.csv.zip")
CH_ORTSCHAFTEN = ("https://data.geo.admin.ch/ch.swisstopo-vd.ortschaftenverzeichnis_plz/"
                  "ortschaftenverzeichnis_plz/ortschaftenverzeichnis_plz_2056.csv.zip")
CH_AGV = "https://www.agvchapp.bfs.admin.ch/api/communes/"
CH_WOHNEN = {"residential", "other_residential", "partly_residential"}

# Plausibility: expected number of postcodes and the district of well-known postcodes.
ERWARTET = {"DE": (8_000, 8_400), "AT": (2_100, 2_400), "CH": (3_000, 3_400)}
STICHPROBEN = {("DE", "86150"): "09761", ("DE", "80331"): "09162", ("DE", "10115"): "11000",
               ("AT", "1010"): "901", ("AT", "8010"): "601", ("CH", "8001"): "112",
               ("CH", "3011"): "246"}
MINDESTABDECKUNG = 0.98  # share of districts that must have at least one postcode

Paar = tuple[str, str]  # (postcode, municipality key)
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


@dataclass
class Land:
    """Counts of one country per (postcode, municipality) and the metadata to write them."""

    code: str
    wohnen: dict[Paar, float] = field(default_factory=lambda: defaultdict(float))
    alle: dict[Paar, float] = field(default_factory=lambda: defaultdict(float))
    ersatz: dict[Paar, float] = field(default_factory=lambda: defaultdict(float))
    orte: dict[Paar, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))
    einwohner: dict[str, int] = field(default_factory=dict)
    namen: dict[str, str] = field(default_factory=dict)
    kreis: dict[str, str] = field(default_factory=dict)
    stand: str = ""
    hinweise: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- helpers

def _hole(url: str, roh: Path | None, datei: str, versuche: int = 3,
          timeout: int = 900) -> bytes:
    """Return the body of ``url``, cached as ``roh/datei`` if a folder is given."""
    pfad = roh / datei if roh else None
    if pfad and pfad.exists():
        return pfad.read_bytes()
    for versuch in range(1, versuche + 1):
        try:
            anfrage = urllib.request.Request(url, headers=AGENT)
            with urllib.request.urlopen(anfrage, timeout=timeout) as antwort:
                inhalt = antwort.read()
            break
        except urllib.error.URLError as fehler:
            if isinstance(fehler.reason, ssl.SSLCertVerificationError):
                raise SystemExit(
                    f"{url}: Zertifikat nicht prüfbar ({fehler.reason.verify_message}). Dem"
                    " CA-Bündel dieser Python-Installation fehlt vermutlich die Wurzel (z. B."
                    " HARICA TLS RSA Root CA 2021 bei data.bev.gv.at); SSL_CERT_FILE auf ein"
                    " aktuelles Bündel setzen oder die Datei selbst nach ORDNER/<quelle>/"
                    f"{datei} laden und mit --roh ORDNER bauen.") from fehler
            if versuch == versuche:
                raise
            print(f"  {url}: {fehler}; neuer Versuch in {20 * versuch} s", file=sys.stderr)
            time.sleep(20 * versuch)
        except OSError as fehler:
            if versuch == versuche:
                raise
            print(f"  {url}: {fehler}; neuer Versuch in {20 * versuch} s", file=sys.stderr)
            time.sleep(20 * versuch)
    if pfad:
        pfad.parent.mkdir(parents=True, exist_ok=True)
        pfad.write_bytes(inhalt)
    return inhalt


def _csv(z: zipfile.ZipFile, name: str, trenner: str = ";") -> Iterator[dict[str, str]]:
    """Rows of a CSV member of a zip archive (UTF-8, optional byte order mark)."""
    with z.open(name) as roh:
        yield from csv.DictReader(io.TextIOWrapper(roh, encoding="utf-8-sig", newline=""),
                                  delimiter=trenner)


def _xlsx_zeilen(inhalt: bytes, muster: str) -> Iterator[dict[str, str]]:
    """Rows ``{column letter: text}`` of the first sheet whose name contains ``muster``."""
    z = zipfile.ZipFile(io.BytesIO(inhalt))
    strings: list[str] = []
    if "xl/sharedStrings.xml" in z.namelist():
        ss = ET.fromstring(z.read("xl/sharedStrings.xml"))
        strings = ["".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t"))
                   for si in ss.findall("m:si", NS)]
    blaetter = ET.fromstring(z.read("xl/workbook.xml")).find("m:sheets", NS)
    rid = next(b.get(REL_ID) for b in blaetter if muster in (b.get("name") or "").lower())
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    ziel = next(r.get("Target", "") for r in rels if r.get("Id") == rid)
    pfad = ziel.lstrip("/") if ziel.startswith("/") else f"xl/{ziel}"
    for zeile in ET.fromstring(z.read(pfad)).find("m:sheetData", NS):
        aus: dict[str, str] = {}
        for c in zeile.findall("m:c", NS):
            spalte = "".join(ch for ch in c.get("r", "") if ch.isalpha())
            wert = c.findtext("m:v", default="", namespaces=NS)
            if c.get("t") == "s" and wert:
                wert = strings[int(wert)]
            elif c.get("t") == "inlineStr":
                wert = "".join(t.text or "" for t in c.iter(f"{{{NS['m']}}}t"))
            aus[spalte] = wert.strip()
        yield aus


def _haeufigster(zaehler: Counter[str]) -> str:
    """Most frequent non-empty name (ties broken alphabetically), else an empty string."""
    kandidaten = [(-n, name) for name, n in zaehler.items() if name]
    return min(kandidaten)[1] if kandidaten else ""


def _demografie() -> tuple[dict[tuple[str, str, str], int], dict[tuple[str, str, str], str],
                           dict[str, str]]:
    """Population and name per (land, ebene, kennung) of districts and municipalities."""
    summe: dict[tuple[str, str, str], int] = defaultdict(int)
    namen: dict[tuple[str, str, str], str] = {}
    stichtag: dict[str, str] = {}
    with gzip.open(DEMOGRAFIE, "rt", encoding="utf-8", newline="") as f:
        next(f)
        for r in csv.DictReader(f):
            if r["ebene"] in ("kreis", "gemeinde"):
                schluessel = (r["land"], r["ebene"], r["kennung"])
                summe[schluessel] += int(r["anzahl"])
                namen[schluessel] = r["name"]
                stichtag[r["land"]] = r["stichtag"]
    return summe, namen, stichtag


# --------------------------------------------------------------------------- Germany

Nachfolger = dict[str, list[tuple[str, float, bool]]]


def _nachfolger_de(roh: Path | None, ab_jahr: int) -> Nachfolger:
    """Successors (AGS) of dissolved or re-keyed municipalities: (AGS, area moved, whether
    the moved part had residents)."""
    uebersicht = _hole(DE_AENDERUNGEN + "namens-grenz-aenderung.html", roh,
                       "namens-grenz-aenderung.html").decode("utf-8", "replace")
    teile: dict[str, str] = {}
    for teil in re.findall(r"Namens-Grenz-Aenderung/(\d{4}(?:-\d{2})?)\.html", uebersicht):
        if int(teil[:4]) >= ab_jahr:
            teile[teil[:4]] = max(teile.get(teil[:4], ""), teil)  # latest list per year
    if not teile:
        raise SystemExit("DE: keine Listen der Gebietsänderungen gefunden")
    nachfolger: Nachfolger = defaultdict(list)
    for teil in sorted(teile.values()):
        seite = _hole(DE_AENDERUNGEN + f"{teil}.html", roh, f"{teil}.html").decode(
            "utf-8", "replace")
        verweis = re.search(r'href="([^"]*Namens-Grenz-Aenderung/[^"]*\.xlsx[^"]*)"', seite)
        if not verweis:
            raise SystemExit(f"DE: keine Tabelle der Gebietsänderungen {teil} gefunden")
        url = "https://www.destatis.de" + html.unescape(verweis.group(1))
        for z in _xlsx_zeilen(_hole(url, roh, f"aenderungen_{teil}.xlsx"), "nderung"):
            # Columns: B unit type, D old AGS, F kind (1 dissolution, 3 new key), G area,
            # H residents moved (empty for a new key), J new AGS.
            alt, neu = z.get("D", ""), z.get("J", "")
            if (z.get("B") == "Gemeinde" and z.get("F") in ("1", "3") and len(alt) == 8
                    and len(neu) == 8 and alt != neu):
                nachfolger[alt].append((neu, float(z.get("G") or 0) or 1.0,
                                        z.get("H", "") != "0"))
    return nachfolger


def _aufloesen(ags: str, nachfolger: Nachfolger, bekannt: dict[str, str],
               tiefe: int = 0) -> list[tuple[str, float, bool]]:
    """Current municipalities (AGS) for ``ags``: the weight each one takes over (by area)
    and whether the part taken over had residents."""
    if ags in bekannt:
        return [(ags, 1.0, True)]
    ziele = nachfolger.get(ags, [])
    if tiefe > 5 or not ziele:
        return []
    flaeche = sum(f for _n, f, _b in ziele)
    return [(k, g * f / flaeche, bewohnt and b) for neu, f, bewohnt in ziele
            for k, g, b in _aufloesen(neu, nachfolger, bekannt, tiefe + 1)]


def deutschland(roh: Path | None, roh_aenderungen: Path | None) -> Land:
    """Germany: streets per (postcode, municipality) from the OpenPLZ OSM extract."""
    land = Land("DE")
    commit = json.loads(_hole(DE_COMMITS, roh, "commit.json"))[0]
    sha, datum = commit["sha"], commit["commit"]["committer"]["date"][:10]
    strassen = _hole(DE_ROH.format(sha=sha), roh, f"streets.updated_{sha[:12]}.csv")
    with gzip.open(GEMEINDEN_DE, "rt", encoding="utf-8", newline="") as f:
        kopf = f.readline()
        gemeinden = list(csv.DictReader(f))
    gebietsstand = re.search(r"Gebietsstand (\d{4}-\d{2}-\d{2})", kopf)
    land.stand = (f"OSM-Auszug {datum} (openplzapi.data {sha[:7]}), Gebietsstand"
                  f" {gebietsstand.group(1) if gebietsstand else '?'}")
    ars = {g["ars"][:5] + g["ars"][9:]: g["ars"] for g in gemeinden}
    for g in gemeinden:
        land.einwohner[g["ars"]] = int(g["einwohner"])
        land.namen[g["ars"]] = g["name"]
        land.kreis[g["ars"]] = g["ars"][:5]
    nachfolger = _nachfolger_de(roh_aenderungen, int(datum[:4]))
    offen: Counter[str] = Counter()
    umgeschluesselt: Counter[str] = Counter()
    for z in csv.DictReader(io.StringIO(strassen.decode("utf-8"))):
        plz, ags = z["PostalCode"], z["RegionalKey"]
        if not re.fullmatch(r"\d{5}", plz):
            continue
        ziele = _aufloesen(ags, nachfolger, ars)
        if not ziele:
            offen[ags] += 1
            continue
        if ags not in ars:
            umgeschluesselt[ags] += 1
        for a, gewicht, bewohnt in ziele:
            paar = (plz, ars[a])
            if bewohnt:  # streets of uninhabited dissolved areas carry no residents
                land.wohnen[paar] += gewicht
            land.alle[paar] += gewicht
            land.orte[paar][z["Locality"]] += gewicht
    # Populated municipalities without any OSM street: one unit at the postcode of the seat,
    # so their whole population is counted there.
    mit_strassen = {g for _p, g in land.alle}
    ohne = [g for g in gemeinden if int(g["einwohner"]) > 0 and g["ars"] not in mit_strassen
            and re.fullmatch(r"\d{5}", g["plz"])]
    for g in ohne:
        paar = (g["plz"], g["ars"])
        land.wohnen[paar] = land.alle[paar] = 1.0
        land.orte[paar][g["name"]] += 1
    vorhanden = {p for p, _g in land.alle}
    sitz = [g for g in gemeinden if re.fullmatch(r"\d{5}", g["plz"])
            and g["plz"] not in vorhanden]
    for g in sitz:
        paar = (g["plz"], g["ars"])
        land.ersatz[paar] = max(int(g["einwohner"]), 1)
        land.orte[paar][g["name"]] += 1
    land.hinweise += [
        f"{sum(umgeschluesselt.values())} Straßen aus {len(umgeschluesselt)} geänderten"
        f" Gemeindeschlüsseln nachgeführt ({', '.join(sorted(umgeschluesselt))})",
        f"{sum(offen.values())} Straßen ohne bekannte Gemeinde verworfen"
        f" ({', '.join(sorted(offen)) or 'keine'})",
        f"{len(ohne)} bewohnte Gemeinden ohne OSM-Straße mit der PLZ des Gemeindesitzes"
        f" ergänzt ({sum(int(g['einwohner']) for g in ohne)} Einwohner)",
        f"{len({g['plz'] for g in sitz})} PLZ von Gemeindesitzen ohne OSM-Gebiet ergänzt",
    ]
    return land


# --------------------------------------------------------------------------- Austria

def oesterreich(roh: Path | None, demo: tuple) -> Land:
    """Austria: addresses per (postcode, municipality) from the BEV address register."""
    summe, namen, _stichtag = demo
    land = Land("AT")
    z = zipfile.ZipFile(io.BytesIO(_hole(AT_ADRESSEN, roh,
                                         "Adresse_Relationale_Tabellen_Stichtagsdaten.zip")))
    datum = re.search(r"(\d{2})\.(\d{2})\.(\d{4})",
                      z.read("Aktualitaetsstand.txt").decode("utf-8", "replace"))
    if not datum:
        raise SystemExit("AT: Stichtag des Adressregisters nicht gefunden")
    land.stand = f"{datum.group(3)}-{datum.group(2)}-{datum.group(1)}"
    wohnadressen = {r["ADRCD"] for r in _csv(z, "GEBAEUDE.csv")
                    if r["EIGENSCHAFT"] in AT_WOHNEN}
    zustellort = {r["SKZ"]: r["ZUSTELLORT"] for r in _csv(z, "STRASSE.csv")}
    ortschaft = {(r["GKZ"], r["OKZ"]): r["ORTSNAME"] for r in _csv(z, "ORTSCHAFT.csv")}
    for r in _csv(z, "ADRESSE.csv"):
        if not re.fullmatch(r"\d{4}", r["PLZ"]):
            continue
        gemeinde = r["ZAEHLSPRENGEL"][:5] if r["GKZ"] == AT_WIEN else r["GKZ"]
        paar = (r["PLZ"], gemeinde)
        land.alle[paar] += 1
        if r["ADRCD"] in wohnadressen:
            land.wohnen[paar] += 1
        land.orte[paar][zustellort.get(r["SKZ"]) or ortschaft.get((r["GKZ"], r["OKZ"]), "")] += 1
    for (lnd, ebene, kennung), n in summe.items():
        if lnd == "AT" and ebene == "gemeinde":
            land.einwohner[kennung] = n
            land.namen[kennung] = namen[(lnd, ebene, kennung)]
            land.kreis[kennung] = kennung[:3]
    return land


# --------------------------------------------------------------------------- Switzerland

def _normiert(name: str) -> str:
    """Locality or municipality name without canton suffix and parenthesised additions."""
    name = re.sub(r"\s*\(.*?\)", "", name).strip()
    return re.sub(r"\s+[A-Z]{2}$", "", name).casefold()


def _datum_agv(iso: str) -> str:
    """ISO date to the DD-MM-YYYY form of the BFS REST service."""
    j, m, t = iso.split("-")
    return f"{t}-{m}-{j}"


def schweiz(roh_adressen: Path | None, roh_ortschaften: Path | None, roh_agv: Path | None,
            demo: tuple) -> Land:
    """Switzerland: addresses per (postcode, municipality) from swisstopo, BFS state."""
    summe, namen, stichtag = demo
    land = Land("CH")
    z = zipfile.ZipFile(io.BytesIO(_hole(CH_ADRESSEN, roh_adressen,
                                         "amtliches-gebaeudeadressverzeichnis_ch_2056.csv.zip")))
    eintrag = next(e for e in z.infolist() if e.filename.endswith(".csv"))
    land.stand = "{:04d}-{:02d}-{:02d}".format(*eintrag.date_time[:3])
    # Districts in the territorial state of the demography file, mergers since then.
    von, bis = _datum_agv(stichtag["CH"]), _datum_agv(land.stand)
    stand_alt = list(csv.DictReader(io.StringIO(_hole(
        f"{CH_AGV}snapshot?date={von}", roh_agv, f"snapshot_{von}.csv").decode("utf-8-sig"))))
    bezirke = {r["HistoricalCode"]: r["BfsCode"] for r in stand_alt if r["Level"] == "2"}
    for r in stand_alt:
        if r["Level"] == "3":
            land.kreis[r["BfsCode"]] = bezirke[r["Parent"]]
    vorgaenger: dict[str, list[str]] = defaultdict(list)
    for r in csv.DictReader(io.StringIO(_hole(
            f"{CH_AGV}correspondances?startPeriod={von}&endPeriod={bis}", roh_agv,
            f"correspondances_{von}_{bis}.csv").decode("utf-8-sig"))):
        vorgaenger[r["TerminalCode"]].append(r["InitialCode"])
    for (lnd, ebene, kennung), n in summe.items():
        if lnd == "CH" and ebene == "gemeinde":
            land.einwohner[kennung] = n
            land.namen[kennung] = namen[(lnd, ebene, kennung)]
    zerlegung: dict[tuple[str, str], list[tuple[str, float]]] = {}

    def zerlege(bfs: str, ort: str) -> list[tuple[str, float]]:
        """Municipalities of the demography state for a current BFS number: the former
        commune named like the locality, else the surviving one, else all by population."""
        if (bfs, ort) not in zerlegung:
            alt = sorted(vorgaenger.get(bfs, [bfs]))
            gleich = [a for a in alt if _normiert(land.namen.get(a, "")) == _normiert(ort)]
            if len(alt) > 1 and (gleich or bfs in alt):
                alt = gleich or [bfs]
            ew = {a: land.einwohner.get(a, 0) for a in alt}
            gesamt = sum(ew.values())
            zerlegung[(bfs, ort)] = [(a, ew[a] / gesamt if gesamt else 1 / len(alt))
                                     for a in alt]
        return zerlegung[(bfs, ort)]

    for r in _csv(z, eintrag.filename):
        if r["ADR_STATUS"] != "real":
            continue
        plz, ort = r["ZIP_LABEL"][:4], r["ZIP_LABEL"][4:].strip()
        if not re.fullmatch(r"\d{4}", plz):
            continue
        wohnen = r["BDG_CATEGORY"] in CH_WOHNEN
        for gemeinde, gewicht in zerlege(r["COM_FOSNR"], ort):
            paar = (plz, gemeinde)
            land.alle[paar] += gewicht
            if wohnen:
                land.wohnen[paar] += gewicht
            land.orte[paar][ort] += gewicht
    # Cross-check with the official locality directory; postcodes without any address
    # get the directory's address shares as fallback.
    ovz = zipfile.ZipFile(io.BytesIO(_hole(CH_ORTSCHAFTEN, roh_ortschaften,
                                           "ortschaftenverzeichnis_plz_2056.csv.zip")))
    mit_adressen = {p for p, _g in land.alle}
    im_verzeichnis: set[str] = set()
    for r in _csv(ovz, next(n for n in ovz.namelist() if n.endswith(".csv"))):
        if not r["Kantonskürzel"]:  # Liechtenstein
            continue
        plz = r["PLZ4"]
        im_verzeichnis.add(plz)
        if plz in mit_adressen:
            continue
        anteil = float(r["Adressenanteil"].replace("%", "").strip() or 0) / 100
        for gemeinde, gewicht in zerlege(r["BFS-Nr"], r["Ortschaftsname"]):
            land.ersatz[(plz, gemeinde)] += anteil * gewicht
            land.orte[(plz, gemeinde)][r["Ortschaftsname"]] += 1
    land.hinweise += [
        f"Ortschaftenverzeichnis: {len(im_verzeichnis)} PLZ, davon ohne Adresse"
        f" {len(im_verzeichnis - mit_adressen)}; PLZ nur im Adressverzeichnis:"
        f" {sorted(mit_adressen - im_verzeichnis) or 'keine'}",
        "zusammengelegte Gemeinden seit dem Stichtag der Demografie: "
        + (", ".join(f"{t} <- {'+'.join(sorted(v))}" for t, v in sorted(vorgaenger.items()))
           or "keine"),
    ]
    return land


# --------------------------------------------------------------------------- shares

def _runden(anteile: dict[str, float]) -> dict[str, int]:
    """Shares in units of 1/STELLEN summing to STELLEN; shares that round to 0 are dropped."""
    gross = {g: a for g, a in anteile.items() if a * STELLEN >= 0.5}
    gesamt = sum(gross.values())
    n = {g: round(a / gesamt * STELLEN) for g, a in gross.items()}
    groesster = max(n, key=lambda g: (n[g], g))
    n[groesster] += STELLEN - sum(n.values())
    return {g: v for g, v in n.items() if v > 0}


def zeilen(land: Land) -> tuple[list[list[str]], Counter[str], int, int]:
    """Rows of one country, weighting stage used per postcode, pairs without residents and
    pairs dropped by rounding."""
    einheiten_w: dict[str, float] = defaultdict(float)
    einheiten_a: dict[str, float] = defaultdict(float)
    for (_p, g), v in land.wohnen.items():
        einheiten_w[g] += v
    for (_p, g), v in land.alle.items():
        einheiten_a[g] += v
    je_plz: dict[str, dict[str, tuple[float, float, float, float]]] = defaultdict(dict)
    for paar in set(land.alle) | set(land.wohnen) | set(land.ersatz):
        p, g = paar
        ew = land.einwohner.get(g, 0)
        w, a = land.wohnen.get(paar, 0.0), land.alle.get(paar, 0.0)
        je_plz[p][g] = (ew * w / einheiten_w[g] if einheiten_w[g] else 0.0,
                        ew * a / einheiten_a[g] if einheiten_a[g] else 0.0,
                        a, land.ersatz.get(paar, 0.0))
    stufen = ["Einwohner x Wohneinheiten", "Einwohner x alle Adressen", "alle Adressen",
              "Ersatzgewicht"]
    genutzt: Counter[str] = Counter()
    aus: list[list[str]] = []
    verworfen = ohne_einwohner = 0
    for p in sorted(je_plz):
        werte = je_plz[p]
        stufe = next((i for i in range(4) if sum(v[i] for v in werte.values()) > 0), None)
        if stufe is None:
            continue
        genutzt[stufen[stufe]] += 1
        gesamt = sum(v[stufe] for v in werte.values())
        ohne_einwohner += sum(1 for v in werte.values() if v[stufe] == 0)
        gerundet = _runden({g: v[stufe] / gesamt for g, v in werte.items() if v[stufe] > 0})
        verworfen += sum(1 for v in werte.values() if v[stufe] > 0) - len(gerundet)
        for g, n in sorted(gerundet.items(), key=lambda kv: (-kv[1], kv[0])):
            aus.append([land.code, p, _haeufigster(land.orte[(p, g)]), g,
                        land.namen.get(g, ""), land.kreis.get(g, ""), f"{n / STELLEN:.4f}"])
    return aus, genutzt, ohne_einwohner, verworfen


# --------------------------------------------------------------------------- checks

def pruefe(alle_zeilen: list[list[str]], demo: tuple, laender: dict[str, Land]) -> list[str]:
    """Plausibility checks; prints counts and returns the problems found."""
    summe, _namen, _stichtag = demo
    probleme: list[str] = []
    for code, land in laender.items():
        eigene = [z for z in alle_zeilen if z[0] == code]
        plz = {z[1] for z in eigene}
        lo, hi = ERWARTET[code]
        if not lo <= len(plz) <= hi:
            probleme.append(f"{code}: {len(plz)} PLZ außerhalb {lo}-{hi}")
        soll_kreise = {k for (lnd, e, k) in summe if lnd == code and e == "kreis"}
        kreise = {z[5] for z in eigene}
        gemeinden = {z[3] for z in eigene}
        abdeckung = len(kreise & soll_kreise) / len(soll_kreise)
        print(f"{code}: {len(plz)} PLZ, {len(eigene)} Zeilen, {len(gemeinden)} Gemeinden,"
              f" Kreise mit PLZ {len(kreise & soll_kreise)} von {len(soll_kreise)}"
              f" ({abdeckung:.1%}); ohne PLZ: {sorted(soll_kreise - kreise) or 'keine'}")
        if abdeckung < MINDESTABDECKUNG:
            probleme.append(f"{code}: nur {abdeckung:.1%} der Kreise mit PLZ")
        fremd_kreise = kreise - soll_kreise
        if fremd_kreise:
            probleme.append(f"{code}: Kreise nicht in der Demografie: {sorted(fremd_kreise)}")
        bekannt = set(land.namen) if code == "DE" else {
            k for (lnd, e, k) in summe if lnd == code and e == "gemeinde"}
        if gemeinden - bekannt:
            probleme.append(f"{code}: unbekannte Gemeinden: {sorted(gemeinden - bekannt)[:10]}")
        mehrere = Counter(z[1] for z in eigene)
        print(f"  PLZ mit mehreren Gemeinden: {sum(1 for n in mehrere.values() if n > 1)};"
              f" Gemeinden mit mehreren PLZ:"
              f" {sum(1 for n in Counter(z[3] for z in eigene).values() if n > 1)}")
        for hinweis in land.hinweise:
            print(f"  {hinweis}")
    summen: dict[tuple[str, str], int] = defaultdict(int)
    for z in alle_zeilen:
        summen[(z[0], z[1])] += round(float(z[6]) * STELLEN)
    falsch = [k for k, v in summen.items() if v != STELLEN]
    if falsch:
        probleme.append(f"Anteile summieren sich nicht zu 1: {falsch[:10]}")
    for (code, plz), kreis in STICHPROBEN.items():
        treffer = [z for z in alle_zeilen if z[0] == code and z[1] == plz]
        print(f"  Stichprobe {code} {plz}: "
              + "; ".join(f"{z[2]} -> {z[3]} {z[4]}, Kreis {z[5]}, {z[6]}" for z in treffer))
        if not treffer or treffer[0][5] != kreis:
            probleme.append(f"Stichprobe {code} {plz}: Kreis {kreis} erwartet")
    return probleme


# --------------------------------------------------------------------------- output

def kopfzeile(laender: dict[str, Land]) -> str:
    at = laender["AT"].stand.split("-")
    return (
        "# Quelle: DE: © OpenStreetMap-Mitwirkende, Straßenverzeichnis der OpenPLZ API"
        " (https://github.com/openpotato/openplzapi.data), Gemeindeschlüssel und Einwohner:"
        " Statistisches Bundesamt (Destatis), Gemeindeverzeichnis GV-ISys und Gebietsänderungen"
        " | AT: BEV - Bundesamt für Eich- und Vermessungswesen, © Österreichisches"
        f" Adressregister, Stichtagsdaten vom {at[2]}.{at[1]}.{at[0]}; Einwohner: Statistik"
        " Austria - data.statistik.gv.at | CH: Bundesamt für Landestopografie swisstopo,"
        " Amtliches Gebäudeadressverzeichnis und Amtliches Ortschaftenverzeichnis; Bundesamt"
        " für Statistik (BFS), Amtliches Gemeindeverzeichnis der Schweiz"
        " (https://www.agvchapp.bfs.admin.ch) und STATPOP"
        " | zusammengeführt, umgeformt und Anteile geschätzt von AnonyMed;"
        " Lizenz: Datei: Open Database License (ODbL) 1.0"
        " (https://opendatacommons.org/licenses/odbl/1-0/), weil die DE-Zeilen aus"
        " OpenStreetMap abgeleitet sind; Einzelquellen: DE: OSM/OpenPLZ ODbL 1.0, Destatis"
        " Datenlizenz Deutschland - Namensnennung - 2.0 (https://www.govdata.de/dl-de/by-2-0)"
        " | AT: CC BY 4.0"
        " (https://creativecommons.org/licenses/by/4.0/) | CH: swisstopo OGD, Quellenangabe"
        " Pflicht; BFS OPEN BY;"
        f" Stand: DE {laender['DE'].stand}, AT {laender['AT'].stand},"
        f" CH {laender['CH'].stand} (Gemeinden im Gebietsstand der Demografie-Datei)\n"
    )


def schreibe(alle_zeilen: list[list[str]], laender: dict[str, Land], ziel: Path) -> None:
    puffer = io.StringIO()
    puffer.write(kopfzeile(laender))
    w = csv.writer(puffer, lineterminator="\n")
    w.writerow(KOPF)
    w.writerows(alle_zeilen)
    ziel.parent.mkdir(parents=True, exist_ok=True)
    with (open(ziel, "wb") as roh,
          gzip.GzipFile(filename="", fileobj=roh, mode="wb", mtime=0) as gz):
        gz.write(puffer.getvalue().encode("utf-8"))
    print(f"{len(alle_zeilen)} Zeilen nach {ziel} geschrieben"
          f" ({ziel.stat().st_size / 1e6:.2f} MB)")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--roh", type=Path, help="folder that keeps the downloads")
    parser.add_argument("--ziel", type=Path, default=ZIEL, help="output file")
    args = parser.parse_args(argv[1:])

    def ordner(name: str) -> Path | None:
        return args.roh / name if args.roh else None

    demo = _demografie()
    print("DE: lade OpenPLZ-Straßen und Destatis-Gebietsänderungen", file=sys.stderr)
    laender = {"DE": deutschland(ordner("openplz"), ordner("destatis_aenderungen"))}
    print("AT: lade BEV-Adressregister", file=sys.stderr)
    laender["AT"] = oesterreich(ordner("bev_adressregister"), demo)
    print("CH: lade swisstopo-Verzeichnisse und BFS-Gemeindestand", file=sys.stderr)
    laender["CH"] = schweiz(ordner("swisstopo_adressen"), ordner("swisstopo"),
                            ordner("bfs_agv"), demo)
    alle_zeilen: list[list[str]] = []
    for land in laender.values():
        z, genutzt, ohne_einwohner, verworfen = zeilen(land)
        land.hinweise.append(f"Gewichtung je PLZ: {dict(genutzt)}; Paare ohne geschätzte"
                             f" Einwohner: {ohne_einwohner}; Anteil unter 0,00005 verworfen:"
                             f" {verworfen}")
        alle_zeilen += z
    probleme = pruefe(alle_zeilen, demo, laender)
    if probleme:
        print(f"{len(probleme)} Probleme, nichts geschrieben:", *probleme[:30], sep="\n  ")
        return 1
    schreibe(alle_zeilen, laender, args.ziel)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
