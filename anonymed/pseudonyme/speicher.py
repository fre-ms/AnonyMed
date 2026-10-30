"""SQLite store for the pseudonym key.  Lives in the user's data folder and
never leaves the machine."""

from __future__ import annotations

import datetime as dt
import os
import sqlite3
from pathlib import Path
from typing import Any

from platformdirs import user_data_path

from anonymed.kern.modell import Patient
from anonymed.pseudonyme.vergabe import Pseudonymvergabe

_SCHEMA = """
CREATE TABLE IF NOT EXISTS patienten (
    schluessel TEXT PRIMARY KEY,
    buchstabe  TEXT UNIQUE NOT NULL,
    nachname   TEXT NOT NULL DEFAULT '',
    vorname    TEXT NOT NULL DEFAULT '',
    geschlecht TEXT NOT NULL DEFAULT '',
    titel      TEXT NOT NULL DEFAULT '',
    angelegt   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS zuordnungen (
    schluessel TEXT NOT NULL,
    pseudonym  TEXT NOT NULL,
    klartext   TEXT NOT NULL,
    art        TEXT NOT NULL DEFAULT '',
    geaendert  TEXT NOT NULL,
    PRIMARY KEY (schluessel, pseudonym)
);
"""


def standardpfad() -> Path:
    return user_data_path("AnonyMed", appauthor=False) / "pseudonyme.sqlite"


def buchstabe(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA (spreadsheet style)."""
    s = ""
    n = index + 1
    while n:
        n, rest = divmod(n - 1, 26)
        s = chr(65 + rest) + s
    return s


def _jetzt() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


class Pseudonymspeicher:
    """Letter per patient and pseudonym -> clear text per patient."""

    def __init__(self, pfad: str | Path | None = None) -> None:
        if pfad is None:
            pfad = standardpfad()
        self.pfad = Path(pfad) if str(pfad) != ":memory:" else None
        if self.pfad is not None:
            self.pfad.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(self.pfad.parent, 0o700)
            except OSError:
                pass
        self.verbindung = sqlite3.connect(str(self.pfad) if self.pfad else ":memory:")
        self.verbindung.row_factory = sqlite3.Row
        self.verbindung.executescript(_SCHEMA)
        if self.pfad is not None:
            try:
                os.chmod(self.pfad, 0o600)
            except OSError:
                pass

    def close(self) -> None:
        self.verbindung.close()

    def __enter__(self) -> Pseudonymspeicher:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- letters -------------------------------------------------------------------------------
    def buchstabe_fuer(self, patient: Patient) -> str:
        """Existing letter of this patient, or the next free one."""
        schluessel = patient.schluessel()
        zeile = self.verbindung.execute(
            "SELECT buchstabe FROM patienten WHERE schluessel = ?", (schluessel,)
        ).fetchone()
        if zeile:
            return zeile["buchstabe"]
        zeilen = self.verbindung.execute("SELECT buchstabe FROM patienten")
        vergeben = {r["buchstabe"] for r in zeilen}
        i = 0
        while buchstabe(i) in vergeben:
            i += 1
        neu = buchstabe(i)
        self.verbindung.execute(
            "INSERT INTO patienten"
            " (schluessel, buchstabe, nachname, vorname, geschlecht, titel, angelegt)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (schluessel, neu, patient.nachname, patient.vorname, patient.geschlecht, patient.titel,
             _jetzt()),
        )
        self.verbindung.commit()
        return neu

    # -- mappings ------------------------------------------------------------------------------
    def merke(self, patient: Patient, vergabe: Pseudonymvergabe) -> None:
        schluessel = patient.schluessel()
        jetzt = _jetzt()
        for pseudonym, klartext in vergabe.zuordnung.items():
            self.verbindung.execute(
                "INSERT INTO zuordnungen (schluessel, pseudonym, klartext, art, geaendert)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(schluessel, pseudonym) DO UPDATE SET"
                " klartext = excluded.klartext, art = excluded.art, geaendert = excluded.geaendert",
                (schluessel, pseudonym, klartext, vergabe.arten.get(pseudonym, ""), jetzt),
            )
        self.verbindung.commit()

    def zuordnung(self, schluessel: str) -> dict[str, str]:
        return {
            r["pseudonym"]: r["klartext"]
            for r in self.verbindung.execute(
                "SELECT pseudonym, klartext FROM zuordnungen WHERE schluessel = ?", (schluessel,)
            )
        }

    def patient_info(self, schluessel: str) -> dict[str, Any] | None:
        r = self.verbindung.execute(
            "SELECT * FROM patienten WHERE schluessel = ?", (schluessel,)
        ).fetchone()
        return dict(r) if r else None

    def finde(
        self, buchstabe_: str | None = None, suche: str | None = None
    ) -> list[dict[str, Any]]:
        """Patients by letter or by a substring of their name (case-insensitive)."""
        if buchstabe_:
            zeilen = self.verbindung.execute(
                "SELECT * FROM patienten WHERE buchstabe = ?", (buchstabe_.upper(),)
            )
        elif suche:
            muster = f"%{suche.casefold()}%"
            zeilen = self.verbindung.execute(
                "SELECT * FROM patienten WHERE lower(nachname) LIKE ? OR lower(vorname) LIKE ?"
                " ORDER BY angelegt",
                (muster, muster),
            )
        else:
            zeilen = self.verbindung.execute("SELECT * FROM patienten ORDER BY angelegt")
        return [dict(r) for r in zeilen]
