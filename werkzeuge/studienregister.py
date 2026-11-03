"""Build ``pseudonymed/daten/studien_dach.csv.gz``: clinical studies with sites in DE, AT or CH.

A named study ("Teilnahme an der XY-Studie") narrows down who a patient can be, the more so
the smaller the study and the fewer its centres in the DACH region.  This script collects
acronym, titles, size and DACH sites of registered studies so that the residual-risk check
can look them up.

Sources:

1. ClinicalTrials.gov, U.S. National Library of Medicine, API v2
   (https://clinicaltrials.gov/api/v2/studies): every study, interventional or observational,
   with at least one location in Germany, Austria or Switzerland.  Use is governed by the
   ClinicalTrials.gov Terms and Conditions (https://clinicaltrials.gov/about-site/terms-conditions):
   free of charge, redistribution allowed provided that the source is attributed as
   ClinicalTrials.gov, the date the data were processed is displayed, modifications are stated
   and no proprietary rights are claimed; outside the United States NLM asserts copyright.
   Pages are fetched with ``pageSize=1000`` and ``pageToken``, at most one request per second,
   and cached per data timestamp, so an interrupted run resumes where it stopped.
2. DRKS – Deutsches Register Klinischer Studien (BfArM), full JSON export
   (https://drks.de/search/de/download/all-json, documented in the BfArM's public help, no
   account).  The DRKS terms of use
   (https://www.bfarm.de/DE/Das-BfArM/Aufgaben/Deutsches-Register-Klinischer-Studien/Studie-suchen/_node.html#nutzungsbedingungen)
   put entries registered from 2025 (or last updated in 2025) under CC BY 4.0, while older
   entries may be subject to their authors' copyright and must not be redistributed without
   consent.  Only entries registered from 1 January 2025 are therefore kept by default (the
   unambiguous reading); ``--drks-umfang aktualisiert-ab-2025`` also keeps older entries last
   updated in 2025 or later.

Modifications (stated in the file header as the terms require): only studies with a DACH
site; selected fields; titles kept in full unless ``--titel-max`` or ``--ohne-titel`` is given;
at most 20 DACH cities, without placeholders such as "Online" or "bundesweit", postcodes and
country suffixes; DRKS status codes mapped to the ClinicalTrials.gov vocabulary; secondary
identifiers reduced to register numbers (NCT, DRKS, EudraCT, EU CT, ISRCTN, UTN and other WHO
primary registers).

Columns: register, kennung, akronym, kurztitel, titel, teilnehmer, teilnehmer_art
(``geplant``/``tatsaechlich``, empty if the register does not say), zentren_dach (registered
sites in DE/AT/CH, placeholders included), laender_dach, orte_dach, status, start,
weitere_kennungen.  For ClinicalTrials.gov, ``kurztitel`` is the brief and ``titel`` the
official title (empty if identical); for DRKS, the German and the English title.  DRKS sites
carry no country: DRKS studies whose recruitment countries exclude DE, AT and CH are dropped;
for studies that also recruit elsewhere (or name no country), only sites in a known DACH place
(Destatis municipalities, ClinicalTrials.gov DACH cities named by at least three studies) count.

Usage: python -I werkzeuge/studienregister.py [--zwischenspeicher DIR] [--drks-json FILE]
       [--ohne-drks] [--drks-umfang registriert-ab-2025|aktualisiert-ab-2025]
       [--titel-max N] [--ohne-titel]
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import io
import json
import re
import statistics
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
ZIEL = WURZEL / "pseudonymed" / "daten" / "studien_dach.csv.gz"
GEMEINDEN = WURZEL / "pseudonymed" / "daten" / "gemeinden_de.csv.gz"
AGENT = "PseudonyMed-studienregister/1.0 (+https://github.com/fre-ms/PseudonyMed)"

CTG_STUDIEN = "https://clinicaltrials.gov/api/v2/studies"
CTG_VERSION = "https://clinicaltrials.gov/api/v2/version"
CTG_BEDINGUNGEN = "https://clinicaltrials.gov/about-site/terms-conditions"
CTG_FILTER = ("AREA[LocationCountry]Germany OR AREA[LocationCountry]Austria"
              " OR AREA[LocationCountry]Switzerland")
CTG_FELDER = ",".join([
    "NCTId", "Acronym", "BriefTitle", "OfficialTitle", "SecondaryIdInfo", "OrgStudyIdInfo",
    "OverallStatus", "StartDate", "EnrollmentInfo", "LocationCity", "LocationCountry",
])
CTG_LAENDER = {"Germany": "DE", "Austria": "AT", "Switzerland": "CH"}

DRKS_EXPORT = "https://drks.de/search/de/download/all-json"
DRKS_BEDINGUNGEN = ("https://www.bfarm.de/DE/Das-BfArM/Aufgaben/Deutsches-Register-Klinischer-"
                    "Studien/Studie-suchen/_node.html#nutzungsbedingungen")
CC_BY = "https://creativecommons.org/licenses/by/4.0/"
# DRKS recruitment status -> ClinicalTrials.gov overall status.
DRKS_STATUS = {
    "PENDING": "NOT_YET_RECRUITING", "RECRUITING": "RECRUITING",
    "INVITE_ONLY": "ENROLLING_BY_INVITATION", "SUSPENDED": "SUSPENDED",
    "COMPLETE_FOLLOW_UP_CONTINUING": "ACTIVE_NOT_RECRUITING",
    "COMPLETE_FOLLOW_UP_COMPLETE": "COMPLETED", "DISCONTINIUED": "TERMINATED",
    "DISCONTINUED": "TERMINATED", "WITHDRAWN": "WITHDRAWN",
}

DACH = ("DE", "AT", "CH")
MAX_ORTE = 20
MIN_STUDIEN_ORT = 3  # ClinicalTrials.gov cities named less often may be data-entry errors
_PLATZHALTER = re.compile(
    r"(?:online.*|keine angabe|k\.? ?a\.?|n/?a|none|unknown|tbd|diverse|various|several"
    r"|multiple.*|many locations|investigat\w* (?:centers?|centres?|sites?)|(?:research|study)"
    r" sites?|(?:deutschland|bundes|europa|welt) ?weit|deutschland|germany|österreich|austria"
    r"|schweiz|switzerland|universität|hochschule|klinik|[-.?]+)")
_LAND_ZUSATZ = re.compile(r"\s*(?:,|-|–)\s*(?:deutschland|germany|österreich|austria|schweiz"
                          r"|switzerland)\s*$", re.I)
PAUSE = 1.1  # seconds between requests to the same server
KOPF = ["register", "kennung", "akronym", "kurztitel", "titel", "teilnehmer", "teilnehmer_art",
        "zentren_dach", "laender_dach", "orte_dach", "status", "start", "weitere_kennungen"]

# Register numbers worth keeping as secondary identifiers.  The EU CT pattern must be tried
# before EudraCT, whose shape is a prefix of it.
_KENNUNGEN = [
    (re.compile(r"\bNCT\s?(\d{8})\b", re.I), "NCT{}"),
    (re.compile(r"\bDRKS\s?(\d{8})\b", re.I), "DRKS{}"),
    (re.compile(r"\bISRCTN\s?(\d{8})\b", re.I), "ISRCTN{}"),
    (re.compile(r"\b((?:19|20)\d{2}-5\d{5}-\d{2}-\d{2})\b"), "EUCT:{}"),
    (re.compile(r"\b((?:19|20)\d{2}-\d{6}-\d{2})\b(?!-\d)"), "EudraCT:{}"),
    (re.compile(r"\b(U1111-\d{4}-\d{4})\b", re.I), "UTN:{}"),
    (re.compile(r"\b(ACTRN\d{14}|ChiCTR[-\dA-Z]{6,}|CTRI/\d{4}/\d+/\d+|jRCT\w{8,}"
                r"|NTR\d{3,}|KCT\d{7}|IRCT\w{8,}|PACTR\d{15}|SLCTR/\d{4}/\d+"
                r"|RBR-\w{6,}|TCTR\d{11}|ITMCTR\d+|LBCTR\d+|CTIS\d+)\b", re.I), "{}"),
]


# --------------------------------------------------------------------------- downloads


def _speichere(datei: Path, daten: bytes) -> None:
    """Write atomically, so that an interrupted run never leaves a truncated cache file."""
    teil = datei.with_name(datei.name + ".teil")
    teil.write_bytes(daten)
    teil.replace(datei)


def _hole(url: str, versuche: int = 6, zeitlimit: int = 120) -> bytes:
    """GET ``url`` with retries and exponential back-off on server or network errors."""
    for versuch in range(versuche):
        try:
            anfrage = urllib.request.Request(url, headers={"User-Agent": AGENT})
            with urllib.request.urlopen(anfrage, timeout=zeitlimit) as antwort:
                return antwort.read()
        except urllib.error.HTTPError as fehler:
            if fehler.code not in (429, 500, 502, 503, 504) or versuch == versuche - 1:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if versuch == versuche - 1:
                raise
        warten = 2 ** (versuch + 1)
        print(f"  Fehler bei {url[:80]}…, neuer Versuch in {warten} s", file=sys.stderr)
        time.sleep(warten)
    raise RuntimeError("unreachable")


def ctgov_seiten(zwischenspeicher: Path) -> tuple[str, Iterator[dict]]:
    """Download (or resume) all DACH pages; return the data timestamp and the studies."""
    stempel = json.loads(_hole(CTG_VERSION))["dataTimestamp"]
    ordner = zwischenspeicher / f"ctgov_{re.sub(r'[^0-9T]', '', stempel)}"
    ordner.mkdir(parents=True, exist_ok=True)
    nummer, token = 1, None
    while True:
        datei = ordner / f"seite_{nummer:04d}.json"
        if datei.exists():
            seite = json.loads(datei.read_text(encoding="utf-8"))
        else:
            parameter = {"filter.advanced": CTG_FILTER, "fields": CTG_FELDER,
                         "pageSize": "1000", "countTotal": "true" if nummer == 1 else "false"}
            if token:
                parameter["pageToken"] = token
            time.sleep(PAUSE)
            roh = _hole(f"{CTG_STUDIEN}?{urllib.parse.urlencode(parameter)}")
            seite = json.loads(roh)
            _speichere(datei, roh)
            gesamt = seite.get("totalCount")
            print(f"  ClinicalTrials.gov Seite {nummer}"
                  + (f" (insgesamt {gesamt} Studien)" if gesamt else ""), file=sys.stderr)
        token = seite.get("nextPageToken")
        if not token:
            break
        nummer += 1

    def studien() -> Iterator[dict]:
        for datei in sorted(ordner.glob("seite_*.json")):
            yield from json.loads(datei.read_text(encoding="utf-8")).get("studies", [])

    return stempel[:10], studien()


def drks_export(zwischenspeicher: Path, pfad: str | None) -> tuple[str, list[dict]]:
    """Load the DRKS full export from ``pfad`` or download today's copy once."""
    if pfad:
        datei = Path(pfad)
    else:
        datei = zwischenspeicher / f"drks_all_{dt.date.today():%Y%m%d}.zip"
        if not datei.exists():
            print("  DRKS-Gesamtexport wird geladen …", file=sys.stderr)
            _speichere(datei, _hole(DRKS_EXPORT, zeitlimit=900))
    roh = datei.read_bytes()
    if zipfile.is_zipfile(io.BytesIO(roh)):
        z = zipfile.ZipFile(io.BytesIO(roh))
        name = next(n for n in z.namelist() if n.lower().endswith(".json"))
        stand = re.search(r"(\d{4})(\d{2})(\d{2})", name)
        roh = z.read(name)
    else:
        stand = re.search(r"(\d{4})(\d{2})(\d{2})", datei.name)
    datum = "-".join(stand.groups()) if stand else dt.date.today().isoformat()
    return datum, json.loads(roh)


