"""State of one desktop session, kept free of Qt so that it can be tested on its own."""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from anonymed.ablauf import (
    Ablaufoptionen,
    Ergebnis,
    Textvorbereitung,
    Vorbereitung,
    Vorlage,
    abschliessen,
    lade,
    vorbereiten,
)
from anonymed.dokument import aufbereitung as aufmodul
from anonymed.dokument.textdatei import TEXTENDUNGEN, ist_textdatei
from anonymed.kern.modell import Patient
from anonymed.pseudonyme.speicher import Pseudonymspeicher, UnsichererAbgleich


@dataclass
class Dokument:
    pfad: Path
    vorlage: Vorlage | None = None  # phase zero: open, pages known, clean-up suggested
    aufbereitung: dict[int, aufmodul.Aufbereitung] = field(default_factory=dict)
    vorbereitung: Vorbereitung | Textvorbereitung | None = None
    vorpruefung: Ergebnis | None = None  # the re-check run into a scratch folder
    vorpruefung_version: int = -1  # review state the pre-check belongs to
    ergebnis: Ergebnis | None = None
    fehler: str = ""

    @property
    def aufzubereiten(self) -> bool:
        return self.vorlage is not None and bool(self.vorlage.kandidaten)

    @property
    def pruefbereit(self) -> bool:
        return self.vorbereitung is not None and not self.vorbereitung.abgeschlossen

    @property
    def vorpruefung_aktuell(self) -> bool:
        return (self.pruefbereit and self.vorpruefung is not None
                and self.vorpruefung_version == self.vorbereitung.version)

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
        if self.vorlage is not None:
            n = len(self.vorlage.kandidaten)
            return (f"{self.vorlage.seitenzahl} Seite(n), {n} ohne Textebene (aufbereiten)"
                    if n else f"{self.vorlage.seitenzahl} Seite(n), geladen")
        return "noch nicht analysiert"


