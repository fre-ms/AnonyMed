"""Step 2: drop the documents.  They are opened at once; scans and photos appear
below for the clean-up before OCR."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from anonymed.gui.arbeiter import Arbeiter
from anonymed.gui.aufbereiten_seite import AufbereitenSeite
from anonymed.gui.sitzung import Dokument, Sitzung


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
    geladen = Signal()  # every pending document is open and, where needed, has a suggestion
    weiter = Signal()  # the user wants to go on to the analysis

    def __init__(self, sitzung: Sitzung, eltern: QWidget | None = None) -> None:
        super().__init__(eltern)
        self.sitzung = sitzung
        self.synchron = False  # tests run the work inline
        self._arbeiter: Arbeiter | None = None
        self._warteschlange: list[Dokument] = []
        aussen = QVBoxLayout(self)

        aussen.addWidget(QLabel(self.tr(
            "Dateien hierher ziehen oder hinzufügen (PDF, Fotos, Texte, Whisper-JSON, E-Mails)."
            " Scans und Fotos erscheinen darunter zum Aufbereiten vor der Texterkennung.")))
        oben = QHBoxLayout()
        self.liste = Ablage()
        self.liste.setMaximumHeight(150)
        self.liste.abgelegt.connect(self.hinzufuegen)
        oben.addWidget(self.liste, 1)
        knoepfe = QVBoxLayout()
        hinzu = QPushButton(self.tr("Dateien hinzufügen …"))
        hinzu.clicked.connect(self.dateien_waehlen)
        entfernen = QPushButton(self.tr("Entfernen"))
        entfernen.clicked.connect(self.entfernen)
        knoepfe.addWidget(hinzu)
        knoepfe.addWidget(entfernen)
        knoepfe.addStretch(1)
        oben.addLayout(knoepfe)
        aussen.addLayout(oben)

        self.aufbereiten = AufbereitenSeite(sitzung)
        aussen.addWidget(self.aufbereiten, 1)

        unten = QHBoxLayout()
        self.status = QLabel("")
        self.weiter_knopf = QPushButton(self.tr("Weiter: Texterkennung"))
        self.weiter_knopf.setDefault(True)
        self.weiter_knopf.clicked.connect(self.weiter.emit)
        unten.addWidget(self.status, 1)
        unten.addWidget(self.weiter_knopf)
        aussen.addLayout(unten)

    # -- files -------------------------------------------------------------------------------------
    @property
    def laedt(self) -> bool:
        return bool(self._warteschlange)

    def dateien_waehlen(self) -> None:
        pfade, _ = QFileDialog.getOpenFileNames(self, self.tr("Dokumente"), "", self.tr(
            "Dokumente (*.pdf *.jpg *.jpeg *.png *.tif *.tiff *.heic *.heif *.bmp *.webp"
            " *.txt *.md *.srt *.vtt *.json *.eml)"))
        self.hinzufuegen([Path(p) for p in pfade])

    def hinzufuegen(self, pfade: list[Path]) -> None:
        """Add files and open them straight away (in the background)."""
        neue: list[Dokument] = []
        for pfad in pfade:
            if pfad.is_dir():
                self.hinzufuegen(sorted(p for p in pfad.iterdir() if p.is_file()))
                continue
            d = self.sitzung.fuege_hinzu(pfad)
            if d is not None:
                eintrag = QListWidgetItem(self._text(d), self.liste)
                eintrag.setData(Qt.ItemDataRole.UserRole, id(d))
                neue.append(d)
        self.aktualisiere()
        if neue:
            laeuft = self.laedt
            self._warteschlange += neue
            if not laeuft:
                self._naechstes()

    def entfernen(self) -> None:
        for eintrag in self.liste.selectedItems():
            d = self._dokument(eintrag)
            if d is not None:
                if d in self._warteschlange:
                    self._warteschlange.remove(d)
                self.sitzung.entferne(d)
            self.liste.takeItem(self.liste.row(eintrag))
        self.aktualisiere()
        self.aufbereiten.aktualisiere()

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
        if d.vorbereitung is not None:
            if d.vorbereitung.ist_text:
                return self.tr("Text, {treffer} Treffer").format(
                    treffer=len(d.vorbereitung.fundstellen))
            return self.tr("analysiert, {treffer} Treffer").format(
                treffer=len(d.vorbereitung.fundstellen))
        if d.vorlage is not None:
            n = len(d.vorlage.kandidaten)
            if n:
                return self.tr("{seiten} Seite(n), {n} ohne Textebene: unten aufbereiten").format(
                    seiten=d.vorlage.seitenzahl, n=n)
            return self.tr("{seiten} Seite(n) mit Textebene").format(seiten=d.vorlage.seitenzahl)
        if d in self._warteschlange:
            return self.tr("wird geladen …")
        return self.tr("Textdatei")

    def aktualisiere(self) -> None:
        for i in range(self.liste.count()):
            eintrag = self.liste.item(i)
            d = self._dokument(eintrag)
            if d is not None:
                eintrag.setText(self._text(d))
        n = len(self.sitzung.dokumente)
        offen = sum(1 for d in self.sitzung.dokumente if d.aufzubereiten)
        text = self.tr("{n} Dokument(e)").format(n=n)
        if offen:
            text += self.tr(", {offen} mit Seiten zum Aufbereiten").format(offen=offen)
        if self.laedt:
            text += self.tr(" – lade …")
        self.status.setText(text)

    # -- loading -----------------------------------------------------------------------------------
    def _melde(self, titel: str, text: str) -> None:
        if self.synchron:
            raise RuntimeError(text)
        QMessageBox.warning(self, titel, text)

    def _naechstes(self) -> None:
        if not self._warteschlange:
            self.aktualisiere()
            self.aufbereiten.aktualisiere()
            self.geladen.emit()
            return
        d = self._warteschlange[0]
        self.aktualisiere()
        if self.synchron:
            try:
                self.sitzung.lade(d)
            except Exception as e:  # noqa: BLE001 - shown to the user
                self._fehler(e, d)
                return
            self._fertig(d)
            return
        self._arbeiter = Arbeiter(lambda: self.sitzung.lade(d), self)
        self._arbeiter.fertig.connect(lambda _r, d=d: self._fertig(d))
        self._arbeiter.fehler.connect(lambda e, d=d: self._fehler(e, d))
        self._arbeiter.start()

    def _fertig(self, d: Dokument) -> None:
        if d in self._warteschlange:
            self._warteschlange.remove(d)
        self._naechstes()

    def _fehler(self, e: Exception, d: Dokument) -> None:
        d.fehler = str(e)
        if d in self._warteschlange:
            self._warteschlange.remove(d)
        if self.synchron:
            raise e
        QMessageBox.warning(self, d.pfad.name, str(e))
        self._naechstes()