# --------------------------------------------------------------------------- helpers


def _text(wert: object) -> str:
    """Single-line text without the separator used in list columns."""
    if not isinstance(wert, str):
        return ""
    return re.sub(r"\s+", " ", wert.replace("_x000D_", " ")).replace(" | ", " / ").strip()


def _kuerzen(text: str, laenge: int) -> str:
    """Shorten ``text`` to ``laenge`` characters; 0 means no limit."""
    if laenge <= 0 or len(text) <= laenge:
        return text
    return text[: laenge - 1].rstrip() + "…"


def _kennungen(werte: list[str], eigene: str) -> str:
    """Register numbers found in ``werte``, normalised, without the record's own one."""
    gefunden: list[str] = []
    for wert in werte:
        rest = wert
        for muster, form in _KENNUNGEN:
            for treffer in muster.finditer(rest):
                kennung = form.format(treffer.group(1))
                if kennung.upper().startswith(("NCT", "DRKS", "ISRCTN")):
                    kennung = kennung.upper()
                if kennung != eigene and kennung not in gefunden:
                    gefunden.append(kennung)
            rest = muster.sub(" ", rest)
    return " | ".join(gefunden)


def _ort(stadt: str) -> str:
    """Display form of a site's city: no postcode, no country suffix, no placeholder."""
    stadt = _text(stadt).replace("|", "/")
    stadt = _LAND_ZUSATZ.sub("", re.sub(r"^(?:[A-Z]-)?\d{4,5}\s+", "", stadt)).strip()
    return "" if _PLATZHALTER.fullmatch(stadt.casefold()) else stadt


