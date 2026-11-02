"""Build ``anonymed/daten/demografie_dach.csv.gz``: population by sex and single year of age.

Germany, Austria and Switzerland, at national, state (Land, Bundesland, Kanton) and district
level (Kreis, politischer Bezirk, Bezirk); Austria and Switzerland also by municipality.  The
residual-risk check uses the counts as cohort sizes (k-map: lambda = population x share).

Sources and licences
--------------------
Germany (DE): Statistische Ämter des Bundes und der Länder, Zensus 2022, Zensusdatenbank
table ``1000A-2027`` "Personen: Geschlecht - Alter (Jahresschritte)", reference date
15 May 2022, territory of 15 May 2022.  Licence: Datenlizenz Deutschland - Namensnennung -
Version 2.0 (https://www.govdata.de/dl-de/by-2-0).  Read through the JSON interface that the
database's own web front end uses without an account
(``https://ergebnisse.zensus2022.de/proxy/api/rest/tables/<code>/...``); the documented
GENESIS web service needs a registered account.  Cells of sex x age are perturbed by the
census confidentiality procedure, so district sums deviate slightly from state totals; the
national total also contains Germans living abroad on official duty.  Persons recorded as
"divers" or "unbekannt" were redistributed to male and female by the census offices.
Hanau became a kreisfreie Stadt (independent city) on 1 January 2026 (Kreis 06415): its
row is taken from the municipality figures and subtracted from the Main-Kinzig-Kreis
(06435), so the districts match the current directory ``anonymed/daten/gemeinden_de.csv.gz``.

Austria (AT): Statistik Austria, open data set ``OGD_bevstandjbab2002_BevStand_2026``
"Bevölkerungsstand zu Jahresbeginn 2026" (municipalities, Vienna by district, single years
0-99 and 100+, sex; 1 January 2026) and the list of political districts ``polbezirke.csv``.
Licence: CC BY 4.0, attribution "Datenquelle: Statistik Austria - data.statistik.gv.at".
Entries "divers", "inter", "offen" and "kein Eintrag" are imputed to male or female by
Statistik Austria.  Districts and states are summed from the municipalities; Vienna's 23
Gemeindebezirke (901-923) stand at district level.

Switzerland (CH): Bundesamt für Statistik (BFS), STATPOP, STAT-TAB table
``px-x-0102010000_101`` "Ständige und nichtständige Wohnbevölkerung nach institutionellen
Gliederungen, Staatsangehörigkeit (Kategorie), Geschlecht und Alter", permanent resident
population on 31 December of the latest year, queried through the PxWeb API in batches (the
server refuses requests of more than about 100,000 cells).  Terms of use OPEN BY: free use,
attribution compulsory (https://www.bfs.admin.ch/asset/de/px-x-0102010000_101).

Output
------
A comment line ``# Quelle: ...; Lizenz: ...; Stand: ...`` and the columns
``land,ebene,kennung,name,geschlecht,alter_von,alter_bis,anzahl,stichtag``.  ``kennung`` is
the official key (DE: AGS prefix, AT: Gemeinde-/Bezirkskennziffer, CH: BFS number; the
country code for ``ebene`` staat).  Ages 0-99 are single years, 100 is the open class
(``alter_bis`` empty).  Rows with a count of zero are omitted.

Usage: python -I werkzeuge/demografie_dach.py [--roh ORDNER] [--ziel DATEI]

With ``--roh`` the downloads are kept in (and re-read from) ``ORDNER/<source>/``.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import re
import sys
import time
import urllib.request
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path

ZIEL = Path(__file__).resolve().parent.parent / "anonymed" / "daten" / "demografie_dach.csv.gz"
GEMEINDEN_DE = ZIEL.parent / "gemeinden_de.csv.gz"
KOPF = ["land", "ebene", "kennung", "name", "geschlecht", "alter_von", "alter_bis", "anzahl",
        "stichtag"]
AGENT = {"User-Agent": "AnonyMed build script (https://github.com/fre-ms/AnonyMed)"}
OFFEN = 100  # first age of the open-ended class in all three sources

ZENSUS = "https://ergebnisse.zensus2022.de/proxy/api/rest/tables/1000A-2027"
ZENSUS_STICHTAG = "2022-05-15"
# New districts since the census: new key -> (old district, municipalities moved).
NEUE_KREISE_DE = {"06415": ("06435", ["064350014014"])}  # Hanau, kreisfrei since 1.1.2026

AT_JAHR = 2026
AT_DATEN = "https://data.statistik.gv.at/data/OGD_bevstandjbab2002_BevStand_{jahr}{teil}.csv"
AT_BEZIRKE = "https://www.statistik.at/verzeichnis/reglisten/polbezirke.csv"

CH_TABELLE = ("https://www.pxweb.bfs.admin.ch/api/v1/de/px-x-0102010000_101/"
              "px-x-0102010000_101.px")
CH_REGIONEN_JE_ABFRAGE = 440  # x 2 sexes x 101 ages stays below the 100,000-cell limit
CH_KANTONE = ["ZH", "BE", "LU", "UR", "SZ", "OW", "NW", "GL", "ZG", "FR", "SO", "BS", "BL",
              "SH", "AR", "AI", "SG", "GR", "AG", "TG", "TI", "VD", "VS", "NE", "GE", "JU"]

# Expected national totals (rough bounds for the plausibility check).
ERWARTET = {"DE": (80_000_000, 86_000_000), "AT": (8_800_000, 9_600_000),
            "CH": (8_700_000, 9_500_000)}
TOLERANZ = {"DE": 0.002, "AT": 0.0, "CH": 0.0}  # relative deviation child sums vs. parent

Zeile = tuple[str, str, str, str, str, int, int | None, int, str]


# --------------------------------------------------------------------------- downloads

def _hole(url: str, roh: Path | None, datei: str, daten: bytes | None = None,
          versuche: int = 3, timeout: int = 600) -> bytes:
    """Return the body of ``url`` (POST if ``daten``), cached as ``roh/datei`` if given."""
    pfad = roh / datei if roh else None
    if pfad and pfad.exists():
        return pfad.read_bytes()
    kopf = dict(AGENT)
    if daten is not None:
        kopf["Content-Type"] = "application/json"
    for versuch in range(1, versuche + 1):
        try:
            anfrage = urllib.request.Request(url, data=daten, headers=kopf)
            with urllib.request.urlopen(anfrage, timeout=timeout) as antwort:
                inhalt = antwort.read()
            break
        except OSError as fehler:
            if versuch == versuche:
                raise
            print(f"  {url}: {fehler}; neuer Versuch in {20 * versuch} s", file=sys.stderr)
            time.sleep(20 * versuch)
    if pfad:
        pfad.parent.mkdir(parents=True, exist_ok=True)
        pfad.write_bytes(inhalt)
    return inhalt


def _jsonstat(ds: dict) -> Iterator[tuple[dict[str, str], float | None, str]]:
    """Yield ``({dimension: category}, value, status)`` for every cell of a JSON-stat set."""
    dims, groesse = ds["id"], ds["size"]
    umkehr = [{i: k for k, i in ds["dimension"][d]["category"]["index"].items()} for d in dims]
    schritte = [1] * len(groesse)
    for j in range(len(groesse) - 2, -1, -1):
        schritte[j] = schritte[j + 1] * groesse[j + 1]
    status = ds.get("status")
    for pos, wert in enumerate(ds["value"]):
        zelle = {d: umkehr[j][(pos // schritte[j]) % groesse[j]] for j, d in enumerate(dims)}
        st = status[pos] if isinstance(status, list) else (status or {}).get(str(pos), "")
        yield zelle, wert, st or ""


# --------------------------------------------------------------------------- Germany

def _alter_zensus(code: str) -> tuple[int, int | None] | None:
    if code == "ALTERU01":
        return 0, 0
    m = re.fullmatch(r"ALTER(\d{3})(UM)?", code)
    if not m:
        return None
    a = int(m.group(1))
    return (a, None) if m.group(2) else (a, a)


def deutschland(roh: Path | None) -> tuple[list[Zeile], dict[tuple[str, str], str]]:
    """Rows for Germany (state, Länder, Kreise) and the parent of each Land and Kreis."""
    struktur = json.loads(_hole(f"{ZENSUS}/structure?language=de", roh, "structure.json"))
    namen = {k: v["label"]["de"] for k, v in struktur["variableValues"].items()}
    namen["DG"] = "Deutschland"
    werte: dict[tuple[str, str, str, int, int | None], int] = {}
    benoetigt = {g for _alt, gemeinden in NEUE_KREISE_DE.values() for g in gemeinden}
    abfragen = [("v3", "GEOBL1"), ("v6", "GEOLK4")] + ([("v8", "GEOGM4")] if benoetigt else [])
    for block, region in abfragen:
        zustand = json.loads(json.dumps(struktur["initialState"]))
        if zustand["variableBlocks"][block]["mainVariable"] != region:
            raise SystemExit(f"Zensus: Block {block} ist nicht {region}, Tabelle geändert?")
        zustand["tableStructure"]["colTitle"][1]["blockCode"] = block
        roh_json = _hole(f"{ZENSUS}/data?language=de", roh, f"data_{region}.json",
                         json.dumps(zustand).encode())
        for ds in json.loads(roh_json)["data"]:
            ebene = next((r for r in ("GEODL1", region) if r in ds["id"]), None)
            if ebene is None:
                continue
            for zelle, wert, status in _jsonstat(ds):
                if ebene == "GEOGM4" and zelle[ebene] not in benoetigt:
                    continue  # municipalities only for the territorial changes below
                inhalt = zelle["content"].split("$")
                alter = _alter_zensus(zelle["ALTER1"])
                g = {"GESM": "m", "GESW": "w"}.get(zelle["GESCH1"])
                if len(inhalt) < 2 or not inhalt[1].startswith("ID") or not alter or not g:
                    continue
                anzahl = 0 if status == "-" or wert is None else round(wert)
                werte[(ebene, zelle[ebene], g, *alter)] = anzahl
    # Territorial changes since the census (Hanau).
    for neu, (alt, gemeinden) in NEUE_KREISE_DE.items():
        if ("GEOLK4", neu, "m", 0, 0) in werte:
            continue
        for (ebene, k, g, a, b), n in list(werte.items()):
            if ebene == "GEOGM4" and k in gemeinden:
                schluessel = ("GEOLK4", neu, g, a, b)
                werte[schluessel] = werte.get(schluessel, 0) + n
                altschluessel = ("GEOLK4", alt, g, a, b)
                werte[altschluessel] = max(0, werte.get(altschluessel, 0) - n)
        namen[neu] = namen[gemeinden[0]] if len(gemeinden) == 1 else neu
    zeilen: list[Zeile] = []
    eltern: dict[tuple[str, str], str] = {}
    ebenen = {"GEODL1": "staat", "GEOBL1": "land", "GEOLK4": "kreis"}
    for (ebene, k, g, a, b), n in werte.items():
        if ebene not in ebenen:
            continue
        kennung = "DE" if ebene == "GEODL1" else k
        zeilen.append(("DE", ebenen[ebene], kennung, namen.get(k, k), g, a, b, n,
                       ZENSUS_STICHTAG))
        if ebene == "GEOBL1":
            eltern[("land", k)] = "DE"
        elif ebene == "GEOLK4":
            eltern[("kreis", k)] = k[:2]
    return zeilen, eltern


# --------------------------------------------------------------------------- Austria

def _csv_at(inhalt: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(inhalt.decode("utf-8-sig")), delimiter=";"))


def oesterreich(roh: Path | None) -> tuple[list[Zeile], dict[tuple[str, str], str]]:
    """Rows for Austria: municipalities summed up to districts, Bundesländer and state."""
    def teil(t: str) -> bytes:
        name = f"BevStand_{AT_JAHR}{t}.csv"
        return _hole(AT_DATEN.format(jahr=AT_JAHR, teil=t), roh, name)

    gemeindenamen = {r[0].removeprefix("GRGEMAKT-"): re.sub(r"\s*<\d+>$", "", r[1])
                     for r in _csv_at(teil("_C-GRGEMAKT-0"))[1:] if r}
    alter: dict[str, tuple[int, int | None]] = {}
    for r in _csv_at(teil("_C-GALTEJ112-0"))[1:]:
        m = re.match(r"(\d+) Jahr", r[1]) if r else None
        if m:
            a = int(m.group(1))
            alter[r[0]] = (a, None) if "älter" in r[1] or "mehr" in r[1] else (a, a)
    geschlecht = {"C11-1": "m", "C11-2": "w"}
    bezirke: dict[str, str] = {}
    laender: dict[str, str] = {}
    for r in _csv_at(_hole(AT_BEZIRKE, roh, "polbezirke.csv")):
        if len(r) >= 5 and r[0].isdigit() and r[4].isdigit():
            laender[r[0]] = r[1]
            if r[4] != "900":  # Vienna as a whole is the Bundesland
                bezirke[r[4]] = re.sub(r"\s+", " ", r[3]).strip()
    summen: dict[tuple[str, str, str, int, int | None], int] = defaultdict(int)
    stichtag = f"{AT_JAHR}-01-01"
    kopf, *daten = _csv_at(teil(""))
    spalte = {name: i for i, name in enumerate(kopf)}
    for r in daten:
        if not r:
            continue
        gem = r[spalte["C-GRGEMAKT-0"]].removeprefix("GRGEMAKT-")
        g = geschlecht[r[spalte["C-C11-0"]]]
        a, b = alter[r[spalte["C-GALTEJ112-0"]]]
        n = int(r[spalte["F-ISIS-1"]])
        for ebene, k in (("gemeinde", gem), ("kreis", gem[:3]), ("land", gem[0]),
                         ("staat", "AT")):
            summen[(ebene, k, g, a, b)] += n
    unbekannt = {k for (e, k, *_r) in summen if e == "kreis" and k not in bezirke}
    if unbekannt:
        raise SystemExit(f"AT: Bezirke ohne Namen in polbezirke.csv: {sorted(unbekannt)}")
    namen = {"gemeinde": gemeindenamen, "kreis": bezirke, "land": laender,
             "staat": {"AT": "Österreich"}}
    zeilen = [("AT", e, k, namen[e].get(k, k), g, a, b, n, stichtag)
              for (e, k, g, a, b), n in summen.items()]
    eltern = {}
    for e, k, *_r in summen:
        if e == "gemeinde":
            eltern[(e, k)] = k[:3]
        elif e == "kreis":
            eltern[(e, k)] = k[0]
        elif e == "land":
            eltern[(e, k)] = "AT"
    return zeilen, eltern


# --------------------------------------------------------------------------- Switzerland

def schweiz(roh: Path | None) -> tuple[list[Zeile], dict[tuple[str, str], str]]:
    """Rows for Switzerland: state, cantons, districts and municipalities from STATPOP."""
    meta = json.loads(_hole(CH_TABELLE, roh, "metadaten.json"))
    var = {v["code"]: v for v in meta["variables"]}
    region = next(c for c in var if c.startswith("Kanton"))
    jahr = max(var["Jahr"]["values"])
    stichtag = f"{jahr}-12-31"
    # Classify the regions by their label prefix and remember the hierarchy.
    einheiten: dict[str, tuple[str, str, str]] = {}  # code -> (ebene, kennung, name)
    eltern: dict[tuple[str, str], str] = {}
    kanton = bezirk = ""
    for code, text in zip(var[region]["values"], var[region]["valueTexts"], strict=True):
        if text.startswith(">>"):
            bezirk = str(int(code))
            einheiten[code] = ("kreis", bezirk, text[2:].strip())
            eltern[("kreis", bezirk)] = kanton
        elif text.startswith("......"):
            nummer, _, name = text.lstrip(".").partition(" ")
            kennung = str(int(nummer))
            einheiten[code] = ("gemeinde", kennung, name.strip())
            eltern[("gemeinde", kennung)] = bezirk
        elif code == "8100":
            einheiten[code] = ("staat", "CH", text.strip())
        elif code in CH_KANTONE:
            kanton = str(CH_KANTONE.index(code) + 1)
            einheiten[code] = ("land", kanton, text.lstrip("- ").strip())
            eltern[("land", kanton)] = "CH"
        else:
            einheiten[code] = ("", code, text)  # "Ohne Angabe": checked, not written
    alter_codes = [a for a in var["Alter"]["values"] if a.lstrip("-").isdigit() and
                   int(a) >= 0]
    offen = {a for a, t in zip(var["Alter"]["values"], var["Alter"]["valueTexts"], strict=True)
             if "mehr" in t or "älter" in t}
    codes = list(einheiten)
    zeilen: list[Zeile] = []
    ohne_angabe = 0
    for nr, start in enumerate(range(0, len(codes), CH_REGIONEN_JE_ABFRAGE), 1):
        teil = codes[start:start + CH_REGIONEN_JE_ABFRAGE]
        abfrage = {"query": [
            {"code": "Jahr", "selection": {"filter": "item", "values": [jahr]}},
            {"code": region, "selection": {"filter": "item", "values": teil}},
            {"code": "Bevölkerungstyp", "selection": {"filter": "item", "values": ["1"]}},
            {"code": "Staatsangehörigkeit (Kategorie)",
             "selection": {"filter": "item", "values": ["-99999"]}},
            {"code": "Geschlecht", "selection": {"filter": "item", "values": ["1", "2"]}},
            {"code": "Alter", "selection": {"filter": "item", "values": alter_codes}},
        ], "response": {"format": "json-stat2"}}
        print(f"  BFS-Abfrage {nr}: {len(teil)} Regionen", file=sys.stderr)
        ds = json.loads(_hole(CH_TABELLE, roh, f"daten_{jahr}_{nr:02d}.json",
                              json.dumps(abfrage).encode()))
        for zelle, wert, _status in _jsonstat(ds):
            ebene, kennung, name = einheiten[zelle[region]]
            n = int(wert or 0)
            if not ebene:
                ohne_angabe += n
                continue
            a = int(zelle["Alter"])
            b = None if zelle["Alter"] in offen else a
            g = {"1": "m", "2": "w"}[zelle["Geschlecht"]]
            zeilen.append(("CH", ebene, kennung, name, g, a, b, n, stichtag))
    if ohne_angabe:
        print(f"  CH: {ohne_angabe} Personen ohne Regionsangabe nicht übernommen",
              file=sys.stderr)
    return zeilen, eltern


# --------------------------------------------------------------------------- checks

def _zahl(n: int) -> str:
    """German thousands separator."""
    return f"{n:,}".replace(",", ".")


def pruefe(zeilen: list[Zeile], eltern: dict[tuple[str, str, str], str]) -> list[str]:
    """Plausibility checks; returns the problems found (empty list = all fine)."""
    probleme: list[str] = []
    summe: dict[tuple[str, str, str, str], int] = defaultdict(int)
    for land, ebene, kennung, _n, g, _a, _b, n, _s in zeilen:
        summe[(land, ebene, kennung, g)] += n
    ober = {"land": "staat", "kreis": "land", "gemeinde": "kreis"}
    for land in ("DE", "AT", "CH"):
        gesamt = sum(v for (lnd, e, _k, _g), v in summe.items() if lnd == land and e == "staat")
        lo, hi = ERWARTET[land]
        anzahl = {e: len({k for (lnd, ee, k, _g) in summe if lnd == land and ee == e})
                  for e in ("land", "kreis", "gemeinde")}
        print(f"{land}: {_zahl(gesamt)} Personen; {anzahl['land']} Länder,"
              f" {anzahl['kreis']} Kreise, {anzahl['gemeinde']} Gemeinden")
        if not lo <= gesamt <= hi:
            probleme.append(f"{land}: Summe {gesamt} außerhalb {lo}-{hi}")
        for ebene, elternebene in ober.items():
            kinder: dict[tuple[str, str], int] = defaultdict(int)
            for (lnd, e, k, g), v in summe.items():
                if lnd == land and e == ebene:
                    vater = eltern.get((land, ebene, k))
                    if vater is None:
                        probleme.append(f"{land} {ebene} {k}: keine übergeordnete Einheit")
                        continue
                    kinder[(vater, g)] += v
            if not kinder:
                continue
            for (vater, g), v in sorted(kinder.items()):
                soll = summe.get((land, elternebene, vater, g))
                if soll is None:
                    probleme.append(f"{land} {elternebene} {vater} fehlt")
                    continue
                abw = (v - soll) / soll if soll else 0.0
                if abs(abw) > TOLERANZ[land]:
                    probleme.append(f"{land} {ebene}->{elternebene} {vater} {g}: "
                                    f"{v} statt {soll} ({abw:+.4%})")
            ist = sum(kinder.values())
            soll = sum(v for (lnd, e, _k, _g), v in summe.items()
                       if lnd == land and e == elternebene)
            groesste = max(abs(v - summe[(land, elternebene, vater, g)])
                           / summe[(land, elternebene, vater, g)]
                           for (vater, g), v in kinder.items()
                           if summe.get((land, elternebene, vater, g)))
            print(f"  Summe {ebene} = {_zahl(ist)} gegen {elternebene} = {_zahl(soll)}"
                  f" ({(ist - soll) / soll:+.4%}; größte Abweichung je Einheit und"
                  f" Geschlecht {groesste:.4%})")
    return probleme


def abgleich_gemeinden_de(zeilen: list[Zeile]) -> None:
    """Report districts of the bundled municipality directory missing from the data."""
    if not GEMEINDEN_DE.exists():
        return
    with gzip.open(GEMEINDEN_DE, "rt", encoding="utf-8") as f:
        next(f)
        kreise = {r["ars"][:5] for r in csv.DictReader(f) if int(r["einwohner"] or 0) > 0}
    vorhanden = {z[2] for z in zeilen if z[0] == "DE" and z[1] == "kreis"}
    print(f"  Abgleich mit gemeinden_de.csv.gz: {len(kreise & vorhanden)} von {len(kreise)}"
          f" Kreisen gefunden; fehlend: {sorted(kreise - vorhanden) or 'keine'};"
          f" überzählig: {sorted(vorhanden - kreise) or 'keine'}")


# --------------------------------------------------------------------------- output

def kopfzeile(stichtage: dict[str, str]) -> str:
    return (
        "# Quelle: DE: Statistische Ämter des Bundes und der Länder, Zensus 2022,"
        " Zensusdatenbank Tabelle 1000A-2027 (Gebietsstand 15.05.2022, Hanau als kreisfreie"
        " Stadt nachgeführt) | AT: Statistik Austria - data.statistik.gv.at,"
        f" OGD_bevstandjbab2002_BevStand_{AT_JAHR} und polbezirke.csv | CH: Bundesamt für"
        " Statistik (BFS), STATPOP, Ständige Wohnbevölkerung, STAT-TAB px-x-0102010000_101,"
        " https://www.bfs.admin.ch/asset/de/px-x-0102010000_101 | ausgewählt, umgeformt und"
        " für AT summiert von AnonyMed, Zeilen mit 0 Personen ausgelassen;"
        " Lizenz: DE: Datenlizenz Deutschland - Namensnennung - Version 2.0"
        " (https://www.govdata.de/dl-de/by-2-0) | AT: CC BY 4.0"
        " (https://creativecommons.org/licenses/by/4.0/) | CH: OPEN BY, Quellenangabe Pflicht"
        " (https://statistik.admin.ch/de/nutzungsbedingungen);"
        f" Stand: DE {stichtage['DE']}, AT {stichtage['AT']}, CH {stichtage['CH']}\n"
    )


def schreibe(zeilen: list[Zeile], ziel: Path) -> None:
    folge_land = {"DE": 0, "AT": 1, "CH": 2}
    folge_ebene = {"staat": 0, "land": 1, "kreis": 2, "gemeinde": 3}
    zeilen = sorted((z for z in zeilen if z[7] > 0),
                    key=lambda z: (folge_land[z[0]], folge_ebene[z[1]], z[2].zfill(12), z[4],
                                   z[5]))
    stichtage = {z[0]: z[8] for z in zeilen}
    puffer = io.StringIO()
    puffer.write(kopfzeile(stichtage))
    w = csv.writer(puffer, lineterminator="\n")
    w.writerow(KOPF)
    for z in zeilen:
        w.writerow([*z[:6], "" if z[6] is None else z[6], *z[7:]])
    ziel.parent.mkdir(parents=True, exist_ok=True)
    with open(ziel, "wb") as roh, gzip.GzipFile(fileobj=roh, mode="wb", mtime=0) as gz:
        gz.write(puffer.getvalue().encode("utf-8"))
    print(f"{len(zeilen)} Zeilen nach {ziel} geschrieben ({ziel.stat().st_size / 1e6:.2f} MB)")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--roh", type=Path, help="folder that keeps the downloads")
    parser.add_argument("--ziel", type=Path, default=ZIEL, help="output file")
    args = parser.parse_args(argv[1:])

    def ordner(name: str) -> Path | None:
        return args.roh / name if args.roh else None

    zeilen: list[Zeile] = []
    eltern: dict[tuple[str, str, str], str] = {}
    for land, funktion, quelle in (("DE", deutschland, "zensus2022_1000A-2027"),
                                   ("AT", oesterreich, "statistik_austria_ogd"),
                                   ("CH", schweiz, "bfs_pxweb")):
        print(f"{land}: lade {quelle}", file=sys.stderr)
        z, e = funktion(ordner(quelle))
        zeilen += z
        eltern.update({(land, ebene, k): v for (ebene, k), v in e.items()})
    probleme = pruefe(zeilen, eltern)
    abgleich_gemeinden_de(zeilen)
    if probleme:
        print(f"{len(probleme)} Probleme, nichts geschrieben:", *probleme[:30], sep="\n  ")
        return 1
    schreibe(zeilen, args.ziel)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
