"""Step 4: write the anonymised files, show the reports."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from anonymed.gui.arbeiter import Arbeiter
from anonymed.gui.sitzung import Dokument, Sitzung
from anonymed.pruefung.bericht import als_text


class ExportSeite(QWidget):
    def __init__(self, sitzung: Sitzung, eltern: QWidget | None = None) -> None:
        super().__init__(eltern)
        self.sitzung = sitzung
        self.synchron = False
        self._arbeiter: Arbeiter | None = None
        self._warteschlange: list[Dokument] = []
        aussen = QVBoxLayout(self)

        optionen = QGroupBox("Ausgabe")
        form = QFormLayout(optionen)
        zeile = QHBoxLayout()
        self.ziel = QLineEdit()
        self.ziel.setPlaceholderText("leer = neben der Quelle")
        waehlen = QPushButton("…")
        waehlen.clicked.connect(self.ziel_waehlen)
        zeile.addWidget(self.ziel, 1)
        zeile.addWidget(waehlen)
        form.addRow("Zielordner", zeile)
        self.textdatei = QCheckBox("Textdatei in Lesereihenfolge (Eingabe für das Sprachmodell)")
        self.textdatei.setChecked(True)
        self.bericht = QCheckBox("Prüfbericht (JSON)")
        self.bericht.setChecked(True)
        self.vermerk = QCheckBox("Vermerk in den PDF-Metadaten")
        self.vermerk.setChecked(True)
        self.rastern = QCheckBox("PDF vollständig rastern (nur Bild, keine Textebene)")
        self.vorschau = QCheckBox("Vorschaubilder immer schreiben")
        for w in (self.textdatei, self.bericht, self.vermerk, self.rastern, self.vorschau):
            form.addRow(w)
        aussen.addWidget(optionen)

        self.ausgabe = QPlainTextEdit()
        self.ausgabe.setReadOnly(True)
        aussen.addWidget(self.ausgabe, 1)

        unten = QHBoxLayout()
        self.status = QLabel("")
        self.oeffnen = QPushButton("Zielordner öffnen")
        self.oeffnen.clicked.connect(self.ordner_oeffnen)
        self.oeffnen.setEnabled(False)
        self.knopf = QPushButton("Exportieren")
        self.knopf.clicked.connect(self.exportieren)
        unten.addWidget(self.status, 1)
        unten.addWidget(self.oeffnen)
        unten.addWidget(self.knopf)
        aussen.addLayout(unten)

    def ziel_waehlen(self) -> None:
        ordner = QFileDialog.getExistingDirectory(self, "Zielordner")
        if ordner:
            self.ziel.setText(ordner)

    def _optionen_uebernehmen(self) -> None:
        o = self.sitzung.optionen
        o.ziel_ordner = Path(self.ziel.text()) if self.ziel.text().strip() else None
        o.textdatei = self.textdatei.isChecked()
        o.bericht = self.bericht.isChecked()
        o.schwaerzen.vermerk = self.vermerk.isChecked()
        o.schwaerzen.rastern = self.rastern.isChecked()
        o.vorschau = "immer" if self.vorschau.isChecked() else "auto"

    def exportieren(self) -> None:
        self._optionen_uebernehmen()
        self._warteschlange = [d for d in self.sitzung.dokumente
                               if d.vorbereitung is not None and not d.vorbereitung.abgeschlossen]
        if not self._warteschlange:
            QMessageBox.information(self, "Export", "Keine analysierten Dokumente.")
            return
        self.ausgabe.clear()
        self._naechstes()

    def _naechstes(self) -> None:
        if not self._warteschlange:
            self.knopf.setEnabled(True)
            self.status.setText("Fertig.")
            self.oeffnen.setEnabled(True)
            return
        d = self._warteschlange.pop(0)
        self.knopf.setEnabled(False)
        self.status.setText(f"Exportiere {d.pfad.name} …")
        if self.synchron:
            try:
                self._fertig(self.sitzung.exportiere(d))
            except Exception as e:  # noqa: BLE001
                self._fehler(e, d)
            return
        self._arbeiter = Arbeiter(lambda: self.sitzung.exportiere(d), self)
        self._arbeiter.fertig.connect(self._fertig)
        self._arbeiter.fehler.connect(lambda e, d=d: self._fehler(e, d))
        self._arbeiter.start()

    def _fertig(self, ergebnis) -> None:
        text = als_text(ergebnis.bericht)
        if ergebnis.freigegeben:
            text += f"Freigegeben: {ergebnis.ziel_pdf}\n"
            if ergebnis.ziel_txt:
                text += f"Text: {ergebnis.ziel_txt}\n"
        else:
            text += "NICHT freigegeben.\n"
        if ergebnis.vorschau:
            text += f"Vorschau: {ergebnis.vorschau[0].parent}\n"
        self.ausgabe.appendPlainText(text + "\n")
        self._naechstes()

    def _fehler(self, e: Exception, d: Dokument) -> None:
        d.fehler = str(e)
        self.ausgabe.appendPlainText(f"{d.pfad.name}: Fehler: {e}\n")
        self._naechstes()

    def ordner_oeffnen(self) -> None:
        ordner = self.sitzung.optionen.ziel_ordner
        if ordner is None and self.sitzung.dokumente:
            ordner = self.sitzung.dokumente[0].pfad.parent
        if ordner is None:
            return
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(ordner)])
        elif sys.platform.startswith("win"):
            subprocess.Popen(["explorer", str(ordner)])
        else:
            subprocess.Popen(["xdg-open", str(ordner)])
