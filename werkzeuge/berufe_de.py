"""Build ``anonymed/daten/berufe_de.csv.gz``: German occupations and employment status.

Sources
-------
* **Occupations:** Statistik der Bundesagentur für Arbeit (BA), "Klassifikation der Berufe
  2010 – überarbeitete Fassung 2020, alphabetisches Verzeichnis der Berufsbenennungen"
  (``Alphabetisches-Verzeichnis-Berufsbenennungen.xlsx``, https://statistik.arbeitsagentur.de
  > Grundlagen > Klassifikationen > KldB 2010 > Systematik und Verzeichnisse).  Terms of use,
  quoted from the workbook's sheet "Impressum": "Daten und Tabellen dürfen uneingeschränkt
  verwendet werden. Informationen dürfen (auch auszugsweise) gespeichert und mit
  Quellenangabe weitergegeben, vervielfältigt und verbreitet werden. Die Inhalte dürfen
  nicht verändert oder verfälscht werden. Eigene Berechnungen sind erlaubt, jedoch als
  solche kenntlich zu machen."  Internet publications should link to the BA statistics
  homepage.  Everything this script derives (gender forms, shortened terms, the choice of
  one code per term) is such an own processing step and is marked so in the header line.
* **Everyday occupation words** missing from the directory ("Polizist", "Lkw-Fahrer",
  "MFA"): curated list ``ALLTAGSBERUFE`` with the KldB code of the matching official term
  (own assignment) or a Wikidata QID.
* **Employment status:** curated list in this script; Wikidata QIDs (CC0 1.0) where an item
  with a matching German label and description was checked by hand on 2026-11-02.
* **Surname flag:** Bundesamt für Statistik (BFS), STATPOP, "Nachnamen der ständigen
  Wohnbevölkerung nach Sprachregion" (opendata.swiss, terms "Freie Nutzung. Quellenangabe ist
  Pflicht."; only the counts for German-speaking Switzerland are used), and Wikidata (CC0
  1.0): family-name items (P31 Q101352) labelled exactly like the term, and the number of
  people with that family name (P734) and a German, Austrian or Swiss citizenship (P27).

Method
------
Each "Berufsbenennung" is cut to its core: parenthesised additions, everything after a
spaced dash (" - Fachrichtung"), appended "/Bachelor Professional …" or "/Master
Professional …" qualifications, prepositional tails ("für …", "im …") and trailing
qualifiers after the head noun ("Werkstoffprüfer/in Metall") are dropped.  Alternatives
separated by " / ", commas, "und" or "oder" become separate terms; truncated compounds
("Brau- und Getränketechnologe", "Kiosk-/Imbissverkäufer/in", "Prüfer/in und -berater/in")
are completed with the head noun of the full word, found as the longest suffix that is
itself a term of the directory.  Gender forms are resolved: "Lehrer/in" → Lehrer, Lehrerin;
"Fachangestellte/r" → Fachangestellter, Fachangestellte; "Kaufmann/-frau" → Kaufmann,
Kauffrau; "Gemüsebauer/-bäuerin" → Gemüsebauer, Gemüsebäuerin; "Arzt/Ärztin" and
"Kinderkrankenschwester/-pfleger" (female form first) are oriented by their endings.
``grundform`` is the masculine form (the lemma of the pair); gender-neutral terms
("Fachkraft") are their own base form.  A term that occurs in several Benennungen gets the
KldB code of the Benennung that consists of the bare term alone if there is one, otherwise
the code that occurs most often (ties: lowest code).  A handful of typing errors and
irregular notations in the source are corrected first (``KORREKTUREN``).  Terms of the
curated employment-status list take precedence over occupations of the same spelling.

``nachname`` is 1 when the exact term is a frequent family name: at least
``SCHWELLE_BFS`` bearers in German-speaking Switzerland (BFS) or at least
``SCHWELLE_WIKIDATA`` people with that family name and a DACH citizenship in Wikidata.

Usage::

    .venv/bin/python -I werkzeuge/berufe_de.py [--kldb DATEI.xlsx] [--bfs DATEI.csv]
        [--ohne-wikidata] [--protokoll nachnamen.csv]
        [--wikidata-zwischenspeicher wikidata.json] [ziel.csv.gz]

Without ``--kldb``/``--bfs`` the files are downloaded into memory.
"""

from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import gzip
import io
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

URL_KLDB = ("https://statistik.arbeitsagentur.de/DE/Statischer-Content/Grundlagen/"
            "Klassifikationen/Klassifikation-der-Berufe/KldB2010-Fassung2020/"
            "Systematik-Verzeichnisse/Generische-Publikationen/"
            "Alphabetisches-Verzeichnis-Berufsbenennungen.xlsx?__blob=publicationFile&v=1")
URL_BFS = "https://dam-api.bfs.admin.ch/hub/api/dam/assets/36752522/master"
ENDPUNKT = "https://query.wikidata.org/sparql"
AGENT = ("AnonyMed/0.1 (open-source medical de-identification;"
         " https://github.com/fre-ms/AnonyMed)")
ZIEL = Path(__file__).resolve().parent.parent / "anonymed" / "daten" / "berufe_de.csv.gz"
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}

SCHWELLE_BFS = 100       # bearers in German-speaking Switzerland (population about 6 million)
SCHWELLE_WIKIDATA = 10   # people in Wikidata with that family name and a DACH citizenship
# Germany, Austria, Switzerland and their predecessor states since 1806.
DACH_STAATEN = ("Q183", "Q40", "Q39", "Q713750", "Q16957", "Q43287", "Q41304", "Q7318",
                "Q27306", "Q154195", "Q28513", "Q131964", "Q12548", "Q151624", "Q518101")

