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
from anonymed.gui.arbeiter import Arbeiter
from anonymed.gui.sitzung import Dokument, Sitzung
from anonymed.pseudonyme.speicher import UnsichererAbgleich

DATEIFILTER = "Dokumente (*.pdf *.jpg *.jpeg *.png *.tif *.tiff *.heic *.heif *.bmp *.webp)"


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
    analysiert = Signal()

    def __init__(self, sitzung: Sitzung, eltern: QWidget | None = None) -> None:
        super().__init__(eltern)
        self.sitzung = sitzung
        self.synchron = False  # tests run the work inline
        self._arbeiter: Arbeiter | None = None
        self._warteschlange: list[Dokument] = []
        aussen = QVBoxLayout(self)

        self.liste = Ablage()
        self.liste.abgelegt.connect(self.hinzufuegen)
        aussen.addWidget(QLabel("Dateien hierher ziehen oder hinzufügen:"))
        aussen.addWidget(self.liste, 1)

        knoepfe = QHBoxLayout()
        hinzu = QPushButton("Dateien hinzufügen …")
        hinzu.clicked.connect(self.dateien_waehlen)
        entfernen = QPushButton("Entfernen")
        entfernen.clicked.connect(self.entfernen)
        knoepfe.addWidget(hinzu)
        knoepfe.addWidget(entfernen)
        knoepfe.addStretch(1)
        aussen.addLayout(knoepfe)

        optionen = QGroupBox("Lesen")
        form = QFormLayout(optionen)
        self.ocr = QComboBox()
        self.ocr.addItem("automatisch", "auto")
        for b in verfuegbare_backends():
            self.ocr.addItem(b, b)
        self.ocr.addItem("aus", "aus")
        form.addRow("OCR für Scans und Fotos", self.ocr)
        self.datum = QComboBox()
        self.datum.addItem("Geburtsjahr [geb. 1990]", "jahr")
        self.datum.addItem("Alter [Alter 36]", "alter")
        form.addRow("Geburtsdatum als", self.datum)
        self.codes = QCheckBox("Barcodes und QR-Codes schwärzen")
        self.codes.setChecked(True)
        form.addRow(self.codes)
        self.unlesbar = QCheckBox("Unlesbare OCR-Zeilen schwärzen")
        self.unlesbar.setChecked(True)
        form.addRow(self.unlesbar)
        aussen.addWidget(optionen)

        unten = QHBoxLayout()
        self.status = QLabel("")
        self.knopf = QPushButton("Analysieren")
        self.knopf.clicked.connect(self.analysieren)
        unten.addWidget(self.status, 1)
        unten.addWidget(self.knopf)
        aussen.addLayout(unten)

    # -- files -------------------------------------------------------------------------------------
    def dateien_waehlen(self) -> None:
        pfade, _ = QFileDialog.getOpenFileNames(self, "Dokumente", "", DATEIFILTER)
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

    @staticmethod
    def _text(d: Dokument) -> str:
        return f"{d.pfad.name}  –  {d.zustand}"

    def aktualisiere(self) -> None:
        for i in range(self.liste.count()):
            eintrag = self.liste.item(i)
            d = self._dokument(eintrag)
            if d is not None:
                eintrag.setText(self._text(d))
        n = len(self.sitzung.dokumente)
        fertig = sum(1 for d in self.sitzung.dokumente if d.vorbereitung is not None)
        self.status.setText(f"{n} Dokument(e), {fertig} analysiert")

    # -- analysis ----------------------------------------------------------------------------------
    def _optionen_uebernehmen(self) -> None:
        o = self.sitzung.optionen
        o.ocr = self.ocr.currentData()
        o.datumsform = self.datum.currentData()
        o.codes = self.codes.isChecked()
        o.unlesbar = "schwaerzen" if self.unlesbar.isChecked() else "lassen"

    def analysieren(self) -> None:
        if self.sitzung.patient is None:
            QMessageBox.information(self, "Analysieren", "Bitte zuerst einen Patienten übernehmen.")
            return
        if not self.sitzung.dokumente:
            QMessageBox.information(self, "Analysieren", "Bitte zuerst Dokumente hinzufügen.")
            return
        self._optionen_uebernehmen()
        self._warteschlange = [d for d in self.sitzung.dokumente
                               if d.vorbereitung is None or d.vorbereitung.abgeschlossen]
        self._naechstes()

    def _naechstes(self) -> None:
        if not self._warteschlange:
            self.knopf.setEnabled(True)
            self.aktualisiere()
            self.analysiert.emit()
            return
        d = self._warteschlange[0]
        self.knopf.setEnabled(False)
        self.status.setText(f"Analysiere {d.pfad.name} …")
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
        self.aktualisiere()
        self._naechstes()

    def _fehler(self, e: Exception, d: Dokument) -> None:
        if isinstance(e, UnsichererAbgleich):
            entscheidung = self.unsicher_dialog(e)
            if entscheidung is None:
                self._warteschlange.clear()
                self.knopf.setEnabled(True)
                self.status.setText("Abgebrochen.")
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
        zeilen = [f"{k.kennung}: {k.nachname}, {k.vorname}, geb. {k.geburtsdatum or '–'}"
                  f" (Übereinstimmung {k.score:.2f})" for k in e.kandidaten]
        box = QMessageBox(self)
        box.setWindowTitle("Ähnliche Person im Pseudonymspeicher")
        box.setText("Diese Person ähnelt einem vorhandenen Eintrag. Dieselbe Person?")
        box.setInformativeText("\n".join(zeilen))
        gleich = box.addButton(f"Ja, {e.kandidaten[0].kennung}", QMessageBox.ButtonRole.YesRole)
        neu = box.addButton("Nein, neue Person", QMessageBox.ButtonRole.NoRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() is gleich:
            return f"gleich:{e.kandidaten[0].kennung}"
        if box.clickedButton() is neu:
            return "neu"
        return None
