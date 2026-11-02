"""Build ``anonymed/daten/orphanet_praevalenz_de.csv.gz`` from Orphanet's German files.

Sources (Orphanet, licence CC BY 4.0, https://www.orphadata.com/):
``de_product9_prev.xml`` (epidemiology: prevalence classes and mean values per 100,000)
and ``de_product1.xml`` (nomenclature: German names and synonyms).  One row per disorder
name or synonym with the disorder's estimated population frequency ``p``.

Choice of the estimate per disorder, in this order of preference: point prevalence, then
prevalence at birth, then lifetime prevalence, then annual incidence (a stand-in), for
Germany, then Europe, then worldwide.  The numeric mean (per 100,000) is used when given,
else the class midpoint; "Unbekannt" becomes 1e-5 (the middle of the rare range), and
disorders known only as cases or families count as < 1/1,000,000.

Usage: python werkzeuge/orphanet_praevalenz.py [de_product9_prev.xml de_product1.xml]
"""

from __future__ import annotations

import csv
import gzip
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

URL_PREV = "https://www.orphadata.com/data/xml/de_product9_prev.xml"
URL_NOM = "https://www.orphadata.com/data/xml/de_product1.xml"
ZIEL = (Path(__file__).resolve().parent.parent / "anonymed" / "daten"
        / "orphanet_praevalenz_de.csv.gz")
KLASSEN = {
    ">1 / 1000": 2e-3, "6-9 / 10 000": 7.5e-4, "1-5 / 10 000": 3e-4, "1-9 / 100 000": 5e-5,
    "1-9 / 1 000 000": 5e-6, "<1 / 1 000 000": 5e-7, "Unbekannt": 1e-5,
    "Noch nicht beschrieben": 1e-5,
}
TYPEN = {"Punktprävalenz": 0, "Prävalenz bei Geburt": 1, "Lebenszeitprävalenz": 2,
         "Jahresinzidenz": 3}
GEO = {"Germany": 0, "Deutschland": 0, "Europa": 1, "Weltweit": 2}
AUSGESCHLOSSENE_TYPEN = {"Kategorie", "Phenom-Gruppe"}


def _lade(url: str, pfad: str | None) -> bytes:
    if pfad:
        return Path(pfad).read_bytes()
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "AnonyMed"}),
                                timeout=300) as antwort:
        return antwort.read()


def schaetzung(disorder: ET.Element) -> tuple[float, str, str] | None:
    """(p, class, source) for the best prevalence entry of a disorder."""
    kandidaten = []
    faelle = False
    for p in disorder.iter("Prevalence"):
        typ = (p.findtext("PrevalenceType/Name") or "").strip()
        if typ == "Fälle/Familie(n)":
            faelle = True
            continue
        if typ not in TYPEN:
            continue
        klasse = (p.findtext("PrevalenceClass/Name") or "").strip()
        geo = (p.findtext("PrevalenceGeographic/Name") or "").strip()
        wert = (p.findtext("ValMoy") or "").strip()
        try:
            mittel = float(wert) / 100_000 if wert and float(wert) > 0 else None
        except ValueError:
            mittel = None
        if klasse not in KLASSEN and mittel is None:
            continue
        pw = mittel if mittel is not None else KLASSEN[klasse]
        kandidaten.append((TYPEN[typ], GEO.get(geo, 3), pw, klasse or f"{wert}/100000",
                           f"{typ}, {geo}"))
    if kandidaten:
        kandidaten.sort(key=lambda k: (k[0], k[1]))
        _t, _g, pw, klasse, quelle = kandidaten[0]
        return pw, klasse, quelle
    if faelle:
        return 5e-7, "<1 / 1 000 000", "Fälle/Familie(n)"
    return None


def main(argv: list[str]) -> int:
    prev = ET.fromstring(_lade(URL_PREV, argv[1] if len(argv) > 2 else None))
    nom = ET.fromstring(_lade(URL_NOM, argv[2] if len(argv) > 2 else None))
    stand = prev.get("date", "")[:10]
    namen: dict[str, tuple[str, list[str], str]] = {}
    for d in nom.iter("Disorder"):
        code = d.findtext("OrphaCode") or ""
        namen[code] = (d.findtext("Name") or "", [s.text or "" for s in d.iter("Synonym")],
                       d.findtext("DisorderType/Name") or "")
    zeilen = []
    for d in prev.iter("Disorder"):
        code = d.findtext("OrphaCode") or ""
        typ = d.findtext("DisorderType/Name") or namen.get(code, ("", [], ""))[2]
        if typ in AUSGESCHLOSSENE_TYPEN:
            continue
        s = schaetzung(d)
        if s is None:
            continue
        pw, klasse, quelle = s
        name = d.findtext("Name") or namen.get(code, ("", [], ""))[0]
        begriffe = [name] + namen.get(code, ("", [], ""))[1]
        for i, b in enumerate(begriffe):
            b = " ".join(b.split())
            if len(b) >= 4:
                zeilen.append([code, b, f"{pw:.3g}", klasse, quelle,
                               "synonym" if i else "name"])
    ZIEL.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(ZIEL, "wt", encoding="utf-8", newline="") as f:
        f.write(f"# Quelle: Orphanet, Epidemiologie (de_product9_prev.xml) und Nomenklatur"
                f" (de_product1.xml), Stand {stand}; Lizenz CC BY 4.0, https://www.orphadata.com/\n")
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["orpha", "begriff", "p", "klasse", "quelle", "art"])
        w.writerows(zeilen)
    print(f"{len(zeilen)} Begriffe ({len({z[0] for z in zeilen})} Erkrankungen, Stand {stand})"
          f" nach {ZIEL} geschrieben ({ZIEL.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
