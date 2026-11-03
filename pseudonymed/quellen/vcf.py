"""Minimal vCard 3.0/4.0 reader for the fields that identify a patient.

Handles line unfolding, escaping, parameters (quoted, multi-valued, bare
vCard 2.1 types), property groups and quoted-printable values.  Field mapping
follows what DoctoNext writes (salutation as honorific prefix in N, legal
gender in GENDER, X-MAIDENNAME, X-DOCTOLIB-PATIENT-ID) and works for hand-made
cards as well.
"""

from __future__ import annotations

import datetime as dt
import quopri
import re
from dataclasses import dataclass, field
from pathlib import Path

from pseudonymed.kern.modell import Patient, parse_datum

_KVNR = re.compile(r"(?<![A-Z0-9])[A-Z]\d{9}(?!\d)")
_ANREDE_M = {"herr", "herrn", "hr.", "hr", "mr", "mr.", "sr."}
_ANREDE_W = {"frau", "fr.", "fr", "mrs", "mrs.", "ms", "ms.", "sra."}


@dataclass
class Eigenschaft:
    name: str
    params: dict[str, list[str]] = field(default_factory=dict)
    wert: str = ""
    gruppe: str = ""

    @property
    def typen(self) -> set[str]:
        return {t.lower() for t in self.params.get("TYPE", [])}

    @property
    def text(self) -> str:
        return entschluessele(self.wert)

    def teile(self) -> list[str]:
        return [entschluessele(t) for t in _trenne(self.wert, ";")]


@dataclass
class VCard:
    eigenschaften: list[Eigenschaft] = field(default_factory=list)

    def alle(self, name: str) -> list[Eigenschaft]:
        name = name.upper()
        return [e for e in self.eigenschaften if e.name == name]

    def erste(self, name: str) -> Eigenschaft | None:
        treffer = self.alle(name)
        return treffer[0] if treffer else None

    def wert(self, name: str, standard: str = "") -> str:
        e = self.erste(name)
        return e.text.strip() if e else standard

    @property
    def uid(self) -> str:
        return self.wert("UID")

    @property
    def fn(self) -> str:
        return self.wert("FN")

    @property
    def version(self) -> str:
        return self.wert("VERSION")


