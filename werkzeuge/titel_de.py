"""Build ``anonymed/daten/titel_de.csv.gz``: academic, noble, ecclesiastical and official titles.

Sources
-------
* **Curated list** in this script (``kuratiert()``): academic degrees and positions with
  their usual abbreviations, Austrian and Swiss particularities (Mag., DI, lic. phil.,
  med. pract., Austrian "Berufstitel" such as Hofrat or Kommerzialrat), noble titles and
  particles, ecclesiastical titles, official titles ("Amtsbezeichnungen", ranks of the
  police and the Bundeswehr, parliamentary mandates such as MdB) and honorary titles.
  Names of titles and their abbreviations are facts; no table of a third party was copied.
  Abbreviations are only given where they are in common use; otherwise the field is empty.
* **Wikidata** (CC0 1.0): items that are instances or subclasses of "academic degree"
  (Q189533) with a German label and a German short name (P1813, language ``de``; Latin
  short names only in the German pattern "Dr. …", "Mag. …", "lic. …").  Items tied to a
  country outside Germany, Austria and Switzerland (P17), a few items with wrong short names
  (``WIKIDATA_AUSGESCHLOSSEN``), lower-case short names and abbreviations already in the
  curated list are left out, so Wikidata only adds degrees and abbreviations the list lacks.
  Wikidata's noble and honorary title classes were checked too but are dominated by
  non-German titles (British peerages, Soviet honorary titles) and are not used.

Columns: ``begriff`` (full form, one row per gender form), ``klasse`` (always "Titel"),
``art`` (akademisch, adel, kirchlich, amt, ehren), ``abkuerzung`` (one per row; a title
with several abbreviations has several rows), ``land`` (DE, AT, CH or alle).

Usage::

    .venv/bin/python -I werkzeuge/titel_de.py [--ohne-wikidata] [ziel.csv.gz]
"""

from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import gzip
import io
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ENDPUNKT = "https://query.wikidata.org/sparql"
AGENT = ("AnonyMed/0.1 (open-source medical de-identification;"
         " https://github.com/fre-ms/AnonyMed)")
ZIEL = Path(__file__).resolve().parent.parent / "anonymed" / "daten" / "titel_de.csv.gz"
LAENDER = {"Q183": "DE", "Q40": "AT", "Q39": "CH"}
# Wrong or misleading short names on Wikidata (checked 2026-11-02): Abitur is no title;
# "Ph.D." carries the short name "Dr. phil."; "Dipl.-Ing." carries "M.Sc."; "Candidata
# oeconomiae" carries "cand. odont."; "Landinspektor" is a Danish degree.
WIKIDATA_AUSGESCHLOSSEN = frozenset({"Q708387", "Q752297", "Q15206450", "Q55334492",
                                     "Q66499193"})
ARTEN = ("akademisch", "adel", "kirchlich", "amt", "ehren")

Zeile = tuple[str, str, str, str]          # begriff, art, abkuerzung, land


def weiblich(begriff: str) -> str:
    """Feminine form of a German title noun ("Diplom-Psychologe" → "Diplom-Psychologin")."""
    kopf, _, rest = begriff.partition(" ")
    if kopf.endswith("mann"):
        kopf = kopf[:-4] + "frau"
    elif kopf.lower().endswith("rat"):
        kopf = kopf[:-3] + ("Rätin" if kopf[-3] == "R" else "rätin")
    elif kopf.endswith("anwalt"):
        kopf = kopf[:-6] + "anwältin"
    elif kopf.endswith("e"):
        kopf = kopf[:-1] + "in"
    else:
        kopf += "in"
    return f"{kopf} {rest}" if rest else kopf