# Typing errors and irregular notations of the source, corrected before parsing
# (substring, replacement).  Own corrections, listed in the research notes.
KORREKTUREN: tuple[tuple[str, str], ...] = (
    ("/i n(", "/in ("),
    ("ingenieu/inr", "ingenieur/in"),
    ("Fellverarbeiterhelfer/n", "Fellverarbeiterhelfer/in"),
    ("Facharbeiter/In", "Facharbeiter/in"),
    ("Medizinischer/r", "Medizinische/r"),
    ("Techniche/r", "Technische/r"),
    ("Vormann-/frau", "Vormann/-frau"),
    ("Evangelisch Theologe", "Evangelische/r Theologe"),
    ("Dritte Technische/r", "Dritte/r Technische/r"),
    ("Katholischer Theologe/Katholische Theologin", "Katholische/r Theologe/Theologin"),
    ("Leitender Unterrichtspfleger/Leitende Unterrichtsschwester",
     "Leitende/r Unterrichtspfleger/Unterrichtsschwester"),
    ("Hausdame/Housekeeper", "Hausdame / Housekeeper"),
    ("Virtual/Augmented Reality-Entwickler/in",
     "Virtual-Reality-Entwickler/in / Augmented-Reality-Entwickler/in"),
    ("Application-Engineer/-Manager/in", "Application-Engineer / Application-Manager/in"),
    ("E-Commerce-Manager/-Assistent/in", "E-Commerce-Manager/in / E-Commerce-Assistent/in"),
    ("Automatenbefüller/-kassierer/in", "Automatenbefüller/in / Automatenkassierer/in"),
    ("Marketingfachkraft/-assistent/in", "Marketingfachkraft / Marketingassistent/in"),
    ("Sammlungs- und ähnliche/r Aufseher/in", "Sammlungsaufseher/in"),
    ("Erstgesell/e/in", "Erstgeselle/Erstgesellin"),
    ("ADAS/AD Engineer", "ADAS Engineer / AD Engineer"),
    ("Aufsicht -Messe", "Aufsicht - Messe"),
    ("Bahnbeamter/Bankbeamtin", "Bahnbeamter/Bahnbeamtin"),
    ("(Uni)- ", "(Uni) - "),
    ("Branntsteinbrenner, -trockner, -glasierer/in",
     "Branntsteinbrenner/in, Branntsteintrockner/in, Branntsteinglasierer/in"),
    ("Keramgravierer, -ätzer/in", "Keramgravierer/in, Keramätzer/in"),
    ("Konservierungsguttrockner, -erhitzer/in",
     "Konservierungsguttrockner/in, Konservierungsguterhitzer/in"),
    ("CNC-Fachkraft/NC-Anwendungsfachmann/-frau", "CNC-Fachkraft / NC-Anwendungsfachmann/-frau"),
    ("EDV/PC-Servicetechniker/in", "EDV-Servicetechniker/in / PC-Servicetechniker/in"),
    ("Fachverkäufer/-berater/in", "Fachverkäufer/in / Fachberater/in"),
    ("Foto- und medientechnische/r Assistent/in",
     "Fototechnische/r Assistent/in / Medientechnische/r Assistent/in"),
    ("Informations- und kommunikationstechnische/r Assistent/in",
     "Informationstechnische/r Assistent/in / Kommunikationstechnische/r Assistent/in"),
    ("Magister/Magistra public health", "Magister/Magistra"),
    ("Magister/Magistra sanitatis publicae", "Magister/Magistra"),
    ("Polsterer und Dekorateur/Polsterin und Dekorateurin", "Polsterer/in / Dekorateur/in"),
    ("Relocation-Specialist/-Manager/in", "Relocation-Specialist / Relocation-Manager/in"),
    ("Stallbursche/-mädchen", "Stallbursche/Stallmädchen"),
    ("Commercialmanger", "Commercialmanager"),
    ("Straßenbauer/in -Pflasterer/Pflasterin-", "Straßenbauer/in / Pflasterer/Pflasterin"),
    ("Psychologischer Berater/in", "Psychologische/r Berater/in"),
    ("Textiltechnischer Assistent/in", "Textiltechnische/r Assistent/in"),
    ("Zimmermädchen/Roomboy", "Zimmermädchen / Roomboy"),
)

# Everyday occupation words missing from the directory, which uses official names:
# (notation, reference).  The reference is "=Term" to reuse the KldB code of an official
# term of the directory (own assignment), a Wikidata QID checked by hand, or "".
# Abbreviations get the referenced term as base form.  Left out on purpose because they
# clash with frequent medical abbreviations: PTA (angioplasty), CTA (CT angiography), ATA,
# MTA, PKA (protein kinase A), TFA.
ALLTAGSBERUFE: tuple[tuple[str, str], ...] = (
    ("Polizist/in", "Q384593"), ("Polizeibeamter/-beamtin", "=Polizeivollzugsbeamter"),
    ("Unternehmer/in", "Q131524"), ("Programmierer/in", "Q5482740"),
    ("Lkw-Fahrer/in", "=Berufskraftfahrer"), ("LKW-Fahrer/in", "=Berufskraftfahrer"),
    ("Lastwagenfahrer/in", "=Berufskraftfahrer"), ("Kraftfahrer/in", "=Berufskraftfahrer"),
    ("Bauarbeiter/in", "=Bauhilfsarbeiter"), ("Fabrikarbeiter/in", ""),
    ("Industriearbeiter/in", ""), ("Handwerker/in", ""), ("Geschäftsmann/-frau", ""),
    ("Babysitter/in", "=Kinderbetreuer"), ("Wissenschaftler/in", "Q901"),
    ("Forscher/in", ""), ("Politiker/in", "Q82955"), ("Anwalt/Anwältin", "=Rechtsanwalt"),
    ("Seemann", "Q45199"), ("Kurier/in", "=Kurierfahrer"),
    ("Paketzusteller/in", "=Paketbote"), ("Künstler/in", "Q483501"),
    ("Systemadministrator/in", "=Netzwerkadministrator"),
    ("Versicherungsvertreter/in", ""), ("Referendar/in", ""),
    ("Rechtsreferendar/in", ""), ("Studienreferendar/in", ""),
    ("Pflegekraft", "=Pflegefachkraft"), ("Sportler/in", "Q2066131"),
    ("Profisportler/in", ""), ("Leistungssportler/in", ""), ("Händler/in", ""),
    ("Einzelhändler/in", "=Einzelhandelskaufmann"), ("Wirt/in", "=Gastwirt"),
    ("Hotelier", ""), ("Bankberater/in", "=Bankkaufmann"),
    ("Justizbeamter/-beamtin", ""), ("Berufssoldat/in", "=Soldat"),
    ("Zeitsoldat/in", "=Soldat"),
    ("MFA", "=Medizinischer Fachangestellter"), ("ZFA", "=Zahnmedizinischer Fachangestellter"),
    ("MTRA", "=Medizinisch-technischer Radiologieassistent"),
    ("MTLA", "=Medizinisch-technischer Laboratoriumsassistent"),
)