def _orte(staedte: list[str]) -> str:
    gesehen: dict[str, str] = {}
    for stadt in map(_ort, staedte):
        if stadt:
            gesehen.setdefault(stadt.casefold(), stadt)
    return " | ".join(list(gesehen.values())[:MAX_ORTE])


def _zahl(wert: object) -> int | None:
    try:
        zahl = int(wert)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return zahl if zahl >= 0 else None


def _ortsschluessel(stadt: str) -> str:
    """Comparable form of a city name ("Halle (Saale)" -> "halle")."""
    stadt = re.split(r"[(/,]| - ", _ort(stadt))[0]
    return re.sub(r"\s+", " ", stadt).strip().casefold()


# --------------------------------------------------------------------------- records


def zeile_ctgov(studie: dict, titel_max: int, ohne_titel: bool = False) -> list | None:
    p = studie.get("protocolSection", {})
    ident = p.get("identificationModule", {})
    nct = ident.get("nctId", "")
    orte = [o for o in p.get("contactsLocationsModule", {}).get("locations", [])
            if o.get("country") in CTG_LAENDER]
    if not nct or not orte:
        return None
    laender = {CTG_LAENDER[o["country"]] for o in orte}
    umfang = p.get("designModule", {}).get("enrollmentInfo", {})
    teilnehmer = _zahl(umfang.get("count"))
    art = {"ACTUAL": "tatsaechlich", "ESTIMATED": "geplant"}.get(umfang.get("type", ""), "")
    sekundaer = [s.get("id", "") for s in ident.get("secondaryIdInfos", [])]
    sekundaer.append(ident.get("orgStudyIdInfo", {}).get("id", ""))
    kurz = _text(ident.get("briefTitle"))
    lang = _text(ident.get("officialTitle"))
    return [
        "ClinicalTrials.gov", nct, _text(ident.get("acronym")),
        _kuerzen(kurz, titel_max),
        "" if lang == kurz or ohne_titel else _kuerzen(lang, titel_max),
        "" if teilnehmer is None else teilnehmer, art if teilnehmer is not None else "",
        len(orte), " ".join(c for c in DACH if c in laender),
        _orte([o.get("city", "") for o in orte]),
        p.get("statusModule", {}).get("overallStatus", ""),
        p.get("statusModule", {}).get("startDateStruct", {}).get("date", ""),
        _kennungen(sekundaer, nct),
    ]