def kuratiert() -> list[Zeile]:
    """The curated list of titles."""
    z: list[Zeile] = []

    def t(art: str, land: str, begriff: str, *abk: str) -> None:
        for a in abk or ("",):
            z.append((begriff, art, a, land))

    def mw(art: str, land: str, m: str, *abk: str, f: str | None = None,
           abk_f: tuple[str, ...] | None = None) -> None:
        """Masculine and feminine form with the same (or given) abbreviations."""
        t(art, land, m, *abk)
        t(art, land, f or weiblich(m), *(abk if abk_f is None else abk_f))

    a = "akademisch"
    # doctorates (Germany and in general)
    mw(a, "alle", "Doktor", "Dr.", f="Doktorin")
    t(a, "AT", "Doktorin", "Dr.in")
    t(a, "alle", "Doktoren", "Dres.")
    for begriff, abk, land in (
        ("Doktor der Medizin", "Dr. med.", "alle"),
        ("Doktor der Zahnmedizin", "Dr. med. dent.", "alle"),
        ("Doktor der Tiermedizin", "Dr. med. vet.", "alle"),
        ("Doktor der Naturwissenschaften", "Dr. rer. nat.", "alle"),
        ("Doktor der Philosophie", "Dr. phil.", "alle"),
        ("Doktor der Rechtswissenschaft", "Dr. iur.", "alle"),
        ("Doktor der Rechtswissenschaft", "Dr. jur.", "alle"),
        ("Doktor beider Rechte", "Dr. iur. utr.", "alle"),
        ("Doktor der Staatswissenschaften", "Dr. rer. pol.", "alle"),
        ("Doktor der Wirtschaftswissenschaften", "Dr. rer. oec.", "alle"),
        ("Doktor der Sozialwissenschaften", "Dr. rer. soc.", "alle"),
        ("Doktor der Theologie", "Dr. theol.", "alle"),
        ("Doktor der Ingenieurwissenschaften", "Dr.-Ing.", "DE"),
        ("Doktor der Agrarwissenschaften", "Dr. agr.", "alle"),
        ("Doktor der Agrarwissenschaften", "Dr. sc. agr.", "DE"),
        ("Doktor der Erziehungswissenschaften", "Dr. paed.", "alle"),
        ("Doktor der Humanbiologie", "Dr. rer. biol. hum.", "DE"),
        ("Doktor der Humanwissenschaften", "Dr. sc. hum.", "DE"),
        ("Doktor der Medizinwissenschaften", "Dr. rer. medic.", "DE"),
        ("Doktor der Gesundheitswissenschaften", "Dr. PH", "DE"),
        ("Doktor der Gesundheitswissenschaften", "Dr. P.H.", "DE"),
        ("Doktor der Pflegewissenschaft", "Dr. rer. cur.", "DE"),
        ("Doktor der Ernährungswissenschaften", "Dr. oec. troph.", "DE"),
        ("Doktor der Forstwissenschaften", "Dr. rer. silv.", "DE"),
        ("Doktor der Gartenbauwissenschaften", "Dr. rer. hort.", "DE"),
        ("Doktor der Sportwissenschaft", "Dr. Sportwiss.", "DE"),
        ("Doktor der Wissenschaften (Promotion B)", "Dr. sc.", "DE"),
        ("Doktor der Medizinwissenschaften (Promotion B)", "Dr. sc. med.", "DE"),
        ("Doktor der gesamten Heilkunde", "Dr. med. univ.", "AT"),
        ("Doktor der Zahnheilkunde", "Dr. med. dent.", "AT"),
        ("Doktor der medizinischen Wissenschaft", "Dr. scient. med.", "AT"),
        ("Doktor der technischen Wissenschaften", "Dr. techn.", "AT"),
        ("Doktor der montanistischen Wissenschaften", "Dr. mont.", "AT"),
        ("Doktor der Bodenkultur", "Dr. nat. techn.", "AT"),
        ("Doktor der Sozial- und Wirtschaftswissenschaften", "Dr. rer. soc. oec.", "AT"),
        ("Doktor der Naturwissenschaften", "Dr. sc. nat.", "CH"),
        ("Doktor der Naturwissenschaften", "Dr. phil. nat.", "CH"),
        ("Doktor der technischen Wissenschaften", "Dr. sc. techn.", "CH"),
        ("Doktor der Wissenschaften", "Dr. sc.", "CH"),
        ("Ehrendoktor", "Dr. h. c.", "alle"),
        ("Ehrendoktor", "Dr. h.c.", "alle"),
        ("Ehrendoktor der Ingenieurwissenschaften", "Dr.-Ing. E. h.", "DE"),
        ("mehrfacher Doktor", "Dr. mult.", "alle"),
        ("zweifacher Doktor", "DDr.", "AT"),
        ("zweifacher Magister", "MMag.", "AT"),
        ("habilitierter Doktor", "Dr. habil.", "alle"),
        ("habilitierter Doktor der Medizin", "Dr. med. habil.", "alle"),
        ("habilitierter Doktor der Naturwissenschaften", "Dr. rer. nat. habil.", "alle"),
        ("habilitierter Doktor der Philosophie", "Dr. phil. habil.", "alle"),
        ("habilitiert", "habil.", "alle"),
        ("Doctor of Philosophy", "Ph.D.", "alle"), ("Doctor of Philosophy", "PhD", "alle"),
        ("Doctor of Philosophy", "D.Phil.", "alle"),
        ("Doctor of Medicine", "M.D.", "alle"), ("Doctor of Medicine", "MD", "alle"),
        ("Doctor of Dental Surgery", "DDS", "alle"),
        ("Doctor of Dental Medicine", "DMD", "alle"),
        ("Doctor of Public Health", "DrPH", "alle"),
        ("Doctor of Business Administration", "DBA", "alle"),
        ("Doctor of Science", "D.Sc.", "alle"), ("Juris Doctor", "J.D.", "alle"),
        ("Doctor of Pharmacy", "Pharm.D.", "alle"),
    ):
        t(a, land, begriff, abk)
    # students and candidates
    mw(a, "alle", "Kandidat der Medizin", "cand. med.", f="Kandidatin der Medizin")
    mw(a, "alle", "Kandidat der Zahnmedizin", "cand. med. dent.",
       f="Kandidatin der Zahnmedizin")
    mw(a, "alle", "Kandidat der Philosophie", "cand. phil.", f="Kandidatin der Philosophie")
    mw(a, "alle", "Kandidat der Rechtswissenschaft", "cand. iur.",
       f="Kandidatin der Rechtswissenschaft")
    mw(a, "alle", "Kandidat der Psychologie", "cand. psych.", f="Kandidatin der Psychologie")
    mw(a, "alle", "Student der Medizin", "stud. med.", f="Studentin der Medizin")
    # diploma degrees (Germany, Austria, GDR)
    for m, abk in (
        ("Diplom-Ingenieur", "Dipl.-Ing."), ("Diplom-Psychologe", "Dipl.-Psych."),
        ("Diplom-Betriebswirt", "Dipl.-Betriebsw."), ("Diplom-Volkswirt", "Dipl.-Volksw."),
        ("Diplom-Ökonom", "Dipl.-Ök."), ("Diplom-Ökonom", "Dipl.-Oec."),
        ("Diplom-Informatiker", "Dipl.-Inf."), ("Diplom-Informatiker", "Dipl.-Inform."),
        ("Diplom-Physiker", "Dipl.-Phys."), ("Diplom-Chemiker", "Dipl.-Chem."),
        ("Diplom-Biologe", "Dipl.-Biol."), ("Diplom-Mathematiker", "Dipl.-Math."),
        ("Diplom-Pädagoge", "Dipl.-Päd."), ("Diplom-Sozialpädagoge", "Dipl.-Soz.-Päd."),
        ("Diplom-Sozialpädagoge", "Dipl.-Sozialpäd."),
        ("Diplom-Sozialarbeiter", "Dipl.-Soz.-Arb."), ("Diplom-Soziologe", "Dipl.-Soz."),
        ("Diplom-Politologe", "Dipl.-Pol."), ("Diplom-Geograph", "Dipl.-Geogr."),
        ("Diplom-Geologe", "Dipl.-Geol."), ("Diplom-Jurist", "Dipl.-Jur."),
        ("Diplom-Theologe", "Dipl.-Theol."), ("Diplom-Pflegewirt", "Dipl.-Pflegew."),
        ("Diplom-Pflegepädagoge", "Dipl.-Pflegepäd."),
        ("Diplom-Heilpädagoge", "Dipl.-Heilpäd."),
        ("Diplom-Sportwissenschaftler", "Dipl.-Sportwiss."),
        ("Diplom-Sportlehrer", "Dipl.-Sportl."), ("Diplom-Designer", "Dipl.-Des."),
        ("Diplom-Wirtschaftsingenieur", "Dipl.-Wirt.-Ing."),
        ("Diplom-Verwaltungswirt", "Dipl.-Verw.-Wirt"), ("Diplom-Finanzwirt", "Dipl.-Finw."),
        ("Diplom-Rechtspfleger", "Dipl.-Rpfl."), ("Diplom-Dolmetscher", "Dipl.-Dolm."),
        ("Diplom-Übersetzer", "Dipl.-Übers."), ("Diplom-Musiklehrer", ""),
        ("Diplom-Oecotrophologe", "Dipl. oec. troph."),
        ("Diplom-Ingenieur (FH)", "Dipl.-Ing. (FH)"),
        ("Diplom-Betriebswirt (FH)", "Dipl.-Betriebsw. (FH)"),
        ("Diplom-Sozialpädagoge (FH)", "Dipl.-Soz.-Päd. (FH)"),
    ):
        mw(a, "DE" if "(FH)" in m else "alle", m, abk)
    mw(a, "alle", "Diplom-Kaufmann", "Dipl.-Kfm.", abk_f=("Dipl.-Kffr.", "Dipl.-Kfr."))
    mw(a, "DE", "Diplom-Kaufmann (FH)", "Dipl.-Kfm. (FH)", abk_f=("Dipl.-Kffr. (FH)",))
    mw(a, "DE", "Diplom-Mediziner", "Dipl.-Med.")
    mw(a, "DE", "Diplom-Stomatologe", "Dipl.-Stom.")
    mw(a, "DE", "Diplomlehrer", "")
    t(a, "AT", "Diplom-Ingenieur", "DI")
    t(a, "AT", "Diplom-Ingenieurin", "DIin", "Dipl.-Ing.in")
    mw(a, "AT", "Ingenieur", "Ing.")
    t(a, "DE", "graduierter Ingenieur", "Ing. grad.")
    # bachelor and master
    for begriff, abk in (
        ("Bachelor of Arts", ("B.A.", "BA", "B. A.")),
        ("Bachelor of Science", ("B.Sc.", "BSc", "B. Sc.")),
        ("Bachelor of Engineering", ("B.Eng.", "B. Eng.")),
        ("Bachelor of Laws", ("LL.B.",)), ("Bachelor of Education", ("B.Ed.", "BEd")),
        ("Bachelor of Fine Arts", ("B.F.A.", "BFA")), ("Bachelor of Music", ("B.Mus.",)),
        ("Bachelor of Business Administration", ("BBA",)),
        ("Master of Arts", ("M.A.", "MA", "M. A.")),
        ("Master of Science", ("M.Sc.", "MSc", "M. Sc.")),
        ("Master of Engineering", ("M.Eng.", "M. Eng.")), ("Master of Laws", ("LL.M.",)),
        ("Master of Education", ("M.Ed.", "MEd")), ("Master of Fine Arts", ("M.F.A.", "MFA")),
        ("Master of Music", ("M.Mus.",)), ("Master of Business Administration", ("MBA",)),
        ("Master of Public Health", ("MPH",)), ("Master of Public Administration", ("MPA",)),
        ("Master of Advanced Studies", ("MAS",)), ("Master of Philosophy", ("M.Phil.",)),
        ("Master of Research", ("MRes",)), ("Master of Social Work", ("MSW",)),
        ("Master of Science in Nursing", ("MScN",)),
        ("Bachelor Professional", ("",)), ("Master Professional", ("",)),
    ):
        t(a, "alle", begriff, *abk)
    t(a, "DE", "Magister Artium", "M.A.")
    mw(a, "AT", "Bakkalaureus", "Bakk.", f="Bakkalaurea")
    # Austrian magister degrees
    t(a, "AT", "Magister", "Mag.")
    t(a, "AT", "Magistra", "Mag.", "Mag.a")
    for begriff, abk in (
        ("Magister der Philosophie", "Mag. phil."),
        ("Magister der Naturwissenschaften", "Mag. rer. nat."),
        ("Magister der Rechtswissenschaften", "Mag. iur."),
        ("Magister der Sozial- und Wirtschaftswissenschaften", "Mag. rer. soc. oec."),
        ("Magister der Pharmazie", "Mag. pharm."), ("Magister der Künste", "Mag. art."),
        ("Magister (FH)", "Mag. (FH)"),
    ):
        t(a, "AT", begriff, abk)
    t(a, "alle", "Magister der Theologie", "Mag. theol.")
    # Swiss degrees and titles
    for begriff, abk in (
        ("Lizentiat der Philosophie", "lic. phil."),
        ("Lizentiat der Rechtswissenschaft", "lic. iur."),
        ("Lizentiat der Wirtschaftswissenschaften", "lic. oec."),
        ("Lizentiat der Staatswissenschaften", "lic. rer. pol."),
        ("diplomierter Ingenieur ETH", "dipl. Ing. ETH"),
        ("diplomierter Architekt ETH", "dipl. Arch. ETH"),
        ("eidgenössisch diplomiert", "eidg. dipl."),
        ("eidgenössisch diplomierter Arzt", "med. pract."),
        ("Facharzttitel FMH", "FMH"),
    ):
        t(a, "CH", begriff, abk)
    t(a, "alle", "Lizentiat der Theologie", "lic. theol.")
    # academic positions
    mw(a, "alle", "Professor", "Prof.")
    t(a, "AT", "Professorin", "Prof.in")
    mw(a, "alle", "Universitätsprofessor", "Univ.-Prof.")
    t(a, "AT", "Universitätsprofessorin", "Univ.-Prof.in")
    mw(a, "AT", "außerordentlicher Universitätsprofessor", "ao. Univ.-Prof.",
       f="außerordentliche Universitätsprofessorin")
    mw(a, "AT", "ordentlicher Universitätsprofessor", "o. Univ.-Prof.",
       f="ordentliche Universitätsprofessorin")
    mw(a, "DE", "außerplanmäßiger Professor", "apl. Prof.",
       f="außerplanmäßige Professorin")
    mw(a, "alle", "Honorarprofessor", "Hon.-Prof.")
    mw(a, "DE", "Juniorprofessor", "Jun.-Prof.")
    t(a, "alle", "Professor emeritus", "Prof. em.")
    mw(a, "alle", "emeritierter Professor", "em. Prof.", f="emeritierte Professorin")
    mw(a, "CH", "Titularprofessor", "Tit.-Prof.")
    mw(a, "AT", "Assistenzprofessor", "Ass.-Prof.")
    mw(a, "AT", "assoziierter Professor", "Assoz. Prof.", f="assoziierte Professorin")
    mw(a, "alle", "Gastprofessor", "Gastprof.")
    mw(a, "alle", "Privatdozent", "PD", "Priv.-Doz.")
    mw(a, "AT", "Universitätsdozent", "Univ.-Doz.")
    mw(a, "alle", "Dozent", "Doz.")
    mw(a, "alle", "Rektor", "")
    mw(a, "alle", "Dekan", "")

    a = "adel"
    for m, f, abk, abk_f in (
        ("Kaiser", "Kaiserin", "", ""), ("König", "Königin", "", ""),
        ("Erzherzog", "Erzherzogin", "", ""), ("Großherzog", "Großherzogin", "", ""),
        ("Kurfürst", "Kurfürstin", "", ""), ("Herzog", "Herzogin", "", ""),
        ("Erbherzog", "Erbherzogin", "", ""), ("Fürst", "Fürstin", "", ""),
        ("Prinz", "Prinzessin", "", ""), ("Erbprinz", "Erbprinzessin", "", ""),
        ("Kronprinz", "Kronprinzessin", "", ""), ("Markgraf", "Markgräfin", "", ""),
        ("Landgraf", "Landgräfin", "", ""), ("Pfalzgraf", "Pfalzgräfin", "", ""),
        ("Burggraf", "Burggräfin", "", ""), ("Reichsgraf", "Reichsgräfin", "", ""),
        ("Altgraf", "Altgräfin", "", ""), ("Erbgraf", "Erbgräfin", "", ""),
        ("Graf", "Gräfin", "Gf.", "Gfn."), ("Freiherr", "Freifrau", "Frhr.", "Frfr."),
        ("Reichsfreiherr", "Reichsfreifrau", "", ""), ("Baron", "Baronin", "", ""),
        ("Edler", "Edle", "", ""), ("Ritter", "", "", ""), ("Junker", "", "", ""),
    ):
        t(a, "alle", m, abk)
        if f:
            t(a, "alle", f, abk_f)
    t(a, "alle", "Freiherr", "Frh.")
    for begriff in ("Freiin", "Baronesse", "Komtesse", "Prinzgemahl"):
        t(a, "alle", begriff)
    for begriff, abk in (("von", "v."), ("zu", "z."), ("von und zu", "v. u. z."),
                         ("vom", ""), ("von der", ""), ("zur", ""), ("zum", ""),
                         ("Edler von", "Edler v."), ("Ritter von", "Ritter v.")):
        t(a, "alle", begriff, abk)
    for begriff, abk in (("Majestät", ""), ("Seine Majestät", "S. M."),
                         ("Ihre Majestät", "I. M."), ("Hoheit", ""),
                         ("Königliche Hoheit", ""), ("Seine Königliche Hoheit", "S. K. H."),
                         ("Ihre Königliche Hoheit", "I. K. H."), ("Kaiserliche Hoheit", ""),
                         ("Durchlaucht", ""), ("Seine Durchlaucht", "S. D."),
                         ("Ihre Durchlaucht", "I. D."), ("Erlaucht", ""),
                         ("Hochgeboren", ""), ("Hochwohlgeboren", "")):
        t(a, "alle", begriff, abk)

    a = "kirchlich"
    for m, f, abk, abk_f in (
        ("Kardinal", "", "Kard.", ""), ("Erzbischof", "", "Erzb.", ""),
        ("Bischof", "Bischöfin", "", ""), ("Weihbischof", "", "", ""),
        ("Landesbischof", "Landesbischöfin", "", ""), ("Generalvikar", "", "", ""),
        ("Prälat", "", "", ""), ("Monsignore", "", "Msgr.", ""),
        ("Domkapitular", "", "", ""), ("Domherr", "", "", ""), ("Dompropst", "", "", ""),
        ("Domdekan", "", "", ""), ("Kanonikus", "", "", ""),
        ("Propst", "Pröpstin", "", ""), ("Dekan", "Dekanin", "", ""),
        ("Superintendent", "Superintendentin", "Sup.", "Sup."),
        ("Oberkirchenrat", "Oberkirchenrätin", "OKR", "OKR"),
        ("Kirchenrat", "Kirchenrätin", "KR", "KR"),
        ("Pfarrer", "Pfarrerin", "Pfr.", "Pfrn."), ("Pastor", "Pastorin", "", ""),
        ("Pastoralreferent", "Pastoralreferentin", "", ""),
        ("Gemeindereferent", "Gemeindereferentin", "", ""),
        ("Diakon", "Diakonin", "", ""), ("Ständiger Diakon", "", "", ""),
        ("Vikar", "Vikarin", "", ""), ("Kaplan", "", "", ""),
        ("Pater", "", "P.", ""), ("Frater", "", "Fr.", ""), ("Bruder", "", "Br.", ""),
        ("Schwester", "", "Sr.", ""), ("Abt", "Äbtissin", "", ""),
        ("Prior", "Priorin", "", ""), ("Oberin", "", "", ""), ("Mutter Oberin", "", "", ""),
        ("Diakonisse", "", "", ""), ("Rabbiner", "Rabbinerin", "", ""),
        ("Landesrabbiner", "Landesrabbinerin", "", ""), ("Imam", "", "", ""),
        ("Hochwürden", "", "Hw.", ""), ("Ehrwürden", "", "", ""),
    ):
        t(a, "alle", m, abk)
        if f:
            t(a, "alle", f, abk_f)
    t(a, "AT", "Kooperator", "Koop.")

    a = "amt"
    for m, abk, land in (
        ("Studienrat", "StR", "DE"), ("Oberstudienrat", "OStR", "DE"),
        ("Studiendirektor", "StD", "DE"), ("Oberstudiendirektor", "OStD", "DE"),
        ("Konrektor", "", "DE"), ("Schulrat", "", "DE"),
        ("Regierungsrat", "RR", "DE"), ("Oberregierungsrat", "ORR", "DE"),
        ("Regierungsdirektor", "RD", "DE"), ("Leitender Regierungsdirektor", "LtdRD", "DE"),
        ("Ministerialrat", "MinR", "DE"), ("Ministerialdirigent", "MinDirig", "DE"),
        ("Ministerialdirektor", "MinDir", "DE"), ("Staatssekretär", "StS", "DE"),
        ("Amtsrat", "AR", "DE"), ("Oberamtsrat", "OAR", "DE"),
        ("Medizinalrat", "MedR", "DE"), ("Obermedizinalrat", "OMedR", "DE"),
        ("Medizinaldirektor", "MedDir", "DE"),
        ("Leitender Medizinaldirektor", "LtdMedDir", "DE"),
        ("Veterinärrat", "VetR", "DE"), ("Veterinärdirektor", "VetDir", "DE"),
        ("Richter am Amtsgericht", "RiAG", "DE"), ("Richter am Landgericht", "RiLG", "DE"),
        ("Vorsitzender Richter am Landgericht", "VRiLG", "DE"),
        ("Richter am Oberlandesgericht", "RiOLG", "DE"),
        ("Staatsanwalt", "StA", "DE"), ("Oberstaatsanwalt", "OStA", "DE"),
        ("Leitender Oberstaatsanwalt", "LOStA", "DE"),
        ("Generalstaatsanwalt", "GStA", "DE"),
        ("Polizeimeister", "PM", "DE"), ("Polizeiobermeister", "POM", "DE"),
        ("Polizeihauptmeister", "PHM", "DE"), ("Polizeikommissar", "PK", "DE"),
        ("Polizeioberkommissar", "POK", "DE"), ("Polizeihauptkommissar", "PHK", "DE"),
        ("Kriminalkommissar", "KK", "DE"), ("Kriminaloberkommissar", "KOK", "DE"),
        ("Kriminalhauptkommissar", "KHK", "DE"),
        ("Bürgermeister", "Bgm.", "alle"), ("Oberbürgermeister", "OB", "DE"),
        ("Landrat", "", "DE"), ("Stadtrat", "", "alle"), ("Gemeinderat", "", "alle"),
        ("Minister", "", "alle"), ("Bundesminister", "", "alle"),
        ("Ministerpräsident", "", "DE"), ("Staatsminister", "", "DE"),
        ("Senator", "", "DE"), ("Bundeskanzler", "", "alle"),
        ("Bundespräsident", "", "alle"), ("Nationalrat", "", "AT"),
        ("Nationalrat", "", "CH"), ("Ständerat", "", "CH"), ("Bundesrat", "", "CH"),
        ("Landeshauptmann", "LH", "AT"), ("Bezirkshauptmann", "BH", "AT"),
        ("Botschafter", "", "alle"), ("Generalkonsul", "", "alle"),
        ("Konsul", "", "alle"), ("Honorarkonsul", "", "alle"),
    ):
        f = (m.replace("Leitender ", "Leitende ").replace("Vorsitzender ", "Vorsitzende ")
             if m.startswith(("Leitender ", "Vorsitzender ")) else m)
        if f.startswith(("Leitende ", "Vorsitzende ")):
            f = f.split(" ", 1)[0] + " " + weiblich(f.split(" ", 1)[1])
        else:
            f = weiblich(f)
        if m == "Landeshauptmann":
            f = "Landeshauptfrau"
        t(a, land, m, abk)
        t(a, land, f, abk)
    for begriff, abk, land in (
        ("Mitglied des Bundestages", "MdB", "DE"), ("Mitglied des Landtages", "MdL", "DE"),
        ("Mitglied des Europäischen Parlaments", "MdEP", "alle"),
        ("Mitglied des Abgeordnetenhauses", "MdA", "DE"),
        ("Mitglied der Hamburgischen Bürgerschaft", "MdHB", "DE"),
        ("Mitglied der Bremischen Bürgerschaft", "MdBB", "DE"),
        ("Abgeordneter zum Nationalrat", "Abg. z. NR", "AT"),
        ("Abgeordnete zum Nationalrat", "Abg. z. NR", "AT"),
        ("außer Dienst", "a. D.", "alle"), ("außer Dienst", "a.D.", "alle"),
        ("im Ruhestand", "i. R.", "alle"), ("im Ruhestand", "i.R.", "alle"),
        ("emeritus", "em.", "alle"),
        ("Gefreiter", "Gefr.", "DE"), ("Obergefreiter", "", "DE"),
        ("Hauptgefreiter", "", "DE"), ("Stabsgefreiter", "", "DE"),
        ("Unteroffizier", "Uffz.", "DE"), ("Stabsunteroffizier", "", "DE"),
        ("Feldwebel", "Fw.", "DE"), ("Oberfeldwebel", "", "DE"),
        ("Hauptfeldwebel", "", "DE"), ("Stabsfeldwebel", "", "DE"),
        ("Oberstabsfeldwebel", "", "DE"), ("Leutnant", "Lt.", "DE"),
        ("Oberleutnant", "", "DE"), ("Hauptmann", "Hptm.", "DE"), ("Major", "", "DE"),
        ("Oberstleutnant", "", "DE"), ("Oberst", "", "DE"),
        ("Brigadegeneral", "", "DE"), ("Generalmajor", "", "DE"),
        ("Generalleutnant", "", "DE"), ("General", "", "DE"),
        ("Kapitänleutnant", "", "DE"), ("Korvettenkapitän", "", "DE"),
        ("Fregattenkapitän", "", "DE"), ("Kapitän zur See", "", "DE"),
        ("Flottillenadmiral", "", "DE"), ("Konteradmiral", "", "DE"),
        ("Vizeadmiral", "", "DE"), ("Admiral", "", "DE"),
        ("Stabsarzt", "", "DE"), ("Oberstabsarzt", "", "DE"), ("Oberfeldarzt", "", "DE"),
        ("Oberstarzt", "", "DE"), ("Generalstabsarzt", "", "DE"), ("Generalarzt", "", "DE"),
        ("Exzellenz", "", "alle"), ("Seine Exzellenz", "S. E.", "alle"),
        ("Ihre Exzellenz", "I. E.", "alle"),
    ):
        t(a, land, begriff, abk)

    a = "ehren"
    for m, abk, land in (
        ("Sanitätsrat", "SanR", "DE"), ("Justizrat", "JR", "DE"),
        ("Geheimrat", "Geh. Rat", "DE"), ("Geheimer Sanitätsrat", "", "DE"),
        ("Geheimer Regierungsrat", "", "DE"), ("Kommerzienrat", "", "DE"),
        ("Kammersänger", "KS", "DE"), ("Kammersänger", "KS", "AT"),
        ("Kammerschauspieler", "", "DE"), ("Kammerschauspieler", "", "AT"),
        ("Kammervirtuose", "", "DE"), ("Kammermusiker", "", "DE"),
        ("Ehrenbürger", "", "alle"), ("Ehrensenator", "", "alle"),
        ("Hofrat", "HR", "AT"), ("Wirklicher Hofrat", "", "AT"),
        ("Regierungsrat", "RegR", "AT"), ("Kommerzialrat", "KommR", "AT"),
        ("Ökonomierat", "ÖkR", "AT"), ("Medizinalrat", "MR", "AT"),
        ("Obermedizinalrat", "OMR", "AT"), ("Veterinärrat", "VR", "AT"),
        ("Studienrat", "StR", "AT"), ("Oberstudienrat", "OStR", "AT"),
        ("Schulrat", "SR", "AT"), ("Oberschulrat", "OSR", "AT"),
        ("Technischer Rat", "TR", "AT"), ("Baurat h. c.", "BR h. c.", "AT"),
        ("Bergrat h. c.", "", "AT"), ("Forstrat h. c.", "", "AT"),
        ("Konsulent", "", "AT"), ("Professor", "Prof.", "AT"),
    ):
        if m.startswith(("Geheimer ", "Wirklicher ", "Technischer ")):
            erstes, rest = m.split(" ", 1)
            f = erstes[:-1] + " " + weiblich(rest)
        else:
            f = weiblich(m)
        t(a, land, m, abk)
        t(a, land, f, abk)
    t(a, "DE", "Professor h. c.", "Prof. h. c.")
    # examples of the GeMTeX annotation guideline, version 2 (7 September 2025, section 5.3,
    # doi:10.5281/zenodo.15747389) that the lists above did not have yet
    for begriff, abk, land in (
        ("Doktor der Medizinischen Wissenschaften", "Dr. rer. med.", "DE"),
        ("Doktor der Medizinischen Wissenschaften", "Dr. med. sci.", "DE"),
        ("Doktor der Medizinischen Wissenschaften", "Dr. rer. hum.", "DE"),
        ("Doktor der naturwissenschaftlichen Medizin", "Dr. nat. med.", "DE"),
        ("Doktor ehrenhalber (Ingenieurwissenschaften)", "Dr. E.h.", "DE"),
        ("Diplommedizinpädagoge", "Dipl.-Med. Päd.", "DE"),
        ("Diplommediziner", "Dipl. med.", "DE"),
        ("Bachelor of Science in Medical Sciences", "BScMed", "AT"),
        ("Bachelor of Medicine", "BMed", "CH"),
        ("Master of Medicine", "MMed", "CH"),
        ("Bachelor of Dental Medicine", "B Dent Med", "CH"),
        ("Master of Dental Medicine", "M Dent Med", "CH"),
        ("Master of Chiropractic Medicine", "M Chiro Med", "CH"),
        ("Doktor der Chiropraktischen Medizin", "Dr. med. chiro.", "CH"),
        ("Bachelor of Science in Nursing", "BSN", "CH"),
        ("Bachelor of Science in Nursing", "BScN", "CH"),
        ("Master of Science in Nursing", "MSN", "CH"),
        ("Medicinae universae doctor", "MUDr.", "alle"),
        ("Medicinae dentalis doctor", "MDDr.", "alle"),
        ("Candidatus scientiarum", "CSc", "alle"),
        ("Doctor scientiarum", "DrSc", "alle"),
        ("Doctor scientiarum", "DSc", "alle"),
    ):
        t("akademisch", land, begriff, abk)
    return z


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


