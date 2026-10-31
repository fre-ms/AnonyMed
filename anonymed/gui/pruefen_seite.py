"""Step 3: look at every page, drop false hits, draw boxes over what was missed."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QImage,
    QPen,
    QPixmap,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from anonymed.gui.sitzung import Dokument, Sitzung
from anonymed.kern.modell import Fundstelle

FARBEN = {
    "text": QColor(30, 100, 200),
    "ocr": QColor(230, 120, 20),
    "code": QColor(140, 40, 160),
    "manuell": QColor(200, 30, 30),
    "unlesbar": QColor(110, 110, 110),
    "ner": QColor(0, 140, 130),
    "fachbegriff": QColor(40, 160, 60),
}
FARBEN["unscharf"] = FARBEN["ocr"]  # a guess by tolerance, like OCR hits
VORSCHAU_DPI = 100


def farbe(f: Fundstelle) -> QColor:
    if f.regel == "ocr.unlesbar":
        return FARBEN["unlesbar"]
    if f.regel.startswith("ner."):
        return FARBEN["ner"]
    return FARBEN.get(f.quelle, FARBEN["text"])


class Seitenansicht(QGraphicsView):
    """One page as an image with a rectangle per hit; drag on empty space draws a box."""

    treffer_gewaehlt = Signal(object)
    kasten_gezeichnet = Signal(object)  # (x0, y0, x1, y1) in page points

    def __init__(self, eltern: QWidget | None = None) -> None:
        super().__init__(eltern)
        self._szene = QGraphicsScene(self)
        self.setScene(self._szene)
        self.setRenderHints(self.renderHints())
        self.skala = VORSCHAU_DPI / 72.0
        self._items: dict[int, Fundstelle] = {}
        self._start: QPointF | None = None
        self._gummi: QGraphicsRectItem | None = None

    def zeige(self, png: bytes, fundstellen: list[Fundstelle], gewaehlt: Fundstelle | None,
              fachrects: list[tuple[float, float, float, float]] = ()) -> None:
        self._szene.clear()
        self._items.clear()
        bild = QImage.fromData(png)
        self._szene.addItem(QGraphicsPixmapItem(QPixmap.fromImage(bild)))
        self._szene.setSceneRect(0, 0, bild.width(), bild.height())
        gruen = FARBEN["fachbegriff"]
        hell = QColor(gruen)
        hell.setAlpha(28)
        for rect in fachrects:  # protected medical terms: outline only, not clickable
            r = QRectF(rect[0] * self.skala, rect[1] * self.skala,
                       (rect[2] - rect[0]) * self.skala, (rect[3] - rect[1]) * self.skala)
            item = QGraphicsRectItem(r)
            item.setPen(QPen(gruen, 1))
            item.setBrush(QBrush(hell))
            item.setToolTip(self.tr("Fachbegriff (geschützt)"))
            self._szene.addItem(item)
        for f in fundstellen:
            for rect in f.rects:
                r = QRectF(rect[0] * self.skala, rect[1] * self.skala,
                           (rect[2] - rect[0]) * self.skala, (rect[3] - rect[1]) * self.skala)
                item = QGraphicsRectItem(r)
                c = farbe(f)
                breite = 3 if f is gewaehlt else 1.5
                item.setPen(QPen(c, breite))
                fuell = QColor(c)
                fuell.setAlpha(90 if f is gewaehlt else 45)
                item.setBrush(QBrush(fuell))
                item.setToolTip(f"{f.regel} → {f.pseudonym}")
                self._szene.addItem(item)
                self._items[id(item)] = f

    def wheelEvent(self, event) -> None:  # noqa: N802
        """Ctrl (Cmd) + wheel zooms, plain wheel scrolls."""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            faktor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            self.scale(faktor, faktor)
            return
        super().wheelEvent(event)

    def zoom(self, faktor: float) -> None:
        self.scale(faktor, faktor)

    def zoom_zuruecksetzen(self) -> None:
        self.resetTransform()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        pos = self.mapToScene(event.position().toPoint())
        for item in self._szene.items(pos):
            f = self._items.get(id(item))
            if f is not None:
                self.treffer_gewaehlt.emit(f)
                return
        self._start = pos
        self._gummi = QGraphicsRectItem(QRectF(pos, pos))
        self._gummi.setPen(QPen(FARBEN["manuell"], 1.5, Qt.PenStyle.DashLine))
        self._szene.addItem(self._gummi)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._start is not None and self._gummi is not None:
            pos = self.mapToScene(event.position().toPoint())
            self._gummi.setRect(QRectF(self._start, pos).normalized())

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._start is None or self._gummi is None:
            return
        r = self._gummi.rect()
        self._szene.removeItem(self._gummi)
        self._start, self._gummi = None, None
        if r.width() > 4 and r.height() > 4:
            self.kasten_gezeichnet.emit((r.left() / self.skala, r.top() / self.skala,
                                         r.right() / self.skala, r.bottom() / self.skala))


class Textansicht(QTextEdit):
    """A text file with a coloured span per hit; a click on a span selects the hit,
    a text selection can be marked by hand."""

    treffer_gewaehlt = Signal(object)

    def __init__(self, eltern: QWidget | None = None) -> None:
        super().__init__(eltern)
        self.setReadOnly(True)
        self._treffer: list[Fundstelle] = []
        self._text: str | None = None
        self._schrift = self.font()

    def zeige(self, text: str, fundstellen: list[Fundstelle], gewaehlt: Fundstelle | None,
              fachspannen: list[tuple[int, int]] = ()) -> None:
        if text != self._text:
            self._text = text
            self.setPlainText(text)
        self._treffer = [f for f in fundstellen if f.ende > f.start]
        auswahlen = [self._auswahl(a, b, FARBEN["fachbegriff"], 45) for a, b in fachspannen]
        for f in self._treffer:
            auswahlen.append(self._auswahl(f.start, f.ende, farbe(f),
                                           170 if f is gewaehlt else 80))
        self.setExtraSelections(auswahlen)
        if gewaehlt is not None and gewaehlt.ende > gewaehlt.start:
            cursor = self.textCursor()
            cursor.setPosition(min(gewaehlt.start, len(text)))
            self.setTextCursor(cursor)
            self.ensureCursorVisible()

    def _auswahl(self, a: int, b: int, c: QColor, alpha: int) -> QTextEdit.ExtraSelection:
        s = QTextEdit.ExtraSelection()
        cursor = QTextCursor(self.document())
        cursor.setPosition(a)
        cursor.setPosition(b, QTextCursor.MoveMode.KeepAnchor)
        s.cursor = cursor
        fmt = QTextCharFormat()
        hell = QColor(c)
        hell.setAlpha(alpha)
        fmt.setBackground(QBrush(hell))
        s.format = fmt
        return s

    def mousePressEvent(self, event) -> None:  # noqa: N802
        super().mousePressEvent(event)
        pos = self.cursorForPosition(event.position().toPoint()).position()
        for f in self._treffer:
            if f.start <= pos < f.ende:
                self.treffer_gewaehlt.emit(f)
                return

    def auswahl(self) -> tuple[int, int] | None:
        """Offsets of the current selection, or None."""
        cursor = self.textCursor()
        if not cursor.hasSelection():
            return None
        return cursor.selectionStart(), cursor.selectionEnd()

    def zoom(self, faktor: float) -> None:
        if faktor > 1:
            self.zoomIn(1)
        else:
            self.zoomOut(1)

    def zoom_zuruecksetzen(self) -> None:
        self.setFont(self._schrift)


class PruefenSeite(QWidget):
    def __init__(self, sitzung: Sitzung, eltern: QWidget | None = None) -> None:
        super().__init__(eltern)
        self.sitzung = sitzung
        self.dokument: Dokument | None = None
        self.seite = 0
        self.gewaehlt: Fundstelle | None = None
        self.synchron = False

        aussen = QVBoxLayout(self)
        teiler = QSplitter(Qt.Orientation.Horizontal)
        self.baum = QTreeWidget()
        self.baum.setHeaderLabels([self.tr("Dokument / Seite")])
        self.baum.currentItemChanged.connect(self._seite_gewaehlt)
        teiler.addWidget(self.baum)

        self.ansicht = Seitenansicht()
        self.ansicht.treffer_gewaehlt.connect(self.waehle)
        self.ansicht.kasten_gezeichnet.connect(self.kasten)
        self.textansicht = Textansicht()
        self.textansicht.treffer_gewaehlt.connect(self.waehle)
        self.stapel = QStackedWidget()
        self.stapel.addWidget(self.ansicht)
        self.stapel.addWidget(self.textansicht)
        teiler.addWidget(self.stapel)

        rechts = QWidget()
        r = QVBoxLayout(rechts)
        legende = QLabel(self.tr(
            "Blau: Textebene · Orange: OCR oder unscharfe Suche · Lila: Code · Rot: manuell ·"
            " Grau: unlesbar · Türkis: NER · Grün: geschützter Fachbegriff. Ziehen mit der Maus"
            " zeichnet einen Kasten; in Textdateien markiert „Auswahl schwärzen“ den"
            " ausgewählten Text."))
        legende.setWordWrap(True)
        r.addWidget(legende)
        self.tabelle = QTableWidget(0, 4)
        self.tabelle.setHorizontalHeaderLabels([self.tr("Seite"), self.tr("Regel"),
                                                self.tr("Pseudonym"), self.tr("Quelle")])
        self.tabelle.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.tabelle.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.tabelle.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tabelle.itemSelectionChanged.connect(self._zeile_gewaehlt)
        r.addWidget(self.tabelle, 1)
        self.fach_anzeigen = QCheckBox(self.tr("Fachbegriffe markieren"))
        self.fach_anzeigen.setChecked(True)
        self.fach_anzeigen.toggled.connect(lambda _an: self.zeichne())
        r.addWidget(self.fach_anzeigen)
        knoepfe = QHBoxLayout()
        self.verwerfen = QPushButton(self.tr("Treffer verwerfen"))
        self.verwerfen.clicked.connect(self.verwerfe)
        knoepfe.addWidget(self.verwerfen)
        self.markieren = QPushButton(self.tr("Auswahl schwärzen"))
        self.markieren.clicked.connect(lambda: self.markiere_auswahl())
        self.markieren.setVisible(False)
        knoepfe.addWidget(self.markieren)
        for text, aktion in (("+", lambda: self._aktive_ansicht().zoom(1.25)),
                             ("−", lambda: self._aktive_ansicht().zoom(0.8)),
                             ("1:1", lambda: self._aktive_ansicht().zoom_zuruecksetzen())):
            k = QPushButton(text)
            k.setMaximumWidth(40)
            k.clicked.connect(aktion)
            knoepfe.addWidget(k)
        r.addLayout(knoepfe)
        self.hinweis = QLabel("")
        self.hinweis.setWordWrap(True)
        r.addWidget(self.hinweis)
        teiler.addWidget(rechts)
        teiler.setSizes([180, 620, 360])
        aussen.addWidget(teiler, 1)

    # -- navigation --------------------------------------------------------------------------------
    def aktualisiere(self) -> None:
        self.baum.clear()
        for d in self.sitzung.dokumente:
            if d.vorbereitung is None or d.vorbereitung.abgeschlossen:
                continue
            knoten = QTreeWidgetItem([d.pfad.name])  # file name, not translated
            knoten.setData(0, Qt.ItemDataRole.UserRole, (id(d), -1))
            for s in d.vorbereitung.seiten:
                if d.vorbereitung.ist_text:
                    kind = QTreeWidgetItem([self.tr("Text")])
                else:
                    kind = QTreeWidgetItem([self.tr("Seite {nummer}").format(nummer=s.nummer + 1)
                                            + (" (OCR)" if s.ocr is not None else "")])
                kind.setData(0, Qt.ItemDataRole.UserRole, (id(d), s.nummer))
                knoten.addChild(kind)
            self.baum.addTopLevelItem(knoten)
            knoten.setExpanded(True)
        if self.baum.topLevelItemCount():
            erster = self.baum.topLevelItem(0)
            self.baum.setCurrentItem(erster.child(0) if erster.childCount() else erster)

    def _seite_gewaehlt(self, aktuell: QTreeWidgetItem | None, _vorher=None) -> None:
        if aktuell is None:
            return
        kennung, nummer = aktuell.data(0, Qt.ItemDataRole.UserRole)
        d = next((d for d in self.sitzung.dokumente if id(d) == kennung), None)
        if d is None or d.vorbereitung is None:
            return
        self.dokument = d
        self.seite = max(nummer, 0)
        self.gewaehlt = None
        self.zeichne()

    def fundstellen(self) -> list[Fundstelle]:
        if self.dokument is None or self.dokument.vorbereitung is None:
            return []
        return [f for f in self.dokument.vorbereitung.fundstellen if f.seite == self.seite]

    def _aktive_ansicht(self):
        return self.stapel.currentWidget()

    def _zeige_ansicht(self, treffer: list[Fundstelle]) -> None:
        v = self.dokument.vorbereitung
        zeigen = self.fach_anzeigen.isChecked()
        if v.ist_text:
            fach = v.fachbegriffe_spannen(self.seite) if zeigen else []
            self.textansicht.zeige(v.text, treffer, self.gewaehlt, fach)
            self.stapel.setCurrentWidget(self.textansicht)
        else:
            fach = v.fachbegriffe_rects(self.seite) if zeigen else []
            self.ansicht.zeige(v.seitenbild(self.seite, VORSCHAU_DPI), treffer, self.gewaehlt,
                               fach)
            self.stapel.setCurrentWidget(self.ansicht)
        self.markieren.setVisible(v.ist_text)

    def zeichne(self) -> None:
        if self.dokument is None or self.dokument.vorbereitung is None:
            return
        v = self.dokument.vorbereitung
        self._zeige_ansicht(self.fundstellen())
        self.tabelle.blockSignals(True)
        self.tabelle.setRowCount(0)
        for f in self.fundstellen():
            zeile = self.tabelle.rowCount()
            self.tabelle.insertRow(zeile)
            for spalte, wert in enumerate((str(f.seite + 1), f.regel, f.pseudonym, f.quelle)):
                self.tabelle.setItem(zeile, spalte, QTableWidgetItem(wert))
            if f is self.gewaehlt:
                self.tabelle.selectRow(zeile)
        self.tabelle.blockSignals(False)
        warnungen = [w for w in v.warnungen
                     if "OCR" in w or "Textebene" in w or w.startswith("Unscharfe Suche")]
        hinweis = self.tr("{n} Fachbegriffe markiert").format(n=v.fachbegriffe_anzahl)
        self.hinweis.setText(" · ".join([hinweis] + warnungen[:1]))

    # -- actions ---------------------------------------------------------------------------------
    def waehle(self, f: Fundstelle) -> None:
        self.gewaehlt = f
        self.zeichne()

    def _zeile_gewaehlt(self) -> None:
        zeilen = self.tabelle.selectionModel().selectedRows()
        if zeilen:
            treffer = self.fundstellen()
            i = zeilen[0].row()
            if 0 <= i < len(treffer):
                self.gewaehlt = treffer[i]
                self._zeige_ansicht(treffer)

    def verwerfe(self) -> None:
        if self.gewaehlt is None or self.dokument is None or self.dokument.vorbereitung is None:
            return
        self.dokument.vorbereitung.verwerfe(self.gewaehlt)
        self.gewaehlt = None
        self.zeichne()

    def kasten(self, rect: tuple[float, float, float, float], pseudonym: str | None = None) -> None:
        if self.dokument is None or self.dokument.vorbereitung is None:
            return
        if pseudonym is None:
            pseudonym, ok = QInputDialog.getText(self, self.tr("Kasten"),
                                                 self.tr("Ersatztext im Kasten:"),
                                                 text=self.tr("[geschwärzt]"))
            if not ok:
                return
        self.dokument.vorbereitung.kasten(self.seite, rect, pseudonym or self.tr("[geschwärzt]"))
        self.gewaehlt = None
        self.zeichne()

    def markiere_auswahl(self, pseudonym: str | None = None) -> None:
        """Text files: the selected text becomes a manual hit."""
        if self.dokument is None or self.dokument.vorbereitung is None \
                or not self.dokument.vorbereitung.ist_text:
            return
        spanne = self.textansicht.auswahl()
        if spanne is None:
            if self.synchron:
                raise ValueError("Keine Auswahl.")
            return
        if pseudonym is None:
            pseudonym, ok = QInputDialog.getText(self, self.tr("Auswahl schwärzen"),
                                                 self.tr("Ersatztext:"),
                                                 text=self.tr("[geschwärzt]"))
            if not ok:
                return
        self.dokument.vorbereitung.markiere(self.seite, spanne[0], spanne[1],
                                            pseudonym or self.tr("[geschwärzt]"))
        self.gewaehlt = None
        self.zeichne()