def zeile_drks(studie: dict, titel_max: int, dach_orte: set[str],
               ohne_titel: bool = False) -> list | None:
    kennung = studie.get("drksId", "")
    r = studie.get("recruitment") or {}
    laender = {c.get("idCountry", {}).get("code") for c in r.get("countries", [])}
    institute = r.get("institutes") or []
    if laender and laender <= set(DACH):
        orte = institute
    else:  # mixed or missing countries: keep only sites in a known DACH place
        orte = [i for i in institute if _ortsschluessel(i.get("city") or "") in dach_orte]
    laender_dach = [c for c in DACH if c in laender]
    # Recruitment countries without DE/AT/CH rule a study out; a DACH place counts only when
    # no country is given at all (then Germany, the register's home, is assumed).
    if not kennung or (laender and not laender_dach) or not (laender_dach or orte):
        return None
    if not laender_dach:
        laender_dach = ["DE"]
    texte = {d.get("idLocale", {}).get("locale"): d for d in studie.get("trialDescriptions", [])}
    de, en = texte.get("de", {}), texte.get("en", {})
    akronym = _text(de.get("acronym")) or _text(en.get("acronym"))
    kurz, lang = _text(de.get("title")), _text(en.get("title"))
    if not kurz:
        kurz, lang = lang, ""
    tatsaechlich, geplant = _zahl(r.get("totalParticipants")), _zahl(r.get("plannedParticipants"))
    if tatsaechlich:
        teilnehmer, art = tatsaechlich, "tatsaechlich"
    elif geplant is not None:
        teilnehmer, art = geplant, "geplant"
    else:
        teilnehmer, art = tatsaechlich, "tatsaechlich" if tatsaechlich is not None else ""
    s = studie.get("secondaryIds") or {}
    sekundaer = [str(s.get(k) or "") for k in
                 ("otherPrimaryRegisterId", "universalTrialNumber", "eudraCtNumber")]
    sekundaer += [str(o.get("value") or "") for o in studie.get("otherSecondaryIds") or []]
    eudract = _text(s.get("eudraCtNumber"))
    return [
        "DRKS", kennung, akronym, _kuerzen(kurz, titel_max),
        "" if lang == kurz or ohne_titel else _kuerzen(lang, titel_max),
        "" if teilnehmer is None else teilnehmer, art,
        len(orte), " ".join(laender_dach), _orte([i.get("city", "") for i in orte]),
        DRKS_STATUS.get(r.get("status") or "", r.get("status") or ""),
        r.get("actualStartDate") or r.get("scheduledStartDate") or "",
        _kennungen(sekundaer + ([f"EudraCT {eudract}"] if eudract else []), kennung),
    ]