@dataclass
class Sitzung:
    speicherpfad: Path | None = None
    schema: str = "hash"
    patient: Patient | None = None
    optionen: Ablaufoptionen = field(default_factory=Ablaufoptionen)
    dokumente: list[Dokument] = field(default_factory=list)
    abgleich: str | None = None  # decision for an uncertain match, set by the dialog
    _scratch: Path | None = None

    def speicher(self) -> Pseudonymspeicher:
        return Pseudonymspeicher(self.speicherpfad, schema=self.schema)

    def scratch(self) -> Path:
        """Folder for the pre-check output, removed when the session closes."""
        if self._scratch is None or not self._scratch.exists():
            self._scratch = Path(tempfile.mkdtemp(prefix="anonymed-"))
        return self._scratch

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
        if d.vorlage is not None:
            d.vorlage.schliessen()
        self.dokumente.remove(d)

    def lade(self, d: Dokument) -> None:
        """Phase zero for one document: open it and suggest the clean-up of its
        image-only pages.  Text files have nothing to clean up."""
        if d.vorlage is not None:
            d.vorlage.schliessen()
        d.vorlage, d.aufbereitung, d.vorbereitung, d.ergebnis, d.fehler = None, {}, None, None, ""
        if ist_textdatei(d.pfad):
            return
        try:
            d.vorlage = lade(d.pfad, self.optionen)
        except Exception as e:
            d.fehler = str(e)
            raise
        d.aufbereitung = {n: aufmodul.Aufbereitung(**{**a.__dict__, "masken": list(a.masken)})
                          for n, a in d.vorlage.vorschlaege.items()}

    def analysiere(self, d: Dokument) -> None:
        """Phase one for one document; raises UnsichererAbgleich for the dialog."""
        if self.patient is None:
            raise ValueError("Kein Patient gewählt.")
        if d.vorbereitung is not None and not d.vorbereitung.abgeschlossen:
            d.vorbereitung.schliessen()
        d.vorbereitung, d.ergebnis, d.fehler = None, None, ""
        opt = Ablaufoptionen(**{**self.optionen.__dict__, "abgleich": self.abgleich})
        aufbereitung = dict(d.aufbereitung) if d.vorlage is not None else None
        speicher = self.speicher()
        try:
            d.vorbereitung = vorbereiten(d.pfad, self.patient, opt, speicher=speicher,
                                         aufbereitung=aufbereitung)
        except UnsichererAbgleich:
            speicher.close()
            raise
        except Exception as e:
            speicher.close()
            d.fehler = str(e)
            raise
        d.vorbereitung.eigener_speicher = True  # the preparation owns and closes it
        if d.vorlage is not None:
            d.vorlage.schliessen()
            d.vorlage = None

    def _optionen_fuer(self, d: Dokument, ziel_ordner: Path | None) -> Ablaufoptionen:
        return Ablaufoptionen(**{**self.optionen.__dict__, "abgleich": self.abgleich,
                                 "datumsform": d.vorbereitung.opt.datumsform,
                                 "ziel_ordner": ziel_ordner})

    def pruefe(self, d: Dokument) -> Ergebnis:
        """The whole finishing phase into a scratch folder: redaction, re-check, report.
        Nothing is exported; the result tells whether the export may happen."""
        if not d.pruefbereit:
            raise ValueError("Dokument ist nicht analysiert.")
        ordner = self.scratch() / str(id(d))
        if ordner.exists():
            shutil.rmtree(ordner)
        d.vorbereitung.opt = self._optionen_fuer(d, ordner)
        d.vorpruefung = abschliessen(d.vorbereitung, endgueltig=False)
        d.vorpruefung_version = d.vorbereitung.version
        return d.vorpruefung

    def exportiere(self, d: Dokument) -> Ergebnis:
        """Export: copy the checked files when the pre-check is current, otherwise run
        the finishing phase again into the target.  A failed re-check exports nothing."""
        if not d.pruefbereit:
            raise ValueError("Dokument ist nicht analysiert.")
        if d.vorpruefung_aktuell and d.vorpruefung is not None and self.optionen.ziel_ordner \
                is not None or (d.vorpruefung_aktuell and d.vorpruefung is not None):
            quelle = d.vorpruefung
            if not quelle.freigegeben:
                d.ergebnis = quelle
                d.vorbereitung.opt = self._optionen_fuer(d, self.optionen.ziel_ordner)
                abschliessen(d.vorbereitung, endgueltig=True)  # stores decisions, closes
                return d.ergebnis
            ziel_ordner = self.optionen.ziel_ordner or d.pfad.parent
            ziel_ordner.mkdir(parents=True, exist_ok=True)
            kopie = Ergebnis(quelle=quelle.quelle, bericht=quelle.bericht,
                             fundstellen=quelle.fundstellen, kennung=quelle.kennung,
                             text=quelle.text, ist_text=quelle.ist_text)
            for feld in ("ziel_pdf", "ziel_txt", "ziel_md", "ziel_bericht"):
                pfad = getattr(quelle, feld)
                if pfad is not None:
                    neu = ziel_ordner / pfad.name
                    shutil.copy2(pfad, neu)
                    setattr(kopie, feld, neu)
            for bild in quelle.vorschau:
                neu_ordner = ziel_ordner / bild.parent.name
                neu_ordner.mkdir(exist_ok=True)
                shutil.copy2(bild, neu_ordner / bild.name)
                kopie.vorschau.append(neu_ordner / bild.name)
            d.vorbereitung.opt = self._optionen_fuer(d, self.optionen.ziel_ordner)
            self._abschluss_ohne_ausgabe(d)
            d.ergebnis = kopie
            return d.ergebnis
        d.vorbereitung.opt = self._optionen_fuer(d, self.optionen.ziel_ordner)
        d.ergebnis = abschliessen(d.vorbereitung, endgueltig=True)
        return d.ergebnis

    def _abschluss_ohne_ausgabe(self, d: Dokument) -> None:
        """Close a preparation whose files were copied: store the mapping and the
        discard decisions as the final run would, without redacting again."""
        v = d.vorbereitung
        from anonymed.ablauf import _entscheidungen_merken

        v.speicher.merke(v.patient, v.vergabe)
        _entscheidungen_merken(v)
        v.abgeschlossen = True
        v.schliessen()

    def ampel(self) -> tuple[str, str]:
        """Traffic light for the export step: "gruen" (re-check clean, ready), "gelb"
        (released with warnings), "rot" (identifiers still found, nothing exported),
        "grau" (not checked yet)."""
        offen = [d for d in self.dokumente if d.pruefbereit]
        if not offen:
            fertig = [d for d in self.dokumente if d.ergebnis is not None]
            if fertig and all(d.ergebnis.freigegeben for d in fertig):
                return "gruen", "Exportiert."
            return "grau", "Keine analysierten Dokumente."
        if any(not d.vorpruefung_aktuell for d in offen):
            return "grau", "Erneute Prüfung steht aus."
        rot = [d for d in offen if not d.vorpruefung.freigegeben]
        if rot:
            return "rot", (f"Identifikatoren noch gefunden in {len(rot)} Dokument(en):"
                           " nichts wird exportiert.")
        gelb = [d for d in offen if d.vorpruefung.bericht.warnungen]
        if gelb:
            return "gelb", (f"Erneute Prüfung bestanden; {len(gelb)} Dokument(e) mit"
                            " Warnungen, bitte lesen.")
        return "gruen", "Erneute Prüfung bestanden: nichts mehr gefunden, bereit zum Export."

    def schliessen(self) -> None:
        for d in self.dokumente:
            if d.vorbereitung is not None and not d.vorbereitung.abgeschlossen:
                d.vorbereitung.schliessen()
            if d.vorlage is not None:
                d.vorlage.schliessen()
        if self._scratch is not None and self._scratch.exists():
            shutil.rmtree(self._scratch, ignore_errors=True)
            self._scratch = None