def entschluessele(wert: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(wert):
        c = wert[i]
        if c == "\\" and i + 1 < len(wert):
            n = wert[i + 1]
            out.append("\n" if n in "nN" else n)
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _trenne(wert: str, sep: str) -> list[str]:
    """Split on ``sep`` unless it is escaped with a backslash."""
    teile: list[str] = []
    aktuell: list[str] = []
    i = 0
    while i < len(wert):
        c = wert[i]
        if c == "\\" and i + 1 < len(wert):
            aktuell.append(wert[i : i + 2])
            i += 2
            continue
        if c == sep:
            teile.append("".join(aktuell))
            aktuell = []
        else:
            aktuell.append(c)
        i += 1
    teile.append("".join(aktuell))
    return teile


def _entfalte(text: str) -> list[str]:
    zeilen: list[str] = []
    for roh in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if roh[:1] in (" ", "\t") and zeilen:
            zeilen[-1] += roh[1:]
        elif roh.strip():
            zeilen.append(roh)
    return zeilen


def _kopf_und_wert(zeile: str) -> tuple[str, str]:
    in_anfuehrung = False
    for i, c in enumerate(zeile):
        if c == '"':
            in_anfuehrung = not in_anfuehrung
        elif c == ":" and not in_anfuehrung:
            return zeile[:i], zeile[i + 1 :]
    return zeile, ""


def _param_tokens(kopf: str) -> list[str]:
    tokens: list[str] = []
    aktuell: list[str] = []
    in_anfuehrung = False
    for c in kopf:
        if c == '"':
            in_anfuehrung = not in_anfuehrung
        if c == ";" and not in_anfuehrung:
            tokens.append("".join(aktuell))
            aktuell = []
        else:
            aktuell.append(c)
    tokens.append("".join(aktuell))
    return tokens


def _eigenschaft(zeile: str) -> Eigenschaft | None:
    kopf, wert = _kopf_und_wert(zeile)
    tokens = _param_tokens(kopf)
    name = tokens[0].strip()
    if not name:
        return None
    gruppe = ""
    if "." in name:
        gruppe, name = name.rsplit(".", 1)
    params: dict[str, list[str]] = {}
    for token in tokens[1:]:
        if "=" in token:
            k, v = token.split("=", 1)
            werte = [w.strip().strip('"') for w in v.split(",")]
        else:
            k, werte = "TYPE", [token.strip().strip('"')]
        params.setdefault(k.strip().upper(), []).extend(w for w in werte if w)
    if any(e.upper() == "QUOTED-PRINTABLE" for e in params.get("ENCODING", [])):
        zeichensatz = (params.get("CHARSET") or ["utf-8"])[0]
        try:
            wert = quopri.decodestring(wert.encode("latin-1")).decode(zeichensatz, errors="replace")
        except (LookupError, ValueError):
            pass
    return Eigenschaft(name=name.upper(), params=params, wert=wert, gruppe=gruppe)


def parse_vcards(text: str) -> list[VCard]:
    karten: list[VCard] = []
    aktuelle: VCard | None = None
    for zeile in _entfalte(text):
        e = _eigenschaft(zeile)
        if e is None:
            continue
        if e.name == "BEGIN" and e.wert.strip().upper() == "VCARD":
            aktuelle = VCard()
        elif e.name == "END" and e.wert.strip().upper() == "VCARD":
            if aktuelle is not None:
                karten.append(aktuelle)
            aktuelle = None
        elif aktuelle is not None:
            aktuelle.eigenschaften.append(e)
    return karten


def lade_vcf(pfad: str | Path) -> list[VCard]:
    roh = Path(pfad).read_bytes()
    try:
        text = roh.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = roh.decode("latin-1")
    return parse_vcards(text)


def parse_bday(wert: str) -> dt.date | None:
    wert = wert.strip()
    if not wert or wert.startswith("--"):
        return None  # no year: useless as an identifier
    wert = wert.split("T")[0]
    return parse_datum(wert)


def _telefon(wert: str) -> str:
    wert = wert.strip()
    if wert.lower().startswith("tel:"):
        wert = wert[4:]
    return wert


def _adresse(card: VCard) -> tuple[str, str, str]:
    adressen = card.alle("ADR")
    if not adressen:
        return "", "", ""
    gewaehlt = next((a for a in adressen if "home" in a.typen), adressen[0])
    teile = gewaehlt.teile() + [""] * 7
    strasse = teile[2].split("\n")[0].strip()
    if not strasse and teile[1].strip():
        strasse = teile[1].split("\n")[0].strip()
    return strasse, teile[5].strip(), teile[3].strip()


def patient_aus_vcard(card: VCard) -> Patient:
    """Derive the identifiers of one card; unknown fields are ignored."""
    n = card.erste("N")
    teile = (n.teile() + [""] * 5) if n else [""] * 5
    nachname, vorname, weitere, prefix, suffix = (t.strip() for t in teile[:5])
    if not nachname and not vorname:
        fn_teile = card.fn.split()
        if fn_teile:
            nachname, vorname = fn_teile[-1], " ".join(fn_teile[:-1])
    if weitere:
        vorname = f"{vorname} {weitere}".strip()

    geschlecht, titel_teile = "", []
    for token in re.split(r"[\s,]+", prefix):
        if not token:
            continue
        if token.lower() in _ANREDE_M:
            geschlecht = "m"
        elif token.lower() in _ANREDE_W:
            geschlecht = "w"
        else:
            titel_teile.append(token)
    gender = card.wert("GENDER").split(";")[0].strip().upper()
    if not geschlecht and gender in ("M", "F"):
        geschlecht = "m" if gender == "M" else "w"

    strasse, plz, ort = _adresse(card)
    telefone: list[str] = []
    for t in card.alle("TEL"):
        nummer = _telefon(t.text)
        if len(re.sub(r"\D", "", nummer)) >= 5 and nummer not in telefone:
            telefone.append(nummer)
    emails: list[str] = []
    for e in card.alle("EMAIL"):
        adresse = e.text.strip().removeprefix("mailto:").lower()
        if "@" in adresse and adresse not in emails:
            emails.append(adresse)

    hinweise = [h.text.strip() for h in card.alle("NOTE") if h.text.strip()]
    rolle = card.wert("ROLE")
    if rolle:
        hinweise.append(f"Beruf: {rolle}")
    versichertennummer = ""
    for name in ("X-VERSICHERTENNUMMER", "X-KVNR", "X-INSURANCE-NUMBER"):
        if card.wert(name):
            versichertennummer = card.wert(name)
            break
    if not versichertennummer:
        for h in hinweise:
            m = _KVNR.search(h)
            if m:
                versichertennummer = m.group(0)
                break

    return Patient(
        nachname=nachname,
        vorname=vorname,
        geschlecht=geschlecht,
        titel=" ".join(titel_teile),
        geburtsname=card.wert("X-MAIDENNAME"),
        geburtsdatum=parse_bday(card.wert("BDAY")),
        strasse=strasse,
        plz=plz,
        ort=ort,
        telefone=telefone,
        emails=emails,
        versichertennummer=versichertennummer,
        patienten_id=card.wert("X-DOCTOLIB-PATIENT-ID"),
        hinweise=hinweise,
    )


def patienten_aus_vcf(pfad: str | Path) -> list[Patient]:
    return [patient_aus_vcard(k) for k in lade_vcf(pfad)]