# Employment status: (notation, QID of the masculine/neutral form, QID of the feminine form
# or None for the same).  Notation as in the KldB ("Rentner/in", "Arbeitslose/r").
ERWERBSSTATUS: tuple[tuple[str, str, str | None], ...] = (
    # pension and retirement
    ("Rentner/in", "Q1749879", None), ("Altersrentner/in", "", None),
    ("Frührentner/in", "", None), ("Erwerbsminderungsrentner/in", "", None),
    ("EU-Rentner/in", "", None), ("EM-Rentner/in", "", None), ("BU-Rentner/in", "", None),
    ("Teilrentner/in", "", None), ("Pensionär/in", "", None), ("Pensionist/in", "", None),
    ("Ruheständler/in", "", None), ("Vorruheständler/in", "", None),
    ("Privatier/Privatière", "", None), ("AHV-Rentner/in", "", None),
    ("IV-Rentner/in", "", None), ("IV-Bezüger/in", "", None),
    ("berentet", "", None), ("verrentet", "", None), ("frühberentet", "", None),
    ("pensioniert", "", None), ("frühpensioniert", "", None), ("emeritiert", "", None),
    ("im Ruhestand", "", None), ("in Rente", "", None), ("in Pension", "", None),
    ("in Frührente", "", None), ("im Vorruhestand", "", None),
    ("Ruhestand", "Q64825398", None), ("Vorruhestand", "", None),
    ("Pensionierung", "Q946865", None), ("Frühpensionierung", "", None),
    ("Rente", "Q268617", None), ("Altersrente", "Q438965", None), ("Frührente", "", None),
    ("Erwerbsminderungsrente", "", None), ("EM-Rente", "", None), ("EU-Rente", "", None),
    ("BU-Rente", "", None), ("Berufsunfähigkeitsrente", "", None),
    ("Erwerbsunfähigkeitsrente", "", None), ("Teilrente", "", None),
    ("Witwenrente", "", None), ("Witwerrente", "", None), ("Waisenrente", "", None),
    ("Pension", "Q156223", None), ("Frühpension", "", None),
    ("Invaliditätspension", "", None), ("Berufsunfähigkeitspension", "", None),
    ("Rehabilitationsgeld", "", None), ("AHV-Rente", "", None), ("IV-Rente", "", None),
    ("Rentenantrag", "", None), ("Altersteilzeit", "Q438986", None),
    ("in Altersteilzeit", "", None),
    # unemployment and benefits
    ("Arbeitslose/r", "Q28790169", None), ("Langzeitarbeitslose/r", "", None),
    ("Erwerbslose/r", "", None), ("Arbeitssuchende/r", "", None),
    ("Arbeitsuchende/r", "", None), ("arbeitslos", "", None),
    ("langzeitarbeitslos", "", None), ("erwerbslos", "", None),
    ("arbeitssuchend", "", None), ("arbeitsuchend", "", None),
    ("beschäftigungslos", "", None), ("ohne Arbeit", "", None),
    ("ohne Beschäftigung", "", None), ("ohne Job", "", None), ("ausgesteuert", "", None),
    ("Arbeitslosigkeit", "Q41171", None), ("Langzeitarbeitslosigkeit", "", None),
    ("Erwerbslosigkeit", "Q1362733", None), ("Arbeitslosengeld", "Q628538", None),
    ("Arbeitslosengeld I", "", None), ("Arbeitslosengeld II", "Q628569", None),
    ("ALG I", "", None), ("ALG 1", "", None), ("ALG II", "", None), ("ALG 2", "", None),
    ("Hartz IV", "", None), ("Hartz 4", "", None), ("Hartz-IV-Empfänger/in", "", None),
    ("Bürgergeld", "Q113957846", None), ("Bürgergeldempfänger/in", "", None),
    ("Bürgergeldbezieher/in", "", None), ("Grundsicherungsgeld", "Q140406332", None),
    ("Grundsicherung", "Q1550701", None), ("Grundsicherung im Alter", "", None),
    ("Sozialhilfe", "Q12322674", None), ("Sozialhilfeempfänger/in", "", None),
    ("Aufstocker/in", "Q760069", None), ("Ein-Euro-Job", "", None),
    ("Notstandshilfe", "Q109923975", None), ("Mindestsicherung", "", None),
    ("Kurzarbeit", "Q316025", None), ("in Kurzarbeit", "", None),
    ("Kurzarbeiter/in", "", None), ("Kurzarbeitergeld", "", None),
    ("Übergangsgeld", "Q331883", None), ("Krankengeld", "Q1786518", None),
    # incapacity for work, rehabilitation
    ("erwerbsunfähig", "", None), ("berufsunfähig", "", None),
    ("erwerbsgemindert", "", None), ("voll erwerbsgemindert", "", None),
    ("teilweise erwerbsgemindert", "", None), ("dienstunfähig", "", None),
    ("arbeitsunfähig", "", None), ("krankgeschrieben", "", None),
    ("im Krankenstand", "", None), ("Erwerbsunfähige/r", "", None),
    ("Berufsunfähige/r", "", None), ("Erwerbsunfähigkeit", "", None),
    ("Berufsunfähigkeit", "Q829071", None), ("Erwerbsminderung", "", None),
    ("Dienstunfähigkeit", "Q1221130", None), ("Arbeitsunfähigkeit", "Q19834774", None),
    ("Wiedereingliederung", "", None), ("stufenweise Wiedereingliederung", "", None),
    ("Hamburger Modell", "", None), ("Umschulung", "Q2478667", None),
    ("in Umschulung", "", None), ("Umschüler/in", "", None), ("Rehabilitand/in", "", None),
    ("Werkstatt für behinderte Menschen", "Q2295494", None), ("WfbM", "", None),
    ("Werkstattbeschäftigte/r", "", None), ("beschützende Werkstatt", "", None),
    # school, training, studies, voluntary and civilian service
    ("Schüler/in", "Q48942", None), ("Grundschüler/in", "", None),
    ("Hauptschüler/in", "", None), ("Realschüler/in", "", None),
    ("Gymnasiast/in", "Q112918691", None), ("Gesamtschüler/in", "", None),
    ("Förderschüler/in", "", None), ("Berufsschüler/in", "", None),
    ("Fachschüler/in", "", None), ("Abiturient/in", "", None),
    ("Student/in", "Q48282", None), ("Studierende/r", "", None),
    ("Werkstudent/in", "Q23685787", None), ("Doktorand/in", "", None),
    ("Auszubildende/r", "Q253567", None), ("Azubi", "", None), ("Lehrling", "", None),
    ("Praktikant/in", "Q315625", None), ("Trainee", "Q126559148", None),
    ("Schulabbrecher/in", "", None), ("Studienabbrecher/in", "", None),
    ("in Ausbildung", "", None), ("im Studium", "", None),
    ("ohne Berufsausbildung", "", None), ("ohne Schulabschluss", "", None),
    ("ungelernt", "", None), ("angelernt", "", None),
    ("Bundesfreiwilligendienst", "Q1006004", None), ("Bufdi", "", None),
    ("Freiwilliges Soziales Jahr", "Q1430892", None), ("FSJ", "", None),
    ("Freiwilliges Ökologisches Jahr", "Q1454945", None), ("FÖJ", "", None),
    ("Freiwilligendienst", "Q1454904", None), ("Zivildienst", "Q702635", None),
    ("Zivildienstleistende/r", "", None), ("Zivildiener", "", None),
    ("Präsenzdienst", "Q1802953", None), ("Präsenzdiener", "", None),
    ("Wehrdienst", "Q209572", None), ("Grundwehrdienst", "", None),
    ("Grundwehrdienstleistende/r", "", None),
    # household and family
    ("Hausmann/-frau", "Q2154210", "Q1934684"), ("Elternzeit", "Q772989", None),
    ("in Elternzeit", "", None), ("Erziehungsurlaub", "Q3122539", None),
    ("Erziehungszeit", "", None), ("Mutterschutz", "Q1956054", None),
    ("im Mutterschutz", "", None), ("Elterngeld", "Q1419924", None),
    ("in Karenz", "", None), ("Bildungskarenz", "", None),
    ("Kinderbetreuungsgeld", "Q1662971", None), ("Pflegezeit", "", None),
    ("Familienpflegezeit", "", None),
    # form of employment
    ("erwerbstätig", "", None), ("berufstätig", "", None),
    ("nicht erwerbstätig", "", None), ("nicht berufstätig", "", None),
    ("vollzeitbeschäftigt", "", None), ("teilzeitbeschäftigt", "", None),
    ("in Vollzeit", "", None), ("in Teilzeit", "", None), ("Vollzeit", "", None),
    ("Teilzeit", "", None), ("Teilzeitarbeit", "Q587972", None),
    ("Teilzeitkraft", "", None), ("Minijob", "", None), ("Minijobber/in", "", None),
    ("Midijob", "Q1390072", None), ("geringfügig beschäftigt", "", None),
    ("geringfügige Beschäftigung", "Q1297203", None), ("450-Euro-Job", "", None),
    ("520-Euro-Job", "", None), ("Aushilfe", "", None), ("Aushilfskraft", "", None),
    ("Leiharbeiter/in", "", None), ("Zeitarbeiter/in", "", None),
    ("Leiharbeit", "", None), ("Zeitarbeit", "", None), ("Saisonarbeiter/in", "", None),
    ("Saisonarbeit", "Q1626021", None), ("Saisonkraft", "", None),
    ("Tagelöhner/in", "Q897359", None), ("Gelegenheitsarbeiter/in", "", None),
    ("Gelegenheitsjob", "", None),
    ("selbstständig", "", None), ("selbständig", "", None),
    ("Selbstständige/r", "", None), ("Selbständige/r", "", None),
    ("Selbstständigkeit", "", None), ("Selbständigkeit", "Q1005490", None),
    ("freiberuflich", "", None), ("Freiberufler/in", "Q19365375", None),
    ("freischaffend", "", None), ("Freelancer/in", "", None),
    ("Gewerbetreibende/r", "", None), ("Existenzgründer/in", "", None),
    ("Ich-AG", "", None), ("verbeamtet", "", None),
    ("Beamter/Beamtin", "Q212238", None), ("Angestellte/r", "Q255274", None),
    ("Arbeiter/in", "Q12713481", None), ("Arbeitnehmer/in", "Q703534", None),
)

