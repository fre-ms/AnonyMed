"""Step 2: drop the documents, choose how they are read, analyse them."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from anonymed.dokument.ocr import verfuegbare_backends
from anonymed.erkennung import ner as nermodul
from anonymed.gui.arbeiter import Arbeiter
from anonymed.gui.sitzung import Dokument, Sitzung
from anonymed.pseudonyme.speicher import UnsichererAbgleich


class Ablage(QListWidget):
    abgelegt = Signal(list)

    def __init__(self, eltern: QWidget | None = None) -> None:
        super().__init__(eltern)
        self.setAcceptDrops(True)
        self.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        pfade = [Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()]
        if pfade:
            self.abgelegt.emit(pfade)
            event.acceptProposedAction()


class DokumenteSeite(QWidget):
    geladen = Signal()  # phase zero done: the main window decides whether to clean up first
    analysiert = Signal()

    def __init__(self, sitzung: Sitzung, eltern: QWidget | None = None) -> None:
        super().__init__(eltern)
        self.sitzung = sitzung
        self.synchron = False  # tests run the work inline
        self._arbeiter: Arbeiter | None = None
        self._warteschlange: list[Dokument] = []
        self._phase = "laden"
        aussen = QVBoxLayout(self)

        self.liste = Ablage()
        self.liste.abgelegt.connect(self.hinzufuegen)
        aussen.addWidget(QLabel(self.tr("Dateien hierher ziehen oder hinzufügen:")))
        aussen.addWidget(self.liste, 1)

        knoepfe = QHBoxLayout()
        hinzu = QPushButton(self.tr("Dateien hinzufügen …"))
        hinzu.clicked.connect(self.dateien_waehlen)
        entfernen = QPushButton(self.tr("Entfernen"))
        entfernen.clicked.connect(self.entfernen)
        knoepfe.addWidget(hinzu)
        knoepfe.addWidget(entfernen)
        knoepfe.addStretch(1)
        aussen.addLayout(knoepfe)

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

        unten = QHBoxLayout()
        self.status = QLabel("")
        self.knopf = QPushButton(self.tr("Analysieren"))
        self.knopf.clicked.connect(self.analysieren)
        unten.addWidget(self.status, 1)
        unten.addWidget(self.knopf)
        aussen.addLayout(unten)

    # -- files -------------------------------------------------------------------------------------
    def dateien_waehlen(self) -> None:
        pfade, _ = QFileDialog.getOpenFileNames(self, self.tr("Dokumente"), "", self.tr(
            "Dokumente (*.pdf *.jpg *.jpeg *.png *.tif *.tiff *.heic *.heif *.bmp *.webp"
            " *.txt *.md *.srt *.vtt *.json *.eml)"))
        self.hinzufuegen([Path(p) for p in pfade])

    def hinzufuegen(self, pfade: list[Path]) -> None:
        for pfad in pfade:
            if pfad.is_dir():
                self.hinzufuegen(sorted(p for p in pfad.iterdir() if p.is_file()))
                continue
            d = self.sitzung.fuege_hinzu(pfad)
            if d is not None:
                eintrag = QListWidgetItem(self._text(d), self.liste)
                eintrag.setData(Qt.ItemDataRole.UserRole, id(d))
        self.aktualisiere()

    def entfernen(self) -> None:
        for eintrag in self.liste.selectedItems():
            d = self._dokument(eintrag)
            if d is not None:
                self.sitzung.entferne(d)
            self.liste.takeItem(self.liste.row(eintrag))
        self.aktualisiere()

    def _dokument(self, eintrag: QListWidgetItem) -> Dokument | None:
        kennung = eintrag.data(Qt.ItemDataRole.UserRole)
        return next((d for d in self.sitzung.dokumente if id(d) == kennung), None)

    def _text(self, d: Dokument) -> str:
        return f"{d.pfad.name}  –  {self._zustand(d)}"

    def _zustand(self, d: Dokument) -> str:
        if d.fehler:
            return self.tr("Fehler: {fehler}").format(fehler=d.fehler)
        if d.ergebnis is not None:
            if d.ergebnis.freigegeben:
                return self.tr("freigegeben")
            return self.tr("nicht freigegeben")
        if d.vorbereitung is None and d.vorlage is not None:
            n = len(d.vorlage.kandidaten)
            if n:
                return self.tr("{seiten} Seite(n), {n} ohne Textebene: aufbereiten").format(
                    seiten=d.vorlage.seitenzahl, n=n)
            return self.tr("{seiten} Seite(n), geladen").format(seiten=d.vorlage.seitenzahl)
        if d.vorbereitung is not None:
            v = d.vorbereitung
            if v.ist_text:
                return self.tr("Text, {treffer} Treffer").format(treffer=len(v.fundstellen))
            text = self.tr("{seiten} Seite(n), {treffer} Treffer").format(
                seiten=len(v.seiten), treffer=len(v.fundstellen))
            if v.ocr_seiten:
                text += self.tr(", OCR auf Seite(n) {seiten}").format(
                    seiten=", ".join(map(str, v.ocr_seiten)))
            return text
        return self.tr("noch nicht analysiert")

    def aktualisiere(self) -> None:
        for i in range(self.liste.count()):
            eintrag = self.liste.item(i)
            d = self._dokument(eintrag)
            if d is not None:
                eintrag.setText(self._text(d))
        n = len(self.sitzung.dokumente)
        fertig = sum(1 for d in self.sitzung.dokumente if d.vorbereitung is not None)
        self.status.setText(
            self.tr("{n} Dokument(e), {fertig} analysiert").format(n=n, fertig=fertig))

    # -- analysis ----------------------------------------------------------------------------------
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

    def analysieren(self) -> None:
        """Phase zero for every pending document (open, suggest clean-up); the main
        window then shows the clean-up step or goes straight to the analysis."""
        if self.sitzung.patient is None:
            self._melde(self.tr("Analysieren"), self.tr("Bitte zuerst einen Patienten übernehmen."))
            return
        if not self.sitzung.dokumente:
            self._melde(self.tr("Analysieren"), self.tr("Bitte zuerst Dokumente hinzufügen."))
            return
        self._optionen_uebernehmen()
        self._phase = "laden"
        self._warteschlange = [d for d in self.sitzung.dokumente
                               if d.vorbereitung is None or d.vorbereitung.abgeschlossen]
        self._naechstes()

    def analysiere_alle(self) -> None:
        """Phase one for every loaded document, with the clean-up the user chose."""
        self._phase = "analysieren"
        self._warteschlange = [d for d in self.sitzung.dokumente
                               if d.vorbereitung is None or d.vorbereitung.abgeschlossen]
        self._naechstes()

    def _naechstes(self) -> None:
        if not self._warteschlange:
            self.knopf.setEnabled(True)
            self.aktualisiere()
            if self._phase == "laden":
                self.geladen.emit()
            else:
                self.analysiert.emit()
            return
        d = self._warteschlange[0]
        self.knopf.setEnabled(False)
        laden = self._phase == "laden"
        self.status.setText((self.tr("Lade {name} …") if laden
                             else self.tr("Analysiere {name} …")).format(name=d.pfad.name))
        aufgabe = (lambda: self.sitzung.lade(d)) if laden else (lambda: self.sitzung.analysiere(d))
        if self.synchron:
            try:
                aufgabe()
            except Exception as e:  # noqa: BLE001 - shown to the user
                self._fehler(e, d)
                return
            self._fertig(d)
            return
        self._arbeiter = Arbeiter(aufgabe, self)
        self._arbeiter.fertig.connect(lambda _r, d=d: self._fertig(d))
        self._arbeiter.fehler.connect(lambda e, d=d: self._fehler(e, d))
        self._arbeiter.start()

    def _fertig(self, d: Dokument) -> None:
        if self._warteschlange and self._warteschlange[0] is d:
            self._warteschlange.pop(0)
        self.aktualisiere()
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
        self.aktualisiere()
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
