"""Step 3: text recognition.  Choose the engine and its language packs, read all
documents, then check and correct the recognised text next to the cleaned-up page.
Only afterwards does the analysis look for identifiers; the review step no longer
edits text."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from anonymed.dokument import ocr as ocrmodul
from anonymed.gui.arbeiter import Arbeiter
from anonymed.gui.pruefen_seite import VORSCHAU_DPI, Seitenansicht
from anonymed.gui.sitzung import Dokument, Sitzung


class TexterkennungSeite(QWidget):
    gelesen = Signal()
    weiter = Signal()

    def __init__(self, sitzung: Sitzung, eltern: QWidget | None = None) -> None:
        super().__init__(eltern)
        self.sitzung = sitzung
        self.synchron = False
        self.laedt = lambda: False
        self._arbeiter: Arbeiter | None = None
        self._warteschlange: list[Dokument] = []
        self._gesamt = 0
        self.dokument: Dokument | None = None
        self.seite = 0
        self._geladen: tuple[int, int] | None = None
        aussen = QVBoxLayout(self)

        einstellungen = QGroupBox(self.tr("Texterkennung (OCR) für Scans und Fotos"))
        form = QFormLayout(einstellungen)
        self.engine = QComboBox()
        self.engine.addItem(self.tr("automatisch"), "auto")
        for b in ocrmodul.verfuegbare_backends():
            self.engine.addItem(b, b)
        self.engine.addItem(self.tr("aus (nur Textebene)"), "aus")
        self.engine.currentIndexChanged.connect(self._engine_gewechselt)
        form.addRow(self.tr("OCR-Programm"), self.engine)
        self.sprachen = QLineEdit()
        form.addRow(self.tr("Sprachpakete"), self.sprachen)
        self.sprachen_hinweis = QLabel("")
        self.sprachen_hinweis.setWordWrap(True)
        form.addRow(self.sprachen_hinweis)
        self.tabellen = QCheckBox(self.tr(
            "Tabellen: Gitter erkennen und die Zellen ohne Linien in einem zweiten Durchgang"
            " lesen (Tesseract)"))
        self.tabellen.setChecked(True)
        form.addRow(self.tabellen)
        zeile = QHBoxLayout()
        self.fortschritt = QProgressBar()
        self.fortschritt.setRange(0, 1)
        self.fortschritt.setValue(0)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.knopf = QPushButton(self.tr("Texterkennung starten"))
        self.knopf.setMinimumHeight(32)
        self.knopf.clicked.connect(self.starten)
        zeile.addWidget(self.fortschritt, 1)
        zeile.addWidget(self.status, 2)
        zeile.addWidget(self.knopf)
        form.addRow(zeile)
        aussen.addWidget(einstellungen)
        self._engine_gewechselt()

        teiler = QSplitter(Qt.Orientation.Horizontal)
        self.baum = QTreeWidget()
        self.baum.setHeaderLabels([self.tr("Dokument / Seite")])
        self.baum.currentItemChanged.connect(self._seite_gewaehlt)
        teiler.addWidget(self.baum)
        self.ansicht = Seitenansicht()
        self.ansicht.masken_modus = True  # nothing is drawn here, drag with modifier pans
        self.ansicht.kasten_gezeichnet.connect(lambda _r: None)
        teiler.addWidget(self.ansicht)
        rechts = QWidget()
        r = QVBoxLayout(rechts)
        r.setContentsMargins(0, 0, 0, 0)
        self.text_titel = QLabel(self.tr("Erkannter Text"))
        self.text_titel.setWordWrap(True)
        r.addWidget(self.text_titel)
        self.text = QPlainTextEdit()
        r.addWidget(self.text, 1)
        knoepfe = QHBoxLayout()
        self.uebernehmen = QPushButton(self.tr("Text übernehmen"))
        self.uebernehmen.clicked.connect(self.text_uebernehmen)
        self.zuruecksetzen = QPushButton(self.tr("Auf OCR-Text zurücksetzen"))
        self.zuruecksetzen.clicked.connect(self.text_zuruecksetzen)
        self.text_hinweis = QLabel("")
        for k in (self.uebernehmen, self.zuruecksetzen):
            k.setMinimumHeight(32)
            knoepfe.addWidget(k)
        knoepfe.addWidget(self.text_hinweis, 1)
        r.addLayout(knoepfe)
        teiler.addWidget(rechts)
        teiler.setSizes([180, 560, 460])
        aussen.addWidget(teiler, 1)

        unten = QHBoxLayout()
        self.hinweis = QLabel("")
        self.hinweis.setWordWrap(True)
        self.weiter_knopf = QPushButton(self.tr("Weiter: Analysieren"))
        self.weiter_knopf.setMinimumHeight(32)
        self.weiter_knopf.setDefault(True)
        self.weiter_knopf.clicked.connect(self.weiter.emit)
        unten.addWidget(self.hinweis, 1)
        unten.addWidget(self.weiter_knopf)
        aussen.addLayout(unten)

    # -- settings ----------------------------------------------------------------------------------
    def _engine_gewechselt(self, _i: int = 0) -> None:
        name = self.engine.currentData()
        tesseract = name == "tesseract" or (name == "auto"
                                            and ocrmodul.verfuegbare_backends()[:1]
                                            == ["tesseract"])
        self.tabellen.setEnabled(tesseract)
        if tesseract:
            programm = ocrmodul.tesseract_programm()
            installiert = ocrmodul.tesseract_sprachen(programm) if programm else []
            self.sprachen.setPlaceholderText("deu+eng")
            self.sprachen_hinweis.setText(
                self.tr("Installierte Tesseract-Sprachen: {liste}").format(
                    liste=", ".join(installiert) or "–"))
        elif name == "aus":
            self.sprachen.setPlaceholderText("")
            self.sprachen_hinweis.setText(self.tr("Es wird nur die Textebene der PDFs gelesen."))
        else:
            self.sprachen.setPlaceholderText("de")
            self.sprachen_hinweis.setText(self.tr(
                "RapidOCR: Sprachtyp de (Standard), en, latin, ch …; Modelle sind im Paket."))

    def _optionen_uebernehmen(self) -> None:
        o = self.sitzung.optionen
        o.ocr = self.engine.currentData()
        o.ocr_sprachen = self.sprachen.text().strip()
        o.ocr_tabellen = self.tabellen.isChecked()

    def _melde(self, titel: str, text: str) -> None:
        if self.synchron:
            raise RuntimeError(text)
        QMessageBox.information(self, titel, text)

    # -- reading -----------------------------------------------------------------------------------
    def starten(self) -> None:
        """Read every document (text layer, OCR after the clean-up)."""
        if not self.sitzung.dokumente:
            self._melde(self.tr("Texterkennung"), self.tr("Bitte zuerst Dokumente hinzufügen."))
            return
        if self.laedt():
            self._melde(self.tr("Texterkennung"),
                        self.tr("Die Dokumente werden noch geladen; bitte einen Moment warten."))
            return
        self._optionen_uebernehmen()
        self._warteschlange = [d for d in self.sitzung.dokumente if d.lesbar]
        self._gesamt = len(self._warteschlange)
        self.fortschritt.setRange(0, max(1, self._gesamt))
        self.fortschritt.setValue(0)
        self._naechstes()

    def _naechstes(self) -> None:
        if not self._warteschlange:
            self.knopf.setEnabled(True)
            self.fortschritt.setValue(self.fortschritt.maximum())
            self.status.setText(self.tr("Gelesen: {n} Dokument(e).").format(n=self._gesamt))
            self.aktualisiere()
            self.gelesen.emit()
            return
        d = self._warteschlange[0]
        self.knopf.setEnabled(False)
        nummer = self._gesamt - len(self._warteschlange) + 1
        self.fortschritt.setValue(nummer - 1)
        self.status.setText(self.tr("Lese {nummer} von {gesamt}: {name} …").format(
            nummer=nummer, gesamt=self._gesamt, name=d.pfad.name))
        if self.synchron:
            try:
                self.sitzung.lese(d)
            except Exception as e:  # noqa: BLE001
                self._fehler(e, d)
                return
            self._fertig(d)
            return
        self._arbeiter = Arbeiter(lambda: self.sitzung.lese(d), self)
        self._arbeiter.fertig.connect(lambda _r, d=d: self._fertig(d))
        self._arbeiter.fehler.connect(lambda e, d=d: self._fehler(e, d))
        self._arbeiter.start()

    def _fertig(self, d: Dokument) -> None:
        if self._warteschlange and self._warteschlange[0] is d:
            self._warteschlange.pop(0)
        self._naechstes()

    def _fehler(self, e: Exception, d: Dokument) -> None:
        d.fehler = str(e)
        if self.synchron:
            raise e
        QMessageBox.warning(self, d.pfad.name, str(e))
        if self._warteschlange and self._warteschlange[0] is d:
            self._warteschlange.pop(0)
        self._naechstes()

    # -- navigation --------------------------------------------------------------------------------
    def aktualisiere(self) -> None:
        self.baum.clear()
        for d in self.sitzung.dokumente:
            if d.lesung is None:
                continue
            knoten = QTreeWidgetItem([d.pfad.name])
            knoten.setData(0, Qt.ItemDataRole.UserRole, (id(d), -1))
            for s in d.lesung.seiten:
                kind = QTreeWidgetItem([self.tr("Seite {nummer}").format(nummer=s.nummer + 1)
                                        + (self.tr(" (OCR)") if s.ocr is not None else "")])
                kind.setData(0, Qt.ItemDataRole.UserRole, (id(d), s.nummer))
                knoten.addChild(kind)
            self.baum.addTopLevelItem(knoten)
            knoten.setExpanded(True)
        offen = [d for d in self.sitzung.dokumente if d.lesbar and d.lesung is None]
        texte = sum(1 for d in self.sitzung.dokumente if not d.lesbar)
        teile = []
        if offen:
            teile.append(self.tr("{n} Dokument(e) noch nicht gelesen").format(n=len(offen)))
        if texte:
            teile.append(self.tr("{n} Textdatei(en) brauchen keine Texterkennung").format(
                n=texte))
        self.hinweis.setText(" · ".join(teile))
        if self.baum.topLevelItemCount():
            erster = self.baum.topLevelItem(0)
            self.baum.setCurrentItem(erster.child(0) if erster.childCount() else erster)
        else:
            self.dokument = None
            self.ansicht.zeige_leer()
            self.text.setPlainText("")
            self.text.setReadOnly(True)

    def _seite_gewaehlt(self, aktuell: QTreeWidgetItem | None, _vorher=None) -> None:
        if aktuell is None:
            return
        kennung, nummer = aktuell.data(0, Qt.ItemDataRole.UserRole)
        d = next((d for d in self.sitzung.dokumente if id(d) == kennung), None)
        if d is None or d.lesung is None:
            return
        self.dokument = d
        self.seite = max(nummer, 0)
        self.zeichne()

    def zeichne(self) -> None:
        if self.dokument is None or self.dokument.lesung is None:
            return
        lesung = self.dokument.lesung
        self.ansicht.skala = VORSCHAU_DPI / 72.0
        self.ansicht.zeige(lesung.seitenbild(self.seite, VORSCHAU_DPI), [], None)
        schluessel = (id(self.dokument), self.seite)
        if self._geladen != schluessel:
            self.text.setPlainText(lesung.text(self.seite))
            self._geladen = schluessel
        ocr = lesung.hat_ocr(self.seite)
        self.text.setReadOnly(not ocr)
        self.uebernehmen.setEnabled(ocr)
        self.zuruecksetzen.setEnabled(ocr)
        if ocr:
            self.text_titel.setText(self.tr(
                "Erkannter Text (OCR), von Hand korrigierbar. Die Korrektur gilt für Textdatei,"
                " Markdown und die Textebene; die Schwärzung im Bild folgt den OCR-Kästen."))
            s = lesung.seiten[self.seite]
            self.text_hinweis.setText(self.tr("korrigiert") if s.korrektur is not None
                                      else self.tr("OCR-Text unverändert"))
        else:
            self.text_titel.setText(self.tr("Text der Textebene (nicht veränderbar)"))
            self.text_hinweis.setText("")

    # -- corrections -------------------------------------------------------------------------------
    def text_uebernehmen(self) -> None:
        if self.dokument is None or self.dokument.lesung is None:
            return
        lesung = self.dokument.lesung
        if not lesung.hat_ocr(self.seite):
            return
        lesung.setze_ocr_text(self.seite, self.text.toPlainText())
        self.zeichne()

    def text_zuruecksetzen(self) -> None:
        if self.dokument is None or self.dokument.lesung is None:
            return
        lesung = self.dokument.lesung
        if not lesung.hat_ocr(self.seite):
            return
        s = lesung.seiten[self.seite]
        lesung.setze_ocr_text(self.seite, s.ocr.text)
        self.text.setPlainText(s.ocr.text)
        self.zeichne()
