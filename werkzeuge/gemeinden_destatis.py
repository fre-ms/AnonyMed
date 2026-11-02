"""Build ``anonymed/daten/gemeinden_de.csv.gz`` from the Destatis municipality directory.

Source: Statistisches Bundesamt (Destatis), Gemeindeverzeichnis-Informationssystem GV-ISys,
quarterly extract "Alle politisch selbständigen Gemeinden mit ausgewählten Merkmalen"
(``AuszugGV3QAktuell.xlsx``), licence Datenlizenz Deutschland – Namensnennung – Version 2.0
(https://www.govdata.de/dl-de/by-2-0).  The workbook is read with the standard library
only (a .xlsx is a zip of XML), rows of record type 60 (municipalities) are kept.

Usage: python werkzeuge/gemeinden_destatis.py [AuszugGV3QAktuell.xlsx]
"""

from __future__ import annotations

import csv
import gzip
import io
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

URL = ("https://www.destatis.de/DE/Themen/Laender-Regionen/Regionales/Gemeindeverzeichnis/"
       "Administrativ/Archiv/GVAuszugQ/AuszugGV3QAktuell.xlsx?__blob=publicationFile")
ZIEL = Path(__file__).resolve().parent.parent / "anonymed" / "daten" / "gemeinden_de.csv.gz"
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_ZUSATZ = re.compile(r",\s*(?:Stadt|St\.?|M\.?|Markt|GKSt|Hansestadt|Landeshauptstadt|"
                     r"Kreisfreie Stadt|Universitätsstadt|Große Kreisstadt|Wissenschaftsstadt|"
                     r"Lutherstadt|Hansestadt|Kurort|Bergstadt|Flecken|Bad)\b.*$")


def _zellen(zeile: ET.Element, strings: list[str]) -> dict[str, str]:
    aus: dict[str, str] = {}
    for c in zeile.findall("m:c", NS):
        ref = c.get("r", "")
        spalte = "".join(ch for ch in ref if ch.isalpha())
        v = c.findtext("m:v", default="", namespaces=NS)
        if c.get("t") == "s" and v:
            v = strings[int(v)]
        elif c.get("t") == "inlineStr":
            v = "".join(t.text or "" for t in c.iter(f"{{{NS['m']}}}t"))
        aus[spalte] = v.strip()
    return aus


def lies(xlsx: bytes) -> tuple[str, list[dict[str, str]]]:
    z = zipfile.ZipFile(io.BytesIO(xlsx))
    ss = ET.fromstring(z.read("xl/sharedStrings.xml"))
    strings = ["".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t"))
               for si in ss.findall("m:si", NS)]
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    blatt = next(s.get("name") for s in wb.find("m:sheets", NS) if "Gemeinden" in s.get("name"))
    stand = re.sub(r"\D", "", blatt)[-8:]
    stand = f"{stand[4:]}-{stand[2:4]}-{stand[:2]}" if len(stand) == 8 else blatt
    daten = ET.fromstring(z.read("xl/worksheets/sheet2.xml")).find("m:sheetData", NS)
    gemeinden = []
    for zeile in daten:
        c = _zellen(zeile, strings)
        if c.get("A") != "60":
            continue
        name = _ZUSATZ.sub("", c.get("H", "")).strip()
        einwohner = c.get("J", "").replace(".", "")
        if not name or not einwohner.isdigit():
            continue
        gemeinden.append({
            "name": name, "einwohner": int(einwohner), "plz": c.get("N", ""),
            "land": c.get("C", ""),
            "ars": "".join(c.get(sp, "") for sp in "CDEFG"),
        })
    return stand, gemeinden


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        roh = Path(argv[1]).read_bytes()
    else:
        with urllib.request.urlopen(urllib.request.Request(URL, headers={"User-Agent": "AnonyMed"}),
                                    timeout=120) as antwort:
            roh = antwort.read()
    stand, gemeinden = lies(roh)
    ZIEL.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(ZIEL, "wt", encoding="utf-8", newline="") as f:
        f.write(f"# Quelle: Statistisches Bundesamt (Destatis), Gemeindeverzeichnis GV-ISys,"
                f" Gebietsstand {stand}; Lizenz: Datenlizenz Deutschland – Namensnennung – 2.0\n")
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["name", "einwohner", "plz", "land", "ars"])
        for g in sorted(gemeinden, key=lambda g: (g["name"], -g["einwohner"])):
            w.writerow([g["name"], g["einwohner"], g["plz"], g["land"], g["ars"]])
    print(f"{len(gemeinden)} Gemeinden (Stand {stand}) nach {ZIEL} geschrieben"
          f" ({ZIEL.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