PRAEPOSITIONEN = frozenset({
    "für", "im", "in", "an", "am", "bei", "beim", "von", "vom", "mit", "auf", "aus", "zur",
    "zum", "der", "des", "einer", "eines", "nach", "ohne", "über", "unter", "als", "gemäß",
})
# Suffix pairs (masculine, feminine) whose forms share no common beginning.
PAARE: tuple[tuple[str, str], ...] = (
    ("mann", "frau"), ("man", "woman"), ("vater", "mutter"), ("pfleger", "schwester"),
    ("entbindungspfleger", "hebamme"), ("modelleur", "direktrice"), ("steward", "hostess"),
)
WEIBLICHE_ENDUNGEN = ("in", "frau", "mutter", "schwester", "hebamme", "dame", "ess", "esse",
                      "ette", "ice", "iere", "isse", "woman", "magd", "madchen")
_FALTUNG = str.maketrans({"ä": "a", "ö": "o", "ü": "u", "é": "e", "è": "e", "ß": "s"})
_MARKER = re.compile(r"/(?:-|[a-zäöüß])")
_GUELTIG = re.compile(r"^[0-9A-ZÄÖÜ][^/()]*[^-]$")
# Academic degrees listed as Benennungen ("Bachelor of Science - Physik"): left to titel_de.py.
_GRADE = re.compile(r"^(?:Bachelor|Master|European Master|Magister|Magistra)\b")


