"""Build ``pseudonymed/daten/fachklassen_de.csv.gz``: a semantic class for every Wikidata term.

The bundled vocabulary (``fachbegriffe_de.txt.gz``) only says *that* a word is medical.  This
script records *what* it is, from the same Wikidata items (CC0 1.0): ATC code (P267) or WHO
INN (P2275) → Medikament; ICD-10 (P494) → Erkrankung; LOINC (P4338) → Laborwert; MeSH tree
numbers (P672) → A Anatomie, B Organismus, C Erkrankung (C23.888 Symptom), D Substanz,
E Verfahren, F Psyche, G Physiologie.  A term may carry several classes (one row each); the
Wikidata item is kept as the identifier so that a class can be traced.

Usage: uv run python werkzeuge/fachklassen_wikidata.py [ziel.csv.gz]
"""

from __future__ import annotations

import csv
import datetime as dt
import gzip
import io
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ENDPUNKT = "https://query.wikidata.org/sparql"
AGENT = ("PseudonyMed/0.1 (open-source medical de-identification;"
         " https://github.com/fre-ms/PseudonyMed)")
ZIEL = Path(__file__).resolve().parent.parent / "pseudonymed" / "daten" / "fachklassen_de.csv.gz"
MESH_KLASSEN = {"A": "Anatomie", "B": "Organismus", "C": "Erkrankung", "D": "Substanz",
                "E": "Verfahren", "F": "Psyche", "G": "Physiologie", "S": "Symptom"}
MESH_PRAEFIXE = ("D000", "D001", "D002", "D003", "D004", "D005", "D006", "D007", "D008",
                 "D009", "D01", "D02", "D03", "D04", "D05", "D06", "D07")
_LABELS = """{ ?item rdfs:label ?label } UNION { ?item skos:altLabel ?label }
  FILTER(LANG(?label) = "de")"""


def abfrage(sparql: str, versuche: int = 5) -> list[list[str]]:
    url = ENDPUNKT + "?" + urllib.parse.urlencode({"query": sparql})
    for versuch in range(versuche):
        req = urllib.request.Request(url, headers={"Accept": "text/csv", "User-Agent": AGENT})
        try:
            with urllib.request.urlopen(req, timeout=180) as antwort:
                text = antwort.read().decode("utf-8")
            return list(csv.reader(io.StringIO(text)))[1:]
        except Exception as e:  # noqa: BLE001 - retry on timeouts and 429
            wartezeit = 15 * (versuch + 1)
            print(f"  Versuch {versuch + 1} fehlgeschlagen ({e}); warte {wartezeit}s",
                  file=sys.stderr)
            time.sleep(wartezeit)
    raise RuntimeError("Wikidata-Abfrage wiederholt fehlgeschlagen")


def qid(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


def eigenschaft(prop: str, klasse: str) -> list[tuple[str, str, str]]:
    zeilen = abfrage(f"SELECT DISTINCT ?item ?label WHERE {{ ?item wdt:{prop} ?v . {_LABELS} }}")
    return [(z[1], klasse, qid(z[0])) for z in zeilen if len(z) >= 2]


def mesh(praefix: str) -> list[tuple[str, str, str]]:
    sparql = f"""SELECT ?item ?label (GROUP_CONCAT(DISTINCT ?k; separator=" ") AS ?klassen)
WHERE {{
  ?item wdt:P486 ?v . FILTER(STRSTARTS(?v, "{praefix}"))
  ?item wdt:P672 ?baum . FILTER(REGEX(?baum, "^[A-G]"))
  BIND(IF(STRSTARTS(?baum, "C23.888"), "S", SUBSTR(?baum, 1, 1)) AS ?k)
  {_LABELS}
}} GROUP BY ?item ?label"""
    aus = []
    for z in abfrage(sparql):
        if len(z) < 3:
            continue
        for k in sorted(set(z[2].split())):
            aus.append((z[1], MESH_KLASSEN[k], qid(z[0])))
    return aus


def main(ziel: Path) -> None:
    zeilen: list[tuple[str, str, str]] = []
    schritte = [("ATC (P267)", lambda: eigenschaft("P267", "Medikament")),
                ("INN (P2275)", lambda: eigenschaft("P2275", "Medikament")),
                ("ICD-10 (P494)", lambda: eigenschaft("P494", "Erkrankung")),
                ("LOINC (P4338)", lambda: eigenschaft("P4338", "Laborwert"))]
    schritte += [(f"MeSH {p}*", lambda p=p: mesh(p)) for p in MESH_PRAEFIXE]
    for name, schritt in schritte:
        t = time.time()
        neu = schritt()
        zeilen += neu
        print(f"{name}: {len(neu)} Zeilen ({time.time() - t:.0f}s)")
        time.sleep(2)
    eindeutig: dict[tuple[str, str], tuple[str, str, str]] = {}
    for begriff, klasse, kennung in zeilen:
        begriff = " ".join(begriff.split())
        if len(begriff) < 3 or begriff.isdigit():
            continue
        eindeutig.setdefault((begriff.casefold(), klasse), (begriff, klasse, kennung))
    sortiert = sorted(eindeutig.values(), key=lambda z: (z[0].casefold(), z[1]))
    ziel.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(ziel, "wt", encoding="utf-8", newline="") as f:
        f.write("# Quelle: Wikidata (ATC P267, INN P2275, ICD-10 P494, LOINC P4338,"
                " MeSH P486/P672),"
                f" abgerufen {dt.date.today().isoformat()}; Lizenz: CC0 1.0,"
                " https://creativecommons.org/publicdomain/zero/1.0/\n")
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["begriff", "klasse", "kennung"])
        w.writerows(sortiert)
    je_klasse: dict[str, int] = {}
    for _b, k, _q in sortiert:
        je_klasse[k] = je_klasse.get(k, 0) + 1
    print(f"geschrieben: {ziel} ({ziel.stat().st_size / 1e6:.2f} MB), {len(sortiert)} Zeilen,"
          f" {len({b.casefold() for b, _k, _q in sortiert})} Begriffe")
    print(", ".join(f"{k} {n}" for k, n in sorted(je_klasse.items(), key=lambda kv: -kv[1])))


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else ZIEL)