def drks_frei(studie: dict, umfang: str) -> bool:
    """True if the entry falls under CC BY 4.0 according to the DRKS terms of use."""
    if (studie.get("registrationDrks") or "") >= "2025-01-01":
        return True
    return umfang == "aktualisiert-ab-2025" and (studie.get("lastUpdate") or "") >= "2025-01-01"


def gemeindenamen() -> set[str]:
    """German municipality names from the bundled Destatis file (for DRKS site matching)."""
    if not GEMEINDEN.exists():
        return set()
    with gzip.open(GEMEINDEN, "rt", encoding="utf-8") as f:
        zeilen = (z for z in f if not z.startswith("#"))
        return {_ortsschluessel(z["name"]) for z in csv.DictReader(zeilen)}


# --------------------------------------------------------------------------- main


def kennzahlen(zeilen: list[list]) -> str:
    """Short summary per register: count, share with acronym, quartiles of participants."""
    aus = []
    for register in dict.fromkeys(z[0] for z in zeilen):
        teil = [z for z in zeilen if z[0] == register]
        zahlen = sorted(z[5] for z in teil if isinstance(z[5], int))
        quartile = statistics.quantiles(zahlen, n=4) if len(zahlen) > 1 else [0, 0, 0]
        aus.append(f"{register}: {len(teil)} Studien, {sum(bool(z[2]) for z in teil)} mit Akronym"
                   f" ({sum(bool(z[2]) for z in teil) / len(teil):.1%}), Teilnehmer Q1/Median/Q3"
                   f" {quartile[0]:.0f}/{quartile[1]:.0f}/{quartile[2]:.0f}")
    return "\n".join(aus)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--zwischenspeicher", type=Path,
                    default=Path(tempfile.gettempdir()) / "pseudonymed-studienregister")
    ap.add_argument("--drks-json", help="local DRKS export (zip or json) instead of downloading")
    ap.add_argument("--ohne-drks", action="store_true")
    ap.add_argument("--drks-umfang", choices=["registriert-ab-2025", "aktualisiert-ab-2025"],
                    default="registriert-ab-2025")
    ap.add_argument("--titel-max", type=int, default=0,
                    help="shorten titles to N characters (default 0: keep them in full)")
    ap.add_argument("--ohne-titel", action="store_true",
                    help="leave the column titel empty (kurztitel stays)")
    ap.add_argument("--ziel", type=Path, default=ZIEL)
    a = ap.parse_args(argv[1:])
    a.zwischenspeicher.mkdir(parents=True, exist_ok=True)

    stand_ctg, studien = ctgov_seiten(a.zwischenspeicher)
    zeilen, ortszahl = [], Counter()
    for studie in studien:
        zeile = zeile_ctgov(studie, a.titel_max, a.ohne_titel)
        if zeile:
            zeilen.append(zeile)
            ortszahl.update({_ortsschluessel(o) for o in zeile[9].split(" | ") if o})
    zeilen.sort(key=lambda z: z[1])
    dach_orte = gemeindenamen() | {o for o, n in ortszahl.items() if n >= MIN_STUDIEN_ORT}

    quelle = (f"ClinicalTrials.gov (U.S. National Library of Medicine, a United States Government"
              f" database), API v2, Daten verarbeitet am {stand_ctg}")
    lizenz = f"ClinicalTrials.gov Terms and Conditions ({CTG_BEDINGUNGEN})"
    if not a.ohne_drks:
        stand_drks, drks = drks_export(a.zwischenspeicher, a.drks_json)
        frei = [s for s in drks if drks_frei(s, a.drks_umfang)]
        neu = [z for z in (zeile_drks(s, a.titel_max, dach_orte, a.ohne_titel)
                           for s in frei) if z]
        zeilen += sorted(neu, key=lambda z: z[1])
        auswahl = ("ab 2025 registrierte Einträge" if a.drks_umfang == "registriert-ab-2025"
                   else "ab 2025 registrierte oder aktualisierte Einträge")
        quelle += (f", und DRKS – Deutsches Register Klinischer Studien (BfArM), Gesamtexport"
                   f" abgerufen am {stand_drks}, nur {auswahl}")
        lizenz += (f", DRKS-Einträge CC BY 4.0 ({CC_BY}) laut DRKS-Nutzungsbedingungen"
                   f" ({DRKS_BEDINGUNGEN})")
        print(f"  DRKS: {len(drks)} Einträge, davon {len(frei)} unter CC BY 4.0,"
              f" {len(neu)} mit DACH-Bezug", file=sys.stderr)
    titel = ("Spalte titel leer" if a.ohne_titel else
             f"Titel auf {a.titel_max} Zeichen gekürzt" if a.titel_max else "Titel ungekürzt")
    quelle += (". Auszug mit Änderungen: nur Studien mit Standort in DE/AT/CH, ausgewählte"
               f" Felder, {titel}, höchstens {MAX_ORTE} Orte ohne Platzhalter, Status"
               " vereinheitlicht, nur Registernummern als weitere Kennungen"
               " (Einzelheiten in werkzeuge/studienregister.py)")

    a.ziel.parent.mkdir(parents=True, exist_ok=True)
    with gzip.GzipFile(a.ziel, "wb", compresslevel=9, mtime=0) as roh, \
            io.TextIOWrapper(roh, encoding="utf-8", newline="") as f:
        f.write(f"# Quelle: {quelle}; Lizenz/Nutzungsbedingungen: {lizenz};"
                f" Stand: {dt.date.today().isoformat()}\n")
        w = csv.writer(f, lineterminator="\n")
        w.writerow(KOPF)
        w.writerows(zeilen)
    print(kennzahlen(zeilen))
    print(f"{len(zeilen)} Studien nach {a.ziel} geschrieben"
          f" ({a.ziel.stat().st_size / 1024 / 1024:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