def _falte(wort: str) -> str:
    """Lower-case and strip umlauts and accents for comparing word beginnings."""
    return wort.lower().translate(_FALTUNG)


def _praefix(a: str, b: str) -> int:
    n = 0
    for x, y in zip(_falte(a), _falte(b), strict=False):
        if x != y:
            break
        n += 1
    return n


def ist_weiblich(wort: str) -> bool:
    """Whether a German person noun looks feminine (used only to orient pairs)."""
    return _falte(wort.split()[-1] if wort.split() else wort).endswith(WEIBLICHE_ENDUNGEN)


def _ersetze_ende(basis: str, ersatz: str) -> str | None:
    """Apply a hyphen replacement such as ``Kaufmann/-frau`` or ``Bauer/-bäuerin``."""
    if ersatz[:1].isupper() and "-" in basis:
        return basis.rsplit("-", 1)[0] + "-" + ersatz
    for m, f in PAARE:
        for von, nach in ((m, f), (f, m)):
            if _falte(ersatz) == nach and _falte(basis).endswith(von):
                rest = basis[:len(basis) - len(von)]
                return rest + (ersatz if rest else ersatz.capitalize())
    beste, stelle = 0, -1
    for i in range(len(basis)):
        n = _praefix(basis[i:], ersatz)
        if n >= beste and n > 0:
            beste, stelle = n, i
    if beste < 2:
        return None
    rest = basis[:stelle]
    return rest + (ersatz if rest else ersatz.capitalize())


def _wortformen(wort: str) -> tuple[str, str] | None:
    """(masculine, feminine) of one word carrying a gender marker, e.g. ``Lehrer/in``."""
    basis, _, rest = wort.partition("/")
    if rest in ("in", "-in", "innen", "n"):
        weiblich = (basis[:-1] if basis.endswith("e") and not basis.endswith("ee")
                    else basis) + "in"
        return basis, weiblich
    if rest == "r":
        return basis + "r", basis
    if rest == "e":
        return basis, basis + "e"
    ersatz = rest.lstrip("-")
    if not ersatz or "/" in ersatz:
        return None
    andere = _ersetze_ende(basis, ersatz)
    if andere is None:
        return None
    if ist_weiblich(basis) and not ist_weiblich(andere):
        return andere, basis
    return basis, andere


def _loese(text: str, geschlecht: str) -> str | None:
    """Resolve all word-level markers of a phrase for one gender (``m`` or ``f``)."""
    woerter = []
    for wort in text.split():
        if _MARKER.search(wort):
            formen = _wortformen(wort)
            if formen is None:
                return None
            wort = formen[0] if geschlecht == "m" else formen[1]
        woerter.append(wort)
    return " ".join(woerter)


def _aehnlich(a: str, b: str) -> bool:
    if _praefix(a, b) >= 3:
        return True
    fa, fb = _falte(a), _falte(b)
    for m, f in PAARE:
        for x, y in ((m, f), (f, m)):
            if fa.endswith(x) and fb.endswith(y) and fa[:len(fa) - len(x)] == fb[:len(fb) - len(y)]:
                return True
    return False


def _gegenstueck(a: str, b: str) -> str | None:
    """The full counterpart phrase if ``b`` is the other gender form of ``a``, else None."""
    woerter_a, woerter_b = a.split(), b.split()
    kopf_a = woerter_a[-1]
    teil_a = kopf_a.rsplit("-", 1)[-1]
    if len(woerter_b) >= len(woerter_a):
        # full form, possibly followed by a qualifier ("Metallurge/Metallurgin Erzeugung")
        kandidat = woerter_b[:len(woerter_a)]
        if _aehnlich(kopf_a, kandidat[-1]):
            return " ".join(kandidat)
        if "-" in kopf_a and "-" not in kandidat[-1] and _aehnlich(teil_a, kandidat[-1]):
            kandidat[-1] = kopf_a.rsplit("-", 1)[0] + "-" + kandidat[-1]   # "EDV-Pädagogin"
            return " ".join(kandidat)
        return None
    if not (_aehnlich(kopf_a, b) or _aehnlich(teil_a, b)):
        return None
    neu = b
    if "-" in kopf_a and "-" not in b and not _aehnlich(kopf_a, b):
        neu = kopf_a.rsplit("-", 1)[0] + "-" + b
    return " ".join(a.split()[:-1] + [neu])


def _paar(a: str, b: str | None) -> tuple[str, str] | None:
    """(masculine, feminine) of one alternative, ``b`` being its explicit counterpart."""
    if b is None:
        m, f = _loese(a, "m"), _loese(a, "f")
    elif ist_weiblich(a) and not ist_weiblich(b):
        m, f = _loese(b, "m"), _loese(a, "f")
    else:
        m, f = _loese(a, "m"), _loese(b, "f")
    if m is None or f is None:
        return None
    return m, f


