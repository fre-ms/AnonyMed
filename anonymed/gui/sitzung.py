"""State of one desktop session, kept free of Qt so that it can be tested on its own."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from anonymed.ablauf import (
    Ablaufoptionen,
    Ergebnis,
    Textvorbereitung,
    Vorbereitung,
    abschliessen,
    vorbereiten,
)
from anonymed.dokument.textdatei import TEXTENDUNGEN
from anonymed.kern.modell import Patient
from anonymed.pseudonyme.speicher import Pseudonymspeicher, UnsichererAbgleich


@dataclass
class Dokument:
    pfad: Path
    vorbereitung: Vorbereitung | Textvorbereitung | None = None
    ergebnis: Ergebnis | None = None
    fehler: str = ""

    @property
    def zustand(self) -> str:
        if self.fehler:
            return f"Fehler: {self.fehler}"
        if self.ergebnis is not None:
            return "freigegeben" if self.ergebnis.freigegeben else "nicht freigegeben"
        if self.vorbereitung is not None:
            v = self.vorbereitung
            if v.ist_text:
                return f"Text, {len(v.fundstellen)} Treffer"
            ocr = f", OCR auf Seite(n) {', '.join(map(str, v.ocr_seiten))}" if v.ocr_seiten else ""
            return f"{len(v.seiten)} Seite(n), {len(v.fundstellen)} Treffer{ocr}"
        return "noch nicht analysiert"


@dataclass
class Sitzung:
    speicherpfad: Path | None = None
    schema: str = "hash"
    patient: Patient | None = None
    optionen: Ablaufoptionen = field(default_factory=Ablaufoptionen)
    dokumente: list[Dokument] = field(default_factory=list)
    abgleich: str | None = None  # decision for an uncertain match, set by the dialog

    def speicher(self) -> Pseudonymspeicher:
        return Pseudonymspeicher(self.speicherpfad, schema=self.schema)

    def fuege_hinzu(self, pfad: str | Path) -> Dokument | None:
        pfad = Path(pfad)
        if pfad.suffix.lower() not in {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp",
                                       ".heic", ".heif", ".webp", *TEXTENDUNGEN}:
            return None
        if any(d.pfad == pfad for d in self.dokumente):
            return None
        d = Dokument(pfad)
        self.dokumente.append(d)
        return d

    def entferne(self, d: Dokument) -> None:
        if d.vorbereitung is not None and not d.vorbereitung.abgeschlossen:
            d.vorbereitung.schliessen()
        self.dokumente.remove(d)

    def analysiere(self, d: Dokument) -> None:
        """Phase one for one document; raises UnsichererAbgleich for the dialog."""
        if self.patient is None:
            raise ValueError("Kein Patient gewählt.")
        if d.vorbereitung is not None and not d.vorbereitung.abgeschlossen:
            d.vorbereitung.schliessen()
        d.vorbereitung, d.ergebnis, d.fehler = None, None, ""
        opt = Ablaufoptionen(**{**self.optionen.__dict__, "abgleich": self.abgleich})
        speicher = self.speicher()
        try:
            d.vorbereitung = vorbereiten(d.pfad, self.patient, opt, speicher=speicher)
        except UnsichererAbgleich:
            speicher.close()
            raise
        except Exception as e:
            speicher.close()
            d.fehler = str(e)
            raise
        d.vorbereitung.eigener_speicher = True  # the preparation owns and closes it

    def exportiere(self, d: Dokument) -> Ergebnis:
        if d.vorbereitung is None or d.vorbereitung.abgeschlossen:
            raise ValueError("Dokument ist nicht analysiert.")
        d.vorbereitung.opt = Ablaufoptionen(**{**self.optionen.__dict__,
                                               "abgleich": self.abgleich,
                                               "datumsform": d.vorbereitung.opt.datumsform})
        d.ergebnis = abschliessen(d.vorbereitung)
        return d.ergebnis

    def schliessen(self) -> None:
        for d in self.dokumente:
            if d.vorbereitung is not None and not d.vorbereitung.abgeschlossen:
                d.vorbereitung.schliessen()
