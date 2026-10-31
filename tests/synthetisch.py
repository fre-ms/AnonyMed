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


def verschrieben(name: str) -> str:
    """The name as a speech recogniser might write it: one letter dropped."""
    return name[:2] + name[3:] if len(name) >= 5 else name + "n"


def transkript_text(p: Patient) -> list[str]:
    """A consultation as a local transcriber would write it: the patient in many
    forms, once misspelt, plus medical terms that must survive."""
    geb = p.geburtsdatum
    assert geb is not None
    z = p.zuweiser[0]
    a = p.angehoerige[0]
    anrede = "Herr" if p.maennlich else "Frau"
    return [
        f"Arzt: Guten Tag, {anrede} {p.nachname}. Sie sind am {datum_lang(geb)} geboren?",
        f"Patient: Ja, am {geb.strftime('%d.%m.%Y')}. Ich wohne in der {p.strasse} in {p.ort}.",
        f"Arzt: {anrede} {verschrieben(p.nachname)}, was führt Sie zu mir?",
        f"Patient: Meine Frau, {a.vollname}, hat gemerkt, dass ich Wörter vergesse.",
        f"Arzt: Überwiesen hat Sie {z.titel} {z.nachname}. Nehmen Sie Medikamente?",
        "Patient: Ramipril und Atorvastatin. Arterielle Hypertonie habe ich seit zehn Jahren.",
        "Arzt: Der MoCA ergab 22 von 30 Punkten, das passt zu einer leichten kognitiven Störung.",
        f"Patient: Erreichen können Sie mich unter {p.telefone[0]} oder {p.emails[0]}.",
        f"Arzt: Danke, {p.vorname}. Wir sehen uns in sechs Monaten wieder.",
    ]


def erzeuge_transkript(pfad: Path, p: Patient, format_: str = "txt") -> Path:
    """Write the transcript as plain text, SRT or WebVTT (one cue per line)."""
    zeilen = transkript_text(p)
    if format_ == "txt":
        pfad.write_text("\n".join(zeilen) + "\n", encoding="utf-8")
    elif format_ == "md":
        pfad.write_text("# Gespräch\n\n" + "\n\n".join(f"- {z}" for z in zeilen) + "\n",
                        encoding="utf-8")
    elif format_ in ("srt", "vtt"):
        trenner = "," if format_ == "srt" else "."
        bloecke = ["WEBVTT", ""] if format_ == "vtt" else []
        for i, zeile in enumerate(zeilen, 1):
            von, bis = 4 * (i - 1), 4 * i - 1
            zeit = (f"00:00:{von:02d}{trenner}000 --> 00:00:{bis:02d}{trenner}500")
            bloecke += ([str(i)] if format_ == "srt" else []) + [zeit, zeile, ""]
        pfad.write_text("\n".join(bloecke), encoding="utf-8")
    else:
        raise ValueError(format_)
    return pfad


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


def _falte(zeile: str) -> str:
    """Fold at 75 octets as vCard writers do, to exercise unfolding."""
    roh = zeile.encode("utf-8")
    teile = []
    while len(roh) > 75:
        schnitt = 75
        while schnitt > 1 and (roh[schnitt] & 0xC0) == 0x80:
            schnitt -= 1
        teile.append(roh[:schnitt].decode("utf-8"))
        roh = b" " + roh[schnitt:]
    teile.append(roh.decode("utf-8"))
    return "\r\n".join(teile)


def _vcard_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")


def vcard_aus_patient(p: Patient, uid: str | None = None) -> str:
    """A card as DoctoNext writes it (vCard 4.0, salutation in N, GENDER, X fields)."""
    anrede = "Herr" if p.maennlich else "Frau" if p.weiblich else ""
    uid = uid or f"synthetisch-{abs(hash(p.schluessel())) % 10**8}"
    zeilen = [
        "BEGIN:VCARD",
        "VERSION:4.0",
        f"UID:{uid}",
        f"FN:{_vcard_escape(p.vollname)}",
        f"N:{_vcard_escape(p.nachname)};{_vcard_escape(p.vorname)};;{anrede};",
    ]
    if p.geburtsdatum:
        zeilen.append(f"BDAY:{p.geburtsdatum.strftime('%Y%m%d')}")
    if p.geschlecht:
        zeilen.append(f"GENDER:{'M' if p.maennlich else 'F'}")
    for i, t in enumerate(p.telefone):
        zeilen.append(f"TEL;TYPE={'cell' if i == 0 else 'voice'}:{t}")
    for e in p.emails:
        zeilen.append(f"EMAIL;TYPE=home:{e}")
    if p.strasse or p.ort:
        zeilen.append(f"ADR;TYPE=home:;;{_vcard_escape(p.strasse)};{_vcard_escape(p.ort)};;{p.plz};")
    if p.geburtsname:
        zeilen.append(f"X-MAIDENNAME:{_vcard_escape(p.geburtsname)}")
    if p.patienten_id:
        zeilen.append(f"X-DOCTOLIB-PATIENT-ID:{p.patienten_id}")
    notiz = []
    if p.versichertennummer:
        notiz.append(f"KVNR {p.versichertennummer}")
    if p.angehoerige:
        a = p.angehoerige[0]
        notiz.append(f"Ehepartner: {a.vollname}, erreichbar unter {a.telefone[0]}")
    if notiz:
        zeilen.append("NOTE:" + _vcard_escape(", ".join(notiz)))
    zeilen.append("X-DOCTOLIB-INSURANCE-SECTOR:private")
    zeilen.append("CATEGORIES:Doctolib,Privatpatient")
    zeilen.append("REV:20261030T120000Z")
    zeilen.append("END:VCARD")
    return "\r\n".join(_falte(z) for z in zeilen) + "\r\n"