def _alternativen(teil: str) -> list[tuple[str, str]]:
    """Split one part at ``/Uppercase`` into gender counterparts and further alternatives."""
    stuecke = [s.strip() for s in re.split(r"/(?=[0-9A-ZÄÖÜ])", teil) if s.strip()]
    if not stuecke:
        return []
    ergebnis: list[tuple[str, str]] = []
    aktuell, partner = stuecke[0], None
    for s in stuecke[1:]:
        # only a marker on the head noun completes the pair ("Leitende/r Arzt/Ärztin")
        if partner is None and not _MARKER.search(aktuell.split()[-1]):
            gegen = _gegenstueck(aktuell, s)
            if gegen is not None:
                partner = gegen
                continue
            aktuell = aktuell + "/" + s        # e.g. "ADAS/AD Engineer": keep literally
            continue
        formen = _paar(aktuell, partner)
        if formen:
            ergebnis.append(formen)
        aktuell, partner = s, None
    formen = _paar(aktuell, partner)
    if formen:
        ergebnis.append(formen)
    return ergebnis


def _ist_adjektiv(token: str, folgendes: str | None) -> bool:
    basis = token.split("/")[0]
    return (folgendes is not None and folgendes[:1].isupper()
            and basis.endswith(("e", "er", "es", "en")))


def _schneide_nach_kopf(teil: str) -> str:
    """Drop qualifiers after the first marked head noun ("Werkstoffprüfer/in Metall")."""
    tokens = teil.split()
    for k, token in enumerate(tokens):
        folgendes = tokens[k + 1] if k + 1 < len(tokens) else None
        if _MARKER.search(token) and not (token.endswith("/r")
                                           and _ist_adjektiv(token, folgendes)):
            return " ".join(tokens[:k + 1])
    return teil


def _kopf(wort: str, lexikon: set[str]) -> int | None:
    """Start of the head noun of a compound: the longest proper suffix in the lexicon."""
    basis = wort.split("/")[0]
    if "-" in basis:
        return basis.rindex("-") + 1
    for i in range(3, len(basis) - 3):
        if basis[i:].lower() in lexikon:
            return i
    return None


def _teile(text: str, lexikon: set[str]) -> list[str]:
    """Split at " / ", commas, "und", "oder" and complete truncated compounds."""
    text = re.sub(r"(\w)-/(?=\w)", r"\1- und ", text)
    teile = [t.strip() for t in re.split(r"\s*,\s*|\s+und\s+|\s+oder\s+|\s+/\s+", text)]
    teile = [t for t in teile if t]
    fertig: list[str] = []
    for i, t in enumerate(teile):
        if t.endswith("-"):
            j = next((j for j in range(i + 1, len(teile)) if not teile[j].endswith("-")), None)
            if j is None:
                continue
            erstes = teile[j].split()[0]
            k = _kopf(erstes, lexikon)
            if k is None:
                continue
            *vorne, stumpf = t.split()
            kopf = erstes[k:]
            fertig.append(" ".join([*vorne, stumpf + kopf if "-" in erstes.split("/")[0]
                                    else stumpf[:-1] + kopf]))
            if vorne and len(teile[j].split()) == 1:
                # "Analytische/r Kinder- und Jugendlichenpsychotherapeut/in"
                teile[j] = " ".join([*vorne, teile[j]])
        elif t.startswith("-") and fertig:
            vorher = fertig[-1].split()[-1]
            k = _kopf(vorher, lexikon)
            if k is None:
                continue
            fertig.append(vorher.split("/")[0][:k] + t[1:])
        else:
            fertig.append(t)
    return fertig


def kern(benennung: str) -> str:
    """The part of a Benennung before qualifiers (still with gender markers)."""
    s = benennung
    for alt, neu in KORREKTUREN:
        s = s.replace(alt, neu)
    while re.search(r"\([^()]*\)", s):          # innermost first: "(… lehrer(innen) …)"
        s = re.sub(r"\s*\([^()]*\)", "", s)
    s = s.replace("(", "").replace(")", "")
    s = re.sub(r"/in-(?=[A-ZÄÖÜ])", "/in - ", s)   # "Maschinenformer/in-Gießerei"
    s = re.split(r"\s+[-–]\s+|\s+[-–]\s*$", s)[0]
    s = re.sub(r"\s*/\s*(?:Bachelor|Master) Professional.*$", "", s)
    s = re.sub(r"(?<=\S) /(?=\S)|(?<=\S)/ (?=\S)", "/", s)
    tokens = s.split()
    for j, token in enumerate(tokens[1:], start=1):
        if token in PRAEPOSITIONEN:
            tokens = tokens[:j]
            break
    return " ".join(tokens)


def zerlege(benennung: str, lexikon: set[str]) -> list[tuple[str, str]]:
    """All (masculine, feminine) terms of one Benennung of the directory."""
    s = kern(benennung)
    if not s:
        return []
    teile = [_schneide_nach_kopf(t) for t in _teile(s, lexikon)]
    mit_markern = any("/" in t for t in teile)
    ergebnis = []
    for teil in teile:
        if mit_markern and "/" not in teil:
            continue                      # "…/in, Einweisung": a qualifier, not a term
        for m, f in _alternativen(teil):
            ergebnis.append((" ".join(m.split()), " ".join(f.split())))
    return ergebnis


def lies_kldb(xlsx: bytes) -> tuple[str, list[tuple[str, str]]]:
    """(creation date, [(Benennung, 5-digit code)]) from the BA workbook."""
    z = zipfile.ZipFile(io.BytesIO(xlsx))
    ss = ET.fromstring(z.read("xl/sharedStrings.xml"))
    strings = ["".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t"))
               for si in ss.findall("m:si", NS)]
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    namen = [s.get("name", "") for s in wb.find("m:sheets", NS)]
    blatt = next(i for i, n in enumerate(namen, start=1) if "Verz" in n)
    daten = ET.fromstring(z.read(f"xl/worksheets/sheet{blatt}.xml")).find("m:sheetData", NS)
    paare, stand = [], ""
    for zeile in daten:
        werte: dict[str, str] = {}
        for c in zeile.findall("m:c", NS):
            v = c.findtext("m:v", default="", namespaces=NS)
            if c.get("t") == "s" and v:
                v = strings[int(v)]
            werte["".join(ch for ch in c.get("r", "") if ch.isalpha())] = v.strip()
        a, b = werte.get("A", ""), werte.get("B", "")
        if re.fullmatch(r"\d{5}", b) and a:
            paare.append((a, b))
        elif a.startswith("Erstellungsdatum"):
            m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", a)
            stand = f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else a
    return stand, paare