def wikidata_grade(bekannt_abk: set[str]) -> list[Zeile]:
    """Academic degrees and abbreviations from Wikidata that the curated list lacks."""
    sparql = """SELECT DISTINCT ?item ?de ?kurz (LANG(?kurz) AS ?sprache) ?land WHERE {
  ?item (wdt:P31|wdt:P279)/wdt:P279* wd:Q189533 .
  ?item rdfs:label ?de . FILTER(LANG(?de) = "de")
  ?item wdt:P1813 ?kurz . FILTER(LANG(?kurz) IN ("de", "la"))
  OPTIONAL { ?item wdt:P17 ?land }
}"""
    namen: dict[str, str] = {}
    kurzformen: dict[str, set[str]] = collections.defaultdict(set)
    staaten: dict[str, set[str]] = collections.defaultdict(set)
    for z in abfrage(sparql):
        qid = z["item"].rsplit("/", 1)[-1]
        namen[qid] = z["de"].strip()
        kurz = z["kurz"].strip()
        if z["sprache"] == "de" or re.match(r"^(?:Dr\.|Mag\.|lic\.) ", kurz):
            kurzformen[qid].add(kurz)
        if z["land"]:
            staaten[qid].add(z["land"].rsplit("/", 1)[-1])
    zeilen: list[Zeile] = []
    for qid, begriff in sorted(namen.items()):
        laender = staaten[qid]
        if qid in WIKIDATA_AUSGESCHLOSSEN or (laender and not laender <= set(LAENDER)):
            continue
        land = LAENDER[next(iter(laender))] if len(laender) == 1 else "alle"
        for kurz in sorted(kurzformen[qid]):
            if (kurz in bekannt_abk or not kurz[:1].isupper()
                    or re.search(r"[A-Z][a-z]*Dr\.$", kurz)):
                continue                    # known, or Czech/Slovak forms such as "MVDr."
            zeilen.append((begriff, "akademisch", kurz, land))
    return zeilen


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("ziel", nargs="?", default=str(ZIEL))
    p.add_argument("--ohne-wikidata", action="store_true", help="curated list only")
    args = p.parse_args(argv)

    zeilen = kuratiert()
    wd: list[Zeile] = []
    if not args.ohne_wikidata:
        wd = wikidata_grade({a for _, _, a, _ in zeilen if a})
        print(f"Wikidata: {len(wd)} zusätzliche Grade: "
              + "; ".join(f"{b} ({a})" for b, _, a, _ in wd))
    eindeutig = sorted(set(zeilen) | set(wd),
                       key=lambda z: (ARTEN.index(z[1]), z[0].casefold(), z[3], z[2]))
    heute = dt.date.today().isoformat()
    kopf = ("# Quelle: kuratierte Liste AnonyMed (Bezeichnungen und gebräuchliche Abkürzungen"
            " als Fakten, ergänzt um die Titelbeispiele der GeMTeX-Annotationsleitlinie,"
            " 2. Version 2025, doi:10.5281/zenodo.15747389) und Wikidata (akademische Grade,"
            " Q189533, mit deutscher"
            " Kurzbezeichnung P1813); Lizenz: kuratierte Liste AGPL-3.0-or-later wie AnonyMed,"
            " Wikidata CC0 1.0; Stand: " + heute + "\n")
    ziel = Path(args.ziel)
    ziel.parent.mkdir(parents=True, exist_ok=True)
    # no file name and no time stamp in the gzip header: reproducible bytes
    with (open(ziel, "wb") as roh,
          gzip.GzipFile(filename="", fileobj=roh, mode="wb", mtime=0) as gz,
          io.TextIOWrapper(gz, encoding="utf-8", newline="") as f):
        f.write(kopf)
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["begriff", "klasse", "art", "abkuerzung", "land"])
        for begriff, art, abk, land in eindeutig:
            w.writerow([begriff, "Titel", art, abk, land])
    je_art = collections.Counter(art for _, art, _, _ in eindeutig)
    begriffe_je_art = collections.Counter(art for _, art in {(b, a) for b, a, _, _ in eindeutig})
    print(f"{len(eindeutig)} Zeilen, " + ", ".join(
        f"{art} {je_art[art]} ({begriffe_je_art[art]} Begriffe)" for art in ARTEN))
    print(f"geschrieben: {ziel} ({ziel.stat().st_size / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
