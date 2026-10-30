"""Synthetic patients and documents for the tests.  Nothing here is real."""

from __future__ import annotations

import datetime as dt
import random
from pathlib import Path

import pymupdf
from faker import Faker

from anonymed.kern.modell import Patient, Person

MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September",
          "Oktober", "November", "Dezember"]


def kvnr(rng: random.Random) -> str:
    ziffern = "".join(rng.choice("0123456789") for _ in range(9))
    return rng.choice("ABCDEFGHKLMNPQRSTUVWXYZ") + ziffern


def synthetischer_patient(seed: int = 7, geschlecht: str = "m") -> Patient:
    fake = Faker("de_DE")
    Faker.seed(seed)
    rng = random.Random(seed)
    vorname = fake.first_name_male() if geschlecht == "m" else fake.first_name_female()
    nachname = fake.last_name()
    while nachname == vorname:
        nachname = fake.last_name()
    strasse = f"{fake.street_name()} {rng.randint(1, 120)}"
    geb = dt.date(rng.randint(1940, 2005), rng.randint(1, 12), rng.randint(1, 28))
    zuweiser = Person(nachname=fake.last_name(), vorname=fake.first_name(), titel="Dr. med.",
                      strasse=f"{fake.street_name()} {rng.randint(1, 50)}",
                      telefone=[fake.phone_number()])
    partner = Person(nachname=nachname, vorname=fake.first_name_female() if geschlecht == "m"
                     else fake.first_name_male(), telefone=[fake.phone_number()])
    return Patient(
        nachname=nachname,
        vorname=vorname,
        geschlecht=geschlecht,
        geburtsdatum=geb,
        strasse=strasse,
        plz=fake.postcode(),
        ort=fake.city(),
        telefone=[fake.phone_number()],
        emails=[f"{vorname}.{nachname}@example.org".lower().replace("ß", "ss")],
        versichertennummer=kvnr(rng),
        patienten_id=str(rng.randint(10_000_000, 99_999_999)),
        aktenzeichen=[f"AZ {rng.randint(2020, 2026)}/{rng.randint(100, 999)}"],
        angehoerige=[partner],
        zuweiser=[zuweiser],
    )


def datum_lang(d: dt.date) -> str:
    return f"{d.day}. {MONATE[d.month - 1]} {d.year}"


def befund_text(p: Patient) -> list[list[str]]:
    """Two pages of a plausible neurological report mentioning the patient in many forms."""
    geb = p.geburtsdatum
    assert geb is not None
    z = p.zuweiser[0]
    a = p.angehoerige[0]
    anrede = "Herrn" if p.maennlich else "Frau"
    seite1 = [
        "Neurologische Gemeinschaftspraxis am Stadtpark",
        "Dr. med. Erika Beispiel · Fachärztin für Neurologie",
        f"Praxis {z.titel} {z.vollname}",
        f"{z.strasse}",
        "",
        f"Betr.: {anrede} {p.vollname}, geb. {geb.strftime('%d.%m.%Y')}",
        f"{p.strasse}, {p.plz} {p.ort}",
        f"Tel. {p.telefone[0]}, E-Mail {p.emails[0]}",
        f"Versicherten-Nr. {p.versichertennummer}   Pat.-ID {p.patienten_id}   {p.aktenzeichen[0]}",
        "",
        f"Sehr geehrte Frau Kollegin {z.nachname},",
        "",
        f"vielen Dank für die Überweisung von {anrede} {p.nachname}, der sich am 12.03.2026",
        "erstmals in unserer Sprechstunde vorstellte. Die Anamnese wurde gemeinsam mit",
        f"der Ehefrau, Frau {a.vollname}, erhoben.",
        "",
        "Diagnosen: Leichte kognitive Störung (ICD-10 F06.7), arterielle Hypertonie.",
        "Medikation: Ramipril 5 mg 1-0-0, Atorvastatin 20 mg 0-0-1, max. 3 Tabletten.",
        "Befund: MMST 26/30, MoCA 22/30, Uhrentest unauffällig.",
    ]
    seite2 = [
        f"Fortsetzung Befund {p.nachname.upper()}, {p.vorname.upper()}"
        f" (*{geb.strftime('%d.%m.%y')})",
        "",
        f"{p.vorname}s Beschwerden bestehen seit etwa einem Jahr. Geboren am {datum_lang(geb)}",
        f"in {p.ort}. {p.nachname}, {p.vorname} berichtet über Wortfindungsstörungen.",
        f"Im Verlauf zeigt {anrede.replace('Herrn', 'Herr')} {p.vorname[0]}. {p.nachname}",
        "eine stabile Befundlage. Kontrolle in 6 Monaten empfohlen.",
        "",
        "Mit freundlichen kollegialen Grüßen",
        "Dr. med. Erika Beispiel",
    ]
    return [seite1, seite2]


def erzeuge_befund(pfad: Path, p: Patient, mit_metadaten: bool = True) -> Path:
    doc = pymupdf.open()
    for zeilen in befund_text(p):
        page = doc.new_page()
        y = 72.0
        for zeile in zeilen:
            page.insert_text((56, y), zeile, fontsize=10.5, fontname="helv")
            y += 14.5
    if mit_metadaten:
        doc.set_metadata({"title": f"Befund {p.vollname}", "author": "Dr. Erika Beispiel",
                          "subject": p.vollname, "keywords": p.versichertennummer})
        doc.set_toc([[1, f"Befund {p.nachname}", 1]])
        doc[0].add_text_annot((400, 60), f"Rückfrage {p.nachname}")
        doc.embfile_add("notiz.txt", f"Notiz zu {p.vollname}".encode())
    doc.save(pfad, garbage=0)
    doc.close()
    return pfad