def berufe(paare: list[tuple[str, str]]) -> tuple[dict[str, tuple[str, str]], list[str]]:
    """{term: (KldB code, base form)} and the Benennungen that could not be resolved."""
    lexikon = {"kraft"}
    for a, _ in paare:
        erstes = kern(a).split(" ")[-1].split("/")[0] if kern(a) else ""
        if erstes and "-" not in erstes and len(erstes) >= 4:
            lexikon.add(erstes.lower())
    stimmen: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    grundformen: dict[str, collections.Counter[str]] = collections.defaultdict(
        collections.Counter)
    fehlschlaege = []
    for a, code in paare:
        formen = zerlege(a, lexikon)
        gut = [(m, f) for m, f in formen if _GUELTIG.match(m) and _GUELTIG.match(f)
               and not _GRADE.match(m)]
        if not gut:
            if not any(_GRADE.match(m) for m, _ in formen):
                fehlschlaege.append(a)
            continue
        nackt = "(" not in a and " - " not in a and len(gut) == 1
        for m, f in gut:
            for begriff in {m, f}:
                stimmen[begriff][code] += 1000 if nackt else 1
                grundformen[begriff][m] += 1000 if nackt else 1
    ergebnis = {}
    for begriff, zaehler in stimmen.items():
        code = min(zaehler, key=lambda c: (-zaehler[c], c))
        grund = min(grundformen[begriff], key=lambda g: (-grundformen[begriff][g], g))
        ergebnis[begriff] = (code, grund)
    for notation, verweis in ALLTAGSBERUFE:
        if verweis.startswith("="):
            if verweis[1:] not in ergebnis:
                print(f"  Verweis fehlt: {verweis[1:]}", file=sys.stderr)
            kennung = ergebnis.get(verweis[1:], ("", ""))[0]
        else:
            kennung = verweis
        for m, f in _alternativen(notation):
            grund = verweis[1:] if m.isupper() and verweis.startswith("=") else m
            for begriff in (m, f):
                ergebnis.setdefault(begriff, (kennung, grund))
    return ergebnis, fehlschlaege


def erwerbsstatus() -> dict[str, tuple[str, str]]:
    """{term: (QID or "", base form)} of the curated employment-status list."""
    ergebnis = {}
    for notation, qid_m, qid_f in ERWERBSSTATUS:
        for m, f in _alternativen(notation):
            ergebnis.setdefault(m, (qid_m, m))
            ergebnis.setdefault(f, (qid_f if qid_f is not None else qid_m, m))
    return ergebnis


def _lade(url: str, pfad: str | None) -> bytes:
    if pfad:
        return Path(pfad).read_bytes()
    req = urllib.request.Request(url, headers={"User-Agent": AGENT})
    with urllib.request.urlopen(req, timeout=300) as antwort:
        return antwort.read()


def bfs_haeufigkeit(roh: bytes) -> tuple[str, dict[str, int]]:
    """(year, {surname: bearers in German-speaking Switzerland}) from the BFS file."""
    leser = csv.DictReader(io.StringIO(roh.decode("utf-8-sig")))
    zahlen, jahr = {}, ""
    for z in leser:
        if z.get("Sprachregion") == "1" and z.get("VALUE", "").isdigit():
            zahlen[z["LASTNAME"]] = int(z["VALUE"])
            jahr = z.get("TIME_PERIOD", jahr)
    return jahr, zahlen


