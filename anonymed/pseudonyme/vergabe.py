"""Assign pseudonyms for one patient and remember what they stand for.

The main person becomes "Patient K7Q4MX" (or "Patientin …"; with the letter
scheme "Patient A"), relatives "Angehörige K7Q4MX-1", referring physicians
"Dr. Z1"; other identifiers become bracketed placeholders such as
"[geb. 1990]" or "[Wohnort]".  The identifier is handed in by the store so
that it stays stable across documents, sessions and machines.
"""

from __future__ import annotations

import datetime as dt

from anonymed.kern.modell import Art, Fundstelle, Patient, Person

PLATZHALTER: dict[Art, str] = {
    Art.ADRESSE: "[Wohnort]",
    Art.ORT: "[Wohnort]",
    Art.TELEFON: "[Telefon]",
    Art.EMAIL: "[E-Mail]",
    Art.VERSICHERTENNUMMER: "[Versichertennummer]",
    Art.PATIENTEN_ID: "[Patienten-ID]",
    Art.AKTENZEICHEN: "[Aktenzeichen]",
    Art.IBAN: "[IBAN]",
}


class Pseudonymvergabe:
    """Pseudonyms for one patient plus the mapping back to clear data."""

    def __init__(
        self,
        patient: Patient,
        kennung: str,
        datumsform: str = "jahr",
        heute: dt.date | None = None,
    ) -> None:
        self.patient = patient
        self.kennung = kennung
        self.datumsform = datumsform
        self.heute = heute or dt.date.today()
        self.zuordnung: dict[str, str] = {}
        self.arten: dict[str, str] = {}

    # -- bookkeeping ---------------------------------------------------------------------------
    def _merke(self, pseudonym: str, klartext: str, art: Art) -> str:
        klartext = klartext.strip()
        if klartext:
            alt = self.zuordnung.get(pseudonym)
            if alt is None:
                self.zuordnung[pseudonym] = klartext
            elif klartext not in alt.split("; "):
                self.zuordnung[pseudonym] = f"{alt}; {klartext}"
        self.arten.setdefault(pseudonym, str(art))
        return pseudonym

    def registriere_treffer(self, fundstellen: list[Fundstelle]) -> None:
        """Record the clear text of generic hits (e-mail, insurance number, IBAN)."""
        for f in fundstellen:
            if f.pseudonym not in self.zuordnung or f.regel.startswith("muster."):
                self._merke(f.pseudonym, " ".join(f.text.split()), f.art)

    # -- the main person -----------------------------------------------------------------------
    def hauptperson(self) -> str:
        wort = "Patientin" if self.patient.weiblich else "Patient"
        return self._merke(f"{wort} {self.kennung}", self.patient.vollname, Art.NAME)

    def geburtsdatum(self) -> str:
        d = self.patient.geburtsdatum
        if d is None:
            return "[Geburtsdatum]"
        if self.datumsform == "alter":
            noch_nicht = (self.heute.month, self.heute.day) < (d.month, d.day)
            alter = self.heute.year - d.year - noch_nicht
            pseudonym = f"[Alter {alter}]"
        else:
            pseudonym = f"[geb. {d.year}]"
        return self._merke(pseudonym, d.strftime("%d.%m.%Y"), Art.GEBURTSDATUM)

    def adresse(self) -> str:
        p = self.patient
        klartext = ", ".join(t for t in (p.strasse, f"{p.plz} {p.ort}".strip()) if t)
        return self._merke(PLATZHALTER[Art.ADRESSE], klartext, Art.ADRESSE)

    def telefon(self) -> str:
        return self._merke(PLATZHALTER[Art.TELEFON], "; ".join(self.patient.telefone), Art.TELEFON)

    def email(self) -> str:
        return self._merke(PLATZHALTER[Art.EMAIL], "; ".join(self.patient.emails), Art.EMAIL)

    def versichertennummer(self) -> str:
        return self._merke(PLATZHALTER[Art.VERSICHERTENNUMMER], self.patient.versichertennummer,
                           Art.VERSICHERTENNUMMER)

    def patienten_id(self) -> str:
        return self._merke(PLATZHALTER[Art.PATIENTEN_ID], self.patient.patienten_id,
                           Art.PATIENTEN_ID)

    def aktenzeichen(self) -> str:
        return self._merke(PLATZHALTER[Art.AKTENZEICHEN], "; ".join(self.patient.aktenzeichen),
                           Art.AKTENZEICHEN)

    def generisch(self, art: Art) -> str:
        """Placeholder for a generic pattern hit; the clear text is recorded per hit."""
        return PLATZHALTER.get(art, f"[{art}]")

    # -- people around the patient -------------------------------------------------------------
    def angehoerige(self, nummer: int, person: Person) -> str:
        return self._merke(f"Angehörige {self.kennung}-{nummer}", person.vollname, Art.NAME)

    def zuweiser(self, nummer: int, person: Person) -> str:
        klartext = " ".join(t for t in (person.titel, person.vollname) if t)
        return self._merke(f"Dr. Z{nummer}", klartext, Art.NAME)

    def begriff(self, nummer: int, text: str) -> str:
        return self._merke(f"[Begriff {nummer}]", text, Art.BEGRIFF)
