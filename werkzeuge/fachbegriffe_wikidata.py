"""Build the bundled medical vocabulary from Wikidata (CC0).

German labels and aliases of every item that carries an ICD-10 code (P494), an
ATC code (P267), a WHO INN (P2275) or a MeSH descriptor ID (P486) in one of the
medical MeSH trees A–G (tree code P672), fetched in chunks from the SPARQL
endpoint and written gzip-compressed, one term per line, with provenance lines
at the top.

Usage: uv run python werkzeuge/fachbegriffe_wikidata.py [ziel.txt.gz]
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
AGENT = ("AnonyMed/0.1 (open-source medical de-identification;"
         " https://github.com/fre-ms/AnonyMed)")
ZIEL = Path(__file__).resolve().parent.parent / "anonymed" / "daten" / "fachbegriffe_de.txt.gz"


def abfrage(sparql: str, versuche: int = 4) -> list[str]:
    url = ENDPUNKT + "?" + urllib.parse.urlencode({"query": sparql})
    for versuch in range(versuche):
        req = urllib.request.Request(url, headers={"Accept": "text/csv", "User-Agent": AGENT})
        try:
            with urllib.request.urlopen(req, timeout=120) as antwort:
                text = antwort.read().decode("utf-8")
            zeilen = list(csv.reader(io.StringIO(text)))
            return [z[0] for z in zeilen[1:] if z and z[0]]
        except Exception as e:  # noqa: BLE001 - retry on timeouts and 429
            wartezeit = 10 * (versuch + 1)
            print(f"  Versuch {versuch + 1} fehlgeschlagen ({e}); warte {wartezeit}s",
                  file=sys.stderr)
            time.sleep(wartezeit)
    raise RuntimeError("Wikidata-Abfrage wiederholt fehlgeschlagen")


def begriffe(bedingung: str) -> set[str]:
    sparql = f"""SELECT DISTINCT ?label WHERE {{
  {bedingung}
  {{ ?item rdfs:label ?label }} UNION {{ ?item skos:altLabel ?label }}
  FILTER(LANG(?label) = "de")
}}"""
    return set(abfrage(sparql))


def main(ziel: Path) -> None:
    gesamt: set[str] = set()
    schritte = [("P494 ICD-10", "?item wdt:P494 ?v ."), ("P267 ATC", "?item wdt:P267 ?v ."),
                ("P2275 INN", "?item wdt:P2275 ?v .")]
    # MeSH only in its medical trees: A anatomy, B organisms, C diseases, D chemicals and
    # drugs, E techniques, F psychiatry and psychology, G phenomena.  Geography (Z), persons
    # and occupations (M), food, technology, humanities and the like are left out, so that
    # towns and surnames do not end up protected from the name recognition.
    for praefix in ("D000", "D001", "D002", "D003", "D004", "D005", "D006", "D007", "D008",
                    "D009", "D01", "D02", "D03", "D04", "D05", "D06", "D07"):
        schritte.append((f"P486 MeSH {praefix}* (Bäume A–G)",
                         f'?item wdt:P486 ?v . FILTER(STRSTARTS(?v, "{praefix}"))'
                         ' ?item wdt:P672 ?baum . FILTER(REGEX(?baum, "^[A-G]"))'))
    for name, bedingung in schritte:
        t = time.time()
        neu = begriffe(bedingung)
        gesamt |= neu
        print(f"{name}: {len(neu)} Begriffe ({time.time() - t:.0f}s), gesamt {len(gesamt)}")
        time.sleep(2)
    brauchbar = {b.strip() for b in gesamt if len(b.strip()) >= 3 and not b.strip().isdigit()}
    bereinigt = sorted(brauchbar, key=str.casefold)
    kopf = [
        "# AnonyMed Fachvokabular (Basis), erzeugt am "
        + dt.date.today().isoformat() + " aus Wikidata (CC0 1.0).",
        "# Deutsche Bezeichnungen und Aliasse aller Einträge mit ICD-10 (P494), ATC (P267), INN"
        " (P2275) oder MeSH-Deskriptor (P486) in den Bäumen A–G (P672).",
        "# Quelle: https://query.wikidata.org/ – Lizenz:"
        " https://creativecommons.org/publicdomain/zero/1.0/",
        f"# {len(bereinigt)} Begriffe, einer je Zeile.",
    ]
    ziel.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(ziel, "wt", encoding="utf-8") as f:
        f.write("\n".join(kopf) + "\n")
        f.write("\n".join(bereinigt) + "\n")
    print(f"geschrieben: {ziel} ({ziel.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else ZIEL)