def abfrage(sparql: str, versuche: int = 5) -> list[dict[str, str]]:
    """Run a SPARQL query (POST, CSV) with retries and growing pauses."""
    daten = urllib.parse.urlencode({"query": sparql}).encode()
    for versuch in range(versuche):
        req = urllib.request.Request(ENDPUNKT, data=daten, headers={
            "Accept": "text/csv", "User-Agent": AGENT,
            "Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=180) as antwort:
                text = antwort.read().decode("utf-8")
            return list(csv.DictReader(io.StringIO(text)))
        except Exception as e:  # noqa: BLE001 - retry on timeouts and 429
            wartezeit = 15 * (versuch + 1)
            print(f"  Versuch {versuch + 1} fehlgeschlagen ({e}); warte {wartezeit}s",
                  file=sys.stderr)
            time.sleep(wartezeit)
    raise RuntimeError("Wikidata-Abfrage wiederholt fehlgeschlagen")


def _literal(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def wikidata_nachnamen(begriffe: list[str]) -> dict[str, int]:
    """{term: number of DACH citizens in Wikidata with that family name}, for name terms."""
    items: dict[str, set[str]] = collections.defaultdict(set)
    for i in range(0, len(begriffe), 200):
        stapel = begriffe[i:i + 200]
        werte = " ".join(f"{_literal(b)}@de {_literal(b)}@mul" for b in stapel)
        sparql = (f"SELECT ?l ?item WHERE {{ VALUES ?l {{ {werte} }} ?item rdfs:label ?l . "
                  "?item wdt:P31 wd:Q101352 . }")
        for z in abfrage(sparql):
            items[z["l"]].add(z["item"].rsplit("/", 1)[-1])
        print(f"  Familiennamen gesucht: {min(i + 200, len(begriffe))}/{len(begriffe)},"
              f" gefunden {len(items)}", file=sys.stderr)
        time.sleep(1)
    alle = sorted({q for qs in items.values() for q in qs})
    staaten = " ".join(f"wd:{q}" for q in DACH_STAATEN)
    anzahl: dict[str, int] = {}
    for i in range(0, len(alle), 20):
        stapel = " ".join(f"wd:{q}" for q in alle[i:i + 20])
        sparql = (f"SELECT ?item (COUNT(DISTINCT ?p) AS ?n) WHERE {{ VALUES ?item {{ {stapel} }}"
                  f" ?p wdt:P734 ?item . ?p wdt:P27 ?st . VALUES ?st {{ {staaten} }} }}"
                  " GROUP BY ?item")
        for z in abfrage(sparql):
            anzahl[z["item"].rsplit("/", 1)[-1]] = int(z["n"])
        print(f"  Namensträger gezählt: {min(i + 20, len(alle))}/{len(alle)}", file=sys.stderr)
        time.sleep(1)
    return {b: sum(anzahl.get(q, 0) for q in qs) for b, qs in items.items()}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("ziel", nargs="?", default=str(ZIEL))
    p.add_argument("--kldb", help="local copy of Alphabetisches-Verzeichnis-…xlsx")
    p.add_argument("--bfs", help="local copy of the BFS surname CSV (by language region)")
    p.add_argument("--ohne-wikidata", action="store_true",
                   help="surname flag from the BFS list only")
    p.add_argument("--protokoll", help="write the surname counts per term to this CSV file")
    p.add_argument("--wikidata-zwischenspeicher", metavar="DATEI",
                   help="JSON cache of the Wikidata surname counts (read if present, else written)")
    args = p.parse_args(argv)

    stand_kldb, paare = lies_kldb(_lade(URL_KLDB, args.kldb))
    beruf, fehlschlaege = berufe(paare)
    status = erwerbsstatus()
    print(f"KldB (Stand {stand_kldb}): {len(paare)} Benennungen, {len(beruf)} Begriffe,"
          f" {len(fehlschlaege)} nicht aufgelöst")
    for a in fehlschlaege:
        print(f"  nicht aufgelöst: {a}", file=sys.stderr)
    zeilen = {b: ("Beruf", k, g) for b, (k, g) in beruf.items() if b not in status}
    zeilen.update({b: ("Erwerbsstatus", k, g) for b, (k, g) in status.items()})

    jahr_bfs, bfs = bfs_haeufigkeit(_lade(URL_BFS, args.bfs))
    namenartig = sorted(b for b in zeilen if re.fullmatch(r"[A-ZÄÖÜ][a-zäöüß]+(?:-[A-ZÄÖÜ]"
                                                             r"[a-zäöüß]+)?", b))
    wd: dict[str, int] = {}
    speicher = Path(args.wikidata_zwischenspeicher) if args.wikidata_zwischenspeicher else None
    if not args.ohne_wikidata:
        if speicher and speicher.exists():
            gespeichert = json.loads(speicher.read_text(encoding="utf-8"))
            fehlend = sorted(set(namenartig) - set(gespeichert["abgefragt"]))
            wd = gespeichert["anzahl"] | (wikidata_nachnamen(fehlend) if fehlend else {})
        else:
            wd = wikidata_nachnamen(namenartig)
        if speicher:
            speicher.write_text(json.dumps({"abgefragt": namenartig, "anzahl": wd},
                                           ensure_ascii=False, indent=0), encoding="utf-8")
    nachname = {b for b in namenartig
                if bfs.get(b, 0) >= SCHWELLE_BFS or wd.get(b, 0) >= SCHWELLE_WIKIDATA}
    if args.protokoll:
        with open(args.protokoll, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(["begriff", "klasse", "bfs_deutschschweiz", "wikidata_dach", "nachname"])
            for b in namenartig:
                if b in bfs or b in wd:
                    w.writerow([b, zeilen[b][0], bfs.get(b, 0), wd.get(b, ""),
                                int(b in nachname)])

    heute = dt.date.today().isoformat()
    kopf = ("# Quelle: Statistik der Bundesagentur für Arbeit, Klassifikation der Berufe 2010 –"
            " überarbeitete Fassung 2020, Alphabetisches Verzeichnis der Berufsbenennungen"
            f" (Stand {stand_kldb}), https://statistik.arbeitsagentur.de, eigene Berechnungen"
            " (Geschlechterformen aufgelöst, Zusätze gekürzt, ein Code je Begriff);"
            " Alltagsberufe und Erwerbsstatus: kuratierte Listen AnonyMed mit Wikidata-QIDs"
            " bzw. zugeordneten KldB-Codes; Spalte nachname:"
            f" BFS STATPOP {jahr_bfs}, Nachnamen der ständigen Wohnbevölkerung nach"
            " Sprachregion (Deutschschweiz ≥ "
            f"{SCHWELLE_BFS}), opendata.swiss, und Wikidata (≥ {SCHWELLE_WIKIDATA} Personen"
            " mit DACH-Staatsangehörigkeit); Lizenz: BA-Nutzungsbedingungen (Weitergabe mit"
            " Quellenangabe, eigene Berechnungen kenntlich), BFS opendata.swiss"
            " (Quellenangabe Pflicht), Wikidata CC0 1.0; Stand: " + heute + "\n")
    ziel = Path(args.ziel)
    ziel.parent.mkdir(parents=True, exist_ok=True)
    # no file name and no time stamp in the gzip header: reproducible bytes
    with (open(ziel, "wb") as roh,
          gzip.GzipFile(filename="", fileobj=roh, mode="wb", mtime=0) as gz,
          io.TextIOWrapper(gz, encoding="utf-8", newline="") as f):
        f.write(kopf)
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["begriff", "klasse", "kennung", "grundform", "nachname"])
        for b in sorted(zeilen, key=lambda b: (zeilen[b][0], b.casefold(), b)):
            klasse, kennung, grund = zeilen[b]
            w.writerow([b, klasse, kennung, grund, int(b in nachname)])
    anzahl = collections.Counter(k for k, _, _ in zeilen.values())
    print(f"Berufe {anzahl['Beruf']}, Erwerbsstatus {anzahl['Erwerbsstatus']},"
          f" Nachnamenkonflikte {len(nachname)}")
    print(f"geschrieben: {ziel} ({ziel.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
