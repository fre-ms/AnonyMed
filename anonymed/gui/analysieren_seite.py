"""Step 3: how the documents are read, then the analysis itself with visible progress."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from anonymed.dokument.ocr import verfuegbare_backends
from anonymed.erkennung import ner as nermodul
from anonymed.gui.arbeiter import Arbeiter
from anonymed.gui.sitzung import Dokument, Sitzung
from anonymed.pseudonyme.speicher import UnsichererAbgleich


class AnalysierenSeite(QWidget):
    analysiert = Signal()

    def __init__(self, sitzung: Sitzung, eltern: QWidget | None = None) -> None:
        super().__init__(eltern)
        self.sitzung = sitzung
        self.synchron = False
        self.laedt = lambda: False  # the main window tells whether documents are still loading
        self._arbeiter: Arbeiter | None = None
        self._warteschlange: list[Dokument] = []
        self._gesamt = 0
        aussen = QVBoxLayout(self)

        optionen = QGroupBox(self.tr("Lesen"))
        form = QFormLayout(optionen)
        self.ocr = QComboBox()
        self.ocr.addItem(self.tr("automatisch"), "auto")
        for b in verfuegbare_backends():
            self.ocr.addItem(b, b)
        self.ocr.addItem(self.tr("aus"), "aus")
        form.addRow(self.tr("OCR für Scans und Fotos"), self.ocr)
        self.datum = QComboBox()
        self.datum.addItem(self.tr("Geburtsjahr [geb. 1990]"), "jahr")
        self.datum.addItem(self.tr("Alter [Alter 36]"), "alter")
        form.addRow(self.tr("Geburtsdatum als"), self.datum)
        self.codes = QCheckBox(self.tr("Barcodes und QR-Codes schwärzen"))
        self.codes.setChecked(True)
        form.addRow(self.codes)
        self.unlesbar = QCheckBox(self.tr("Unlesbare OCR-Zeilen schwärzen"))
        self.unlesbar.setChecked(True)
        form.addRow(self.unlesbar)
        self.fachbegriffe = QCheckBox(
            self.tr("Fachbegriffe markieren und vor NER und unscharfer OCR-Suche schützen"))
        self.fachbegriffe.setChecked(True)
        form.addRow(self.fachbegriffe)
        self.unscharf = QCheckBox(
            self.tr("Textdateien: abweichend geschriebene Namen unscharf suchen (Transkripte)"))
        self.unscharf.setChecked(True)
        form.addRow(self.unscharf)
        self.ner = QCheckBox(self.tr("Unbekannte Personen und Orte per NER suchen (rät)"))
        self.ner.setEnabled(nermodul.verfuegbar())
        self.ner.setChecked(nermodul.verfuegbar())
        if not nermodul.verfuegbar():
            self.ner.setToolTip(
                self.tr("Extra 'ner' und ein deutsches spaCy-Modell installieren."))
        form.addRow(self.ner)
        aussen.addWidget(optionen)

        aussen.addWidget(QLabel(self.tr("Dokumente dieser Sitzung:")))
        self.liste = QListWidget()
        aussen.addWidget(self.liste, 1)
        self.fortschritt = QProgressBar()
        self.fortschritt.setRange(0, 1)
        self.fortschritt.setValue(0)
        aussen.addWidget(self.fortschritt)

        unten = QHBoxLayout()
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.knopf = QPushButton(self.tr("Analysieren"))
        self.knopf.setDefault(True)
        self.knopf.clicked.connect(self.analysieren)
        unten.addWidget(self.status, 1)
        unten.addWidget(self.knopf)
        aussen.addLayout(unten)

    # -- state -----------------------------------------------------------------------------------
    def aktualisiere(self) -> None:
        self.liste.clear()
        for d in self.sitzung.dokumente:
            self.liste.addItem(f"{d.pfad.name}  –  {d.zustand}")

    def _optionen_uebernehmen(self) -> None:
        o = self.sitzung.optionen
        o.ocr = self.ocr.currentData()
        o.datumsform = self.datum.currentData()
        o.codes = self.codes.isChecked()
        o.unlesbar = "schwaerzen" if self.unlesbar.isChecked() else "lassen"
        o.ner = self.ner.isChecked()
        o.fachbegriffe = self.fachbegriffe.isChecked()
        o.unscharf_text = self.unscharf.isChecked()

    def _melde(self, titel: str, text: str) -> None:
        if self.synchron:
            raise RuntimeError(text)
        QMessageBox.information(self, titel, text)

    # -- analysis ----------------------------------------------------------------------------------
    def analysieren(self) -> None:
        if self.sitzung.patient is None:
            self._melde(self.tr("Analysieren"), self.tr("Bitte zuerst einen Patienten übernehmen."))
            return
        if not self.sitzung.dokumente:
            self._melde(self.tr("Analysieren"), self.tr("Bitte zuerst Dokumente hinzufügen."))
            return
        if self.laedt():
            self._melde(self.tr("Analysieren"),
                        self.tr("Die Dokumente werden noch geladen; bitte einen Moment warten."))
            return
        self._optionen_uebernehmen()
        self._warteschlange = [d for d in self.sitzung.dokumente
                               if d.vorbereitung is None or d.vorbereitung.abgeschlossen]
        self._gesamt = len(self._warteschlange)
        self.fortschritt.setRange(0, max(1, self._gesamt))
        self.fortschritt.setValue(0)
        self._naechstes()

    def _naechstes(self) -> None:
        self.aktualisiere()
        if not self._warteschlange:
            self.knopf.setEnabled(True)
            self.fortschritt.setValue(self.fortschritt.maximum())
            self.status.setText(self.tr("Fertig: {n} Dokument(e) analysiert.").format(
                n=self._gesamt))
            self.analysiert.emit()
            return
        d = self._warteschlange[0]
        self.knopf.setEnabled(False)
        nummer = self._gesamt - len(self._warteschlange) + 1
        self.fortschritt.setValue(nummer - 1)
        self.status.setText(self.tr("Analysiere {nummer} von {gesamt}: {name} …").format(
            nummer=nummer, gesamt=self._gesamt, name=d.pfad.name))
        if self.synchron:
            try:
                self.sitzung.analysiere(d)
            except Exception as e:  # noqa: BLE001 - shown to the user
                self._fehler(e, d)
                return
            self._fertig(d)
            return
        self._arbeiter = Arbeiter(lambda: self.sitzung.analysiere(d), self)
        self._arbeiter.fertig.connect(lambda _r, d=d: self._fertig(d))
        self._arbeiter.fehler.connect(lambda e, d=d: self._fehler(e, d))
        self._arbeiter.start()

    def _fertig(self, d: Dokument) -> None:
        if self._warteschlange and self._warteschlange[0] is d:
            self._warteschlange.pop(0)
        self._naechstes()

    def _fehler(self, e: Exception, d: Dokument) -> None:
        if self.synchron:  # tests: never block on a modal dialog
            raise e
        if isinstance(e, UnsichererAbgleich):
            entscheidung = self.unsicher_dialog(e)
            if entscheidung is None:
                self._warteschlange.clear()
                self.knopf.setEnabled(True)
                self.status.setText(self.tr("Abgebrochen."))
                return
            self.sitzung.abgleich = entscheidung
            self._naechstes()  # same document again
            return
        d.fehler = str(e)
        QMessageBox.warning(self, d.pfad.name, str(e))
        if self._warteschlange and self._warteschlange[0] is d:
            self._warteschlange.pop(0)
        self._naechstes()

    def unsicher_dialog(self, e: UnsichererAbgleich) -> str | None:
        """Ask whether the patient is one of the similar stored people; None = cancel."""
        zeilen = [self.tr("{kennung}: {nachname}, {vorname}, geb. {geb} (Übereinstimmung {score})")
                  .format(kennung=k.kennung, nachname=k.nachname, vorname=k.vorname,
                          geb=k.geburtsdatum or "–", score=f"{k.score:.2f}") for k in e.kandidaten]
        box = QMessageBox(self)
        box.setWindowTitle(self.tr("Ähnliche Person im Pseudonymspeicher"))
        box.setText(self.tr("Diese Person ähnelt einem vorhandenen Eintrag. Dieselbe Person?"))
        box.setInformativeText("\n".join(zeilen))
        gleich = box.addButton(self.tr("Ja, {kennung}").format(kennung=e.kandidaten[0].kennung),
                               QMessageBox.ButtonRole.YesRole)
        neu = box.addButton(self.tr("Nein, neue Person"), QMessageBox.ButtonRole.NoRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() is gleich:
            return f"gleich:{e.kandidaten[0].kennung}"
        if box.clickedButton() is neu:
            return "neu"
        return None
