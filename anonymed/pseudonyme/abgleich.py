"""Error-tolerant record linkage of a patient against the pseudonym store.

A weighted field comparison in the manner of EpiLink (Contiero et al. 2005),
which the Mainzelliste uses: every field contributes a similarity between 0
and 1, weighted by how identifying the field is; the score is the weighted
mean over the fields present on both sides.  Names are compared with the
Dice coefficient over character bigrams, component-wise for compound names
and with swapped first/last names tried as well; equal Kölner Phonetik codes
count as a strong similarity.  Date of birth is compared per component so
that a typo in the day does not break the match.

Scores at or above ``SCHWELLE_GLEICH`` are treated as the same person,
scores between the two thresholds are "uncertain" and need a human decision,
anything below is a different person.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Any

from anonymed.kern.modell import Patient, normalisiere
from anonymed.pseudonyme.phonetik import koelner_phonetik

# EpiLink weights w = log2((1 - errorRate) / frequency) from the Mainzelliste defaults
# (config/mainzelliste.conf.default: vorname f=0.000235 e=0.01, nachname f=0.0000271 e=0.008,
# geburtstag f=0.0333 e=0.005, geburtsmonat f=0.0833 e=0.002, geburtsjahr f=0.0286 e=0.004).
GEWICHTE = {
    "nachname": 15.2,
    "vorname": 12.0,
    "geburtsjahr": 5.1,
    "geburtsmonat": 3.6,
    "geburtstag": 4.9,
}
SCHWELLE_GLEICH = 0.95  # Mainzelliste threshold_match
SCHWELLE_UNSICHER = 0.80  # Mainzelliste uses 0.6; raised so that mere namesakes do not ask
PHONETISCH_GLEICH = 0.9


@dataclass
class Vergleich:
    kennung: str
    schluessel: str
    score: float
    felder: dict[str, float] = field(default_factory=dict)
    nachname: str = ""
    vorname: str = ""
    geburtsdatum: str = ""

    @property
    def gleich(self) -> bool:
        return self.score >= SCHWELLE_GLEICH

    @property
    def unsicher(self) -> bool:
        return SCHWELLE_UNSICHER <= self.score < SCHWELLE_GLEICH


def _bigramme(text: str) -> list[str]:
    t = f" {text} "
    return [t[i : i + 2] for i in range(len(t) - 1)]


_TRANS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def _formen(text: str) -> set[str]:
    """Accent-stripped and transliterated normal forms (Müller -> muller, mueller)."""
    return {normalisiere(text), normalisiere(text.lower().translate(_TRANS))} - {""}


def _dice_roh(a: str, b: str) -> float:
    if a == b:
        return 1.0
    ba, bb = _bigramme(a), _bigramme(b)
    gemeinsam = 0
    rest = list(bb)
    for g in ba:
        if g in rest:
            rest.remove(g)
            gemeinsam += 1
    return 2 * gemeinsam / (len(ba) + len(bb))


def dice(a: str, b: str) -> float:
    """Dice coefficient over character bigrams, best over the normal forms of both."""
    fa, fb = _formen(a), _formen(b)
    if not fa or not fb:
        return 0.0
    return max(_dice_roh(x, y) for x in fa for y in fb)


def _teile(name: str) -> list[str]:
    return [t for t in re.split(r"[\s\-‐‑–,]+", name.strip()) if t]


def namensaehnlichkeit(a: str, b: str) -> float:
    """Best of: whole strings, any pair of components; phonetic equality counts 0.9."""
    if not a.strip() or not b.strip():
        return 0.0
    kandidaten = [dice(a, b)]
    ta, tb = _teile(a), _teile(b)
    for x in ta:
        for y in tb:
            s = dice(x, y)
            if len(x) >= 3 and len(y) >= 3 and koelner_phonetik(x) == koelner_phonetik(y):
                s = max(s, PHONETISCH_GLEICH)
            kandidaten.append(s)
    if len(ta) > 1 and len(tb) > 1:
        kandidaten.append(dice("".join(ta), "".join(tb)))
    return max(kandidaten)


def _datum(wert: Any) -> dt.date | None:
    if isinstance(wert, dt.date):
        return wert
    if isinstance(wert, str) and wert:
        try:
            return dt.date.fromisoformat(wert[:10])
        except ValueError:
            return None
    return None


def vergleiche(patient: Patient, kandidat: dict[str, Any]) -> Vergleich:
    """Score ``patient`` against one stored row (keys nachname, vorname, geburtsname,
    geburtsdatum, kennung, schluessel)."""
    k_nach = str(kandidat.get("nachname", "") or "")
    k_vor = str(kandidat.get("vorname", "") or "")
    k_geb = str(kandidat.get("geburtsname", "") or "")
    felder: dict[str, float] = {}

    nach = namensaehnlichkeit(patient.nachname, k_nach)
    if patient.geburtsname:
        nach = max(nach, namensaehnlichkeit(patient.geburtsname, k_nach))
    if k_geb:
        nach = max(nach, namensaehnlichkeit(patient.nachname, k_geb))
    vor = namensaehnlichkeit(patient.vorname, k_vor)
    getauscht = min(namensaehnlichkeit(patient.nachname, k_vor),
                    namensaehnlichkeit(patient.vorname, k_nach))
    if getauscht > (nach + vor) / 2:
        nach = vor = getauscht
    if patient.nachname and k_nach:
        felder["nachname"] = nach
    if patient.vorname and k_vor:
        felder["vorname"] = vor

    d1, d2 = patient.geburtsdatum, _datum(kandidat.get("geburtsdatum"))
    if d1 and d2:
        felder["geburtsjahr"] = 1.0 if d1.year == d2.year else 0.0
        felder["geburtsmonat"] = 1.0 if d1.month == d2.month else 0.0
        felder["geburtstag"] = 1.0 if d1.day == d2.day else 0.0
        # day and month swapped (01.02. vs 02.01.) is a common slip
        if felder["geburtsmonat"] == 0 and felder["geburtstag"] == 0 \
                and d1.day == d2.month and d1.month == d2.day:
            felder["geburtsmonat"] = felder["geburtstag"] = 0.5

    summe = sum(GEWICHTE[f] for f in felder)
    score = sum(GEWICHTE[f] * s for f, s in felder.items()) / summe if summe else 0.0
    return Vergleich(
        kennung=str(kandidat.get("kennung", "")),
        schluessel=str(kandidat.get("schluessel", "")),
        score=round(score, 4),
        felder=felder,
        nachname=k_nach,
        vorname=k_vor,
        geburtsdatum=d2.isoformat() if d2 else "",
    )


def abgleichen(patient: Patient, kandidaten: list[dict[str, Any]]) -> list[Vergleich]:
    """All candidates scored, best first; only scores above the lower threshold."""
    ergebnisse = [vergleiche(patient, k) for k in kandidaten]
    return sorted((v for v in ergebnisse if v.score >= SCHWELLE_UNSICHER),
                  key=lambda v: -v.score)
