"""Step 5: re-check everything, show the traffic light, then write the files.

Entering the step runs the whole finishing phase into a scratch folder for every
analysed document: redaction, the re-check with the patient's identifiers, the
report.  The light sums it up: green (nothing found, ready), yellow (released,
but with warnings worth reading), red (identifiers still found, nothing will be
exported).  The export then copies the checked files to the target folder."""

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

AMPELFARBEN = {"grau": "#9a9a9a", "gruen": "#28a040", "gelb": "#e6a500", "rot": "#c81e1e"}


class ExportSeite(QWidget):
    def __init__(self, sitzung: Sitzung, eltern: QWidget | None = None) -> None:
        super().__init__(eltern)
        self.sitzung = sitzung
        self.synchron = False
        self._arbeiter: Arbeiter | None = None
        self._warteschlange: list[Dokument] = []
        self._phase = "pruefen"
        aussen = QVBoxLayout(self)

        kopf = QHBoxLayout()
        kopf.addWidget(QLabel(self.tr("Erneute Prüfung des Ergebnisses:")))
        self.ampel_text = QLabel("")
        self.ampel_text.setWordWrap(True)
        kopf.addWidget(self.ampel_text, 1)
        self.ampel = QLabel()
        self.ampel.setFixedSize(28, 28)
        kopf.addWidget(self.ampel)
        self.erneut = QPushButton(self.tr("Erneut prüfen"))
        self.erneut.clicked.connect(self.pruefen)
        kopf.addWidget(self.erneut)
        aussen.addLayout(kopf)

        optionen = QGroupBox(self.tr("Ausgabe"))
        form = QFormLayout(optionen)
        zeile = QHBoxLayout()
        self.ziel = QLineEdit()
        self.ziel.setPlaceholderText(self.tr("leer = neben der Quelle"))
        waehlen = QPushButton("…")
        waehlen.clicked.connect(self.ziel_waehlen)
        zeile.addWidget(self.ziel, 1)
        zeile.addWidget(waehlen)
        form.addRow(self.tr("Zielordner"), zeile)
        self.textebene = QCheckBox(self.tr(
            "PDF mit Textebene: OCR-Text unsichtbar einbetten (Scans bleiben durchsuchbar)"))
        self.textebene.setChecked(True)
        self.textdatei = QCheckBox(
            self.tr("Textdatei (.txt) in Lesereihenfolge (Eingabe für das Sprachmodell)"))
        self.textdatei.setChecked(True)
        self.markdown = QCheckBox(self.tr("Markdown-Datei (.md) zusätzlich"))
        self.bericht = QCheckBox(self.tr("Prüfbericht (JSON)"))
        self.bericht.setChecked(True)
        self.vermerk = QCheckBox(self.tr("Vermerk in den PDF-Metadaten"))
        self.vermerk.setChecked(True)
        self.rastern = QCheckBox(self.tr(
            "PDF vollständig rastern (nur Bild; die Textebene wird danach unsichtbar ergänzt)"))
        self.vorschau = QCheckBox(self.tr("Vorschaubilder immer schreiben"))
        self.vokabular = QCheckBox(self.tr(
            "Vokabular der verworfenen Rate-Treffer (.vokabular.txt, ein Begriff je Zeile;"
            " in „4 Analysieren“ importierbar)"))
        self.vokabular.setChecked(True)
        for w in (self.textebene, self.textdatei, self.markdown, self.bericht, self.vokabular,
                  self.vermerk, self.rastern, self.vorschau):
            form.addRow(w)
        aussen.addWidget(optionen)

        self.ausgabe = QPlainTextEdit()
        self.ausgabe.setReadOnly(True)
        aussen.addWidget(self.ausgabe, 1)

        unten = QHBoxLayout()
        self.status = QLabel("")
        self.oeffnen = QPushButton(self.tr("Zielordner öffnen"))
        self.oeffnen.clicked.connect(self.ordner_oeffnen)
        self.oeffnen.setEnabled(False)
        self.knopf = QPushButton(self.tr("Exportieren"))
        self.knopf.clicked.connect(self.exportieren)
        unten.addWidget(self.status, 1)
        unten.addWidget(self.oeffnen)
        unten.addWidget(self.knopf)
        aussen.addLayout(unten)
        self._ampel_setzen("grau", self.tr("Noch nicht geprüft."))

    def ziel_waehlen(self) -> None:
        ordner = QFileDialog.getExistingDirectory(self, self.tr("Zielordner"))
        if ordner:
            self.ziel.setText(ordner)

    def _optionen_uebernehmen(self) -> None:
        o = self.sitzung.optionen
        o.ziel_ordner = Path(self.ziel.text()) if self.ziel.text().strip() else None
        o.textdatei = self.textdatei.isChecked()
        o.markdown = self.markdown.isChecked()
        o.schwaerzen.textebene = self.textebene.isChecked()
        o.bericht = self.bericht.isChecked()
        o.vokabular = self.vokabular.isChecked()
        o.schwaerzen.vermerk = self.vermerk.isChecked()
        o.schwaerzen.rastern = self.rastern.isChecked()
        o.vorschau = "immer" if self.vorschau.isChecked() else "auto"

    # -- traffic light -----------------------------------------------------------------------------
    def _ampel_setzen(self, farbe: str, text: str) -> None:
        self.ampel.setStyleSheet(
            f"background: {AMPELFARBEN[farbe]}; border-radius: 14px; border: 1px solid #555;")
        self.ampel.setToolTip(text)
        self.ampel_text.setText(text)
        self.farbe = farbe
        self.knopf.setEnabled(farbe in ("gruen", "gelb"))

    def aktualisiere(self) -> None:
        """Called when the step is shown: run the re-check for everything that needs it."""
        farbe, text = self.sitzung.ampel()
        self._ampel_setzen(farbe, self.tr(text))
        if farbe == "grau" and any(d.pruefbereit and not d.vorpruefung_aktuell
                                   for d in self.sitzung.dokumente):
            self.pruefen()

    def pruefen(self) -> None:
        """Finishing phase into the scratch folder for every analysed document."""
        if self._warteschlange:
            return
        self._optionen_uebernehmen()
        self._phase = "pruefen"
        self._warteschlange = [d for d in self.sitzung.dokumente if d.pruefbereit]
        if not self._warteschlange:
            return
        self.ausgabe.clear()
        self._ampel_setzen("grau", self.tr("Erneute Prüfung läuft …"))
        self._naechstes()

    def _nach_pruefung(self) -> None:
        farbe, text = self.sitzung.ampel()
        self._ampel_setzen(farbe, self.tr(text))
        if farbe == "rot":
            rot = [d for d in self.sitzung.dokumente
                   if d.pruefbereit and d.vorpruefung is not None
                   and not d.vorpruefung.freigegeben]
            zeilen = []
            for d in rot:
                g = d.vorpruefung.bericht.gegenprobe
                reste = ", ".join(f"Seite {r['seite']}: {r['regel']}" for r in g.resttreffer[:8])
                zeilen.append(f"{d.pfad.name}: {len(g.resttreffer)} Resttreffer ({reste})")
            meldung = self.tr(
                "Abbruch: Bei der erneuten Prüfung wurden noch Daten aus „1 Patient“ gefunden."
                " Es wird nichts exportiert. Bitte im Prüfschritt nacharbeiten (Kasten oder"
                " Maske) und erneut prüfen.") + "\n\n" + "\n".join(zeilen)
            self.status.setText(meldung.split("\n")[0])
            if not self.synchron:
                QMessageBox.critical(self, self.tr("Export nicht möglich"), meldung)
        elif farbe == "gelb":
            self.status.setText(self.tr("Bereit; bitte die Warnungen lesen."))
        elif farbe == "gruen":
            self.status.setText(self.tr("Bereit zum Export."))

    # -- export ------------------------------------------------------------------------------------
    def exportieren(self) -> None:
        self._optionen_uebernehmen()
        offen = [d for d in self.sitzung.dokumente if d.pruefbereit]
        if not offen:
            if self.synchron:
                raise RuntimeError("Keine analysierten Dokumente.")
            QMessageBox.information(self, self.tr("Export"),
                                    self.tr("Keine analysierten Dokumente."))
            return
        if any(not d.vorpruefung_aktuell for d in offen):
            self._phase = "pruefen"  # something changed since the check: check again first
            self._warteschlange = list(offen)
            self.ausgabe.clear()
            self._ampel_setzen("grau", self.tr("Erneute Prüfung läuft …"))
            self._naechstes()
            return
        if any(not d.vorpruefung.freigegeben for d in offen):
            self._nach_pruefung()
            if self.synchron:
                raise RuntimeError("Erneute Prüfung nicht bestanden; nichts exportiert.")
            return
        self._phase = "exportieren"
        self._warteschlange = list(offen)
        self._naechstes()

    def _naechstes(self) -> None:
        if not self._warteschlange:
            if self._phase == "pruefen":
                self._nach_pruefung()
            else:
                self.status.setText(self.tr("Fertig."))
                self.oeffnen.setEnabled(True)
                farbe, text = self.sitzung.ampel()
                self._ampel_setzen(farbe, self.tr(text))
            self.erneut.setEnabled(True)
            return
        d = self._warteschlange.pop(0)
        self.knopf.setEnabled(False)
        self.erneut.setEnabled(False)
        pruefen = self._phase == "pruefen"
        self.status.setText((self.tr("Prüfe {name} …") if pruefen
                             else self.tr("Exportiere {name} …")).format(name=d.pfad.name))
        if pruefen:
            aufgabe = lambda: self.sitzung.pruefe(d)  # noqa: E731
        else:
            aufgabe = lambda: self.sitzung.exportiere(d)  # noqa: E731
        if self.synchron:
            try:
                self._fertig(aufgabe())
            except Exception as e:  # noqa: BLE001
                self._fehler(e, d)
            return
        self._arbeiter = Arbeiter(aufgabe, self)
        self._arbeiter.fertig.connect(self._fertig)
        self._arbeiter.fehler.connect(lambda e, d=d: self._fehler(e, d))
        self._arbeiter.start()

    def _fertig(self, ergebnis) -> None:
        text = als_text(ergebnis.bericht)
        if self._phase == "pruefen":
            text += (self.tr("Erneute Prüfung bestanden; Export möglich.") if ergebnis.freigegeben
                     else self.tr("Erneute Prüfung NICHT bestanden; Export gesperrt.")) + "\n"
        elif ergebnis.freigegeben:
            text += self.tr("Freigegeben: {pfad}").format(pfad=ergebnis.ziel) + "\n"
            if ergebnis.ziel_txt and not ergebnis.ist_text:
                text += self.tr("Text: {pfad}").format(pfad=ergebnis.ziel_txt) + "\n"
            if ergebnis.ziel_md:
                text += self.tr("Markdown: {pfad}").format(pfad=ergebnis.ziel_md) + "\n"
            if ergebnis.ziel_vokabular:
                text += self.tr("Vokabular: {pfad}").format(pfad=ergebnis.ziel_vokabular) + "\n"
            if ergebnis.vorschau:
                text += self.tr("Vorschau: {pfad}").format(pfad=ergebnis.vorschau[0].parent) + "\n"
        else:
            text += self.tr("NICHT freigegeben.") + "\n"
        self.ausgabe.appendPlainText(text + "\n")
        self._naechstes()

    def _fehler(self, e: Exception, d: Dokument) -> None:
        d.fehler = str(e)
        self.ausgabe.appendPlainText(
            self.tr("{name}: Fehler: {fehler}").format(name=d.pfad.name, fehler=e) + "\n")
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
