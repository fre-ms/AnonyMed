"""Step 3: clean up scans and photos before OCR, checked by eye.

Only pages without a text layer appear here.  Each has an automatic suggestion
(tilt, sheet corners, resolution, background); the user switches steps on or
off, rotates, draws black masks on the result, compares before and after, and
then starts the analysis.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from anonymed.dokument import aufbereitung as aufmodul
from anonymed.gui.pruefen_seite import VORSCHAU_DPI, Seitenansicht
from anonymed.gui.sitzung import Dokument, Sitzung


class AufbereitenSeite(QWidget):
    analysieren_gewuenscht = Signal()

    def __init__(self, sitzung: Sitzung, eltern: QWidget | None = None) -> None:
        super().__init__(eltern)
        self.sitzung = sitzung
        self.dokument: Dokument | None = None
        self.seite = 0
        self._stumm = False  # while the form is being filled from the data
        aussen = QVBoxLayout(self)
        teiler = QSplitter(Qt.Orientation.Horizontal)

        self.baum = QTreeWidget()
        self.baum.setHeaderLabels([self.tr("Dokument / Seite")])
        self.baum.currentItemChanged.connect(self._seite_gewaehlt)
        teiler.addWidget(self.baum)

        bilder = QWidget()
        b = QHBoxLayout(bilder)
        b.setContentsMargins(0, 0, 0, 0)
        links = QVBoxLayout()
        links.addWidget(QLabel(self.tr("Vorher")))
        self.vorher = Seitenansicht()
        self.vorher.setEnabled(True)
        links.addWidget(self.vorher, 1)
        rechts_bild = QVBoxLayout()
        rechts_bild.addWidget(QLabel(self.tr("Nachher (so sieht es die OCR; Ziehen zeichnet"
                                             " eine schwarze Maske)")))
        self.nachher = Seitenansicht()
        self.nachher.masken_modus = True
        self.nachher.kasten_gezeichnet.connect(self.maske)
        rechts_bild.addWidget(self.nachher, 1)
        b.addLayout(links, 1)
        b.addLayout(rechts_bild, 1)
        teiler.addWidget(bilder)

        rechts = QWidget()
        r = QVBoxLayout(rechts)
        self.hinweis = QLabel("")
        self.hinweis.setWordWrap(True)
        r.addWidget(self.hinweis)
        schritte = QGroupBox(self.tr("Schritte für diese Seite"))
        form = QFormLayout(schritte)
        self.begradigen = QCheckBox(self.tr("Schräglage begradigen"))
        self.winkel = QDoubleSpinBox()
        self.winkel.setRange(-15.0, 15.0)
        self.winkel.setSingleStep(0.1)
        self.winkel.setDecimals(1)
        self.winkel.setSuffix("°")
        zeile = QHBoxLayout()
        zeile.addWidget(self.begradigen, 1)
        zeile.addWidget(self.winkel)
        form.addRow(zeile)
        self.entzerren = QCheckBox(self.tr("Fotografiertes Blatt entzerren"))
        form.addRow(self.entzerren)
        self.hochskalieren = QCheckBox(self.tr("Auf das Doppelte hochskalieren"))
        form.addRow(self.hochskalieren)
        self.hintergrund = QCheckBox(self.tr("Hintergrund glätten (Schatten, graues Papier)"))
        form.addRow(self.hintergrund)
        self.schwarzweiss = QCheckBox(self.tr("Schwarz-Weiß"))
        form.addRow(self.schwarzweiss)
        drehen = QHBoxLayout()
        for text, grad in ((self.tr("↺ 90°"), 270), (self.tr("↻ 90°"), 90),
                           (self.tr("180°"), 180)):
            k = QPushButton(text)
            k.clicked.connect(lambda _c=False, g=grad: self.drehe(g))
            drehen.addWidget(k)
        form.addRow(self.tr("Drehen"), drehen)
        self.masken_loeschen = QPushButton(self.tr("Masken löschen"))
        self.masken_loeschen.clicked.connect(self.loesche_masken)
        form.addRow(self.masken_loeschen)
        r.addWidget(schritte)
        for w in (self.begradigen, self.entzerren, self.hochskalieren, self.hintergrund,
                  self.schwarzweiss):
            w.toggled.connect(lambda _an: self._formular_uebernehmen())
        self.winkel.valueChanged.connect(lambda _w: self._formular_uebernehmen())

        knoepfe = QVBoxLayout()
        self.vorschlag = QPushButton(self.tr("Vorschlag wiederherstellen"))
        self.vorschlag.clicked.connect(self.vorschlag_wiederherstellen)
        self.nichts = QPushButton(self.tr("Diese Seite nicht aufbereiten"))
        self.nichts.clicked.connect(self.nichts_aufbereiten)
        self.alle = QPushButton(self.tr("Schritte auf alle Seiten übertragen"))
        self.alle.clicked.connect(self.auf_alle_uebertragen)
        for k in (self.vorschlag, self.nichts, self.alle):
            knoepfe.addWidget(k)
        r.addLayout(knoepfe)
        r.addStretch(1)
        zoom = QHBoxLayout()
        for text, aktion in (("+", lambda: self._zoom(1.25)), ("−", lambda: self._zoom(0.8)),
                             ("1:1", self._zoom_zuruecksetzen)):
            k = QPushButton(text)
            k.setMaximumWidth(40)
            k.clicked.connect(aktion)
            zoom.addWidget(k)
        zoom.addStretch(1)
        self.weiter = QPushButton(self.tr("Analysieren"))
        self.weiter.setDefault(True)
        self.weiter.clicked.connect(self.analysieren_gewuenscht.emit)
        zoom.addWidget(self.weiter)
        r.addLayout(zoom)
        teiler.addWidget(rechts)
        teiler.setSizes([180, 760, 300])
        aussen.addWidget(teiler, 1)
        if not aufmodul.verfuegbar():
            self.hinweis.setText(self.tr(
                "Bildaufbereitung braucht OpenCV und NumPy (Extra 'ocr'); hier sind nur"
                " Masken möglich."))

    # -- navigation ------------------------------------------------------------------------------
    def aktualisiere(self) -> None:
        self.baum.clear()
        for d in self.sitzung.dokumente:
            if d.vorlage is None or not d.vorlage.kandidaten:
                continue
            knoten = QTreeWidgetItem([d.pfad.name])
            knoten.setData(0, Qt.ItemDataRole.UserRole, (id(d), -1))
            for n in d.vorlage.kandidaten:
                kind = QTreeWidgetItem([self.tr("Seite {nummer}").format(nummer=n + 1)])
                kind.setData(0, Qt.ItemDataRole.UserRole, (id(d), n))
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
        if d is None or d.vorlage is None:
            return
        self.dokument = d
        self.seite = max(nummer, 0)
        self._formular_fuellen()
        self.zeichne()

    # -- data <-> form -----------------------------------------------------------------------------
    def einstellung(self) -> aufmodul.Aufbereitung | None:
        if self.dokument is None or self.dokument.vorlage is None:
            return None
        return self.dokument.aufbereitung.setdefault(
            self.seite, _kopie(self.dokument.vorlage.vorschlaege.get(self.seite)))

    def _formular_fuellen(self) -> None:
        a = self.einstellung()
        if a is None:
            return
        self._stumm = True
        try:
            self.begradigen.setChecked(a.begradigen)
            self.winkel.setValue(a.winkel)
            self.entzerren.setChecked(a.entzerren)
            self.entzerren.setEnabled(bool(a.ecken))
            self.hochskalieren.setChecked(a.hochskalieren > 1)
            self.hintergrund.setChecked(a.hintergrund)
            self.schwarzweiss.setChecked(a.schwarzweiss)
        finally:
            self._stumm = False

    def _formular_uebernehmen(self) -> None:
        if self._stumm:
            return
        a = self.einstellung()
        if a is None:
            return
        a.begradigen = self.begradigen.isChecked()
        a.winkel = self.winkel.value()
        a.entzerren = self.entzerren.isChecked() and bool(a.ecken)
        a.hochskalieren = 2 if self.hochskalieren.isChecked() else 1
        a.hintergrund = self.hintergrund.isChecked()
        a.schwarzweiss = self.schwarzweiss.isChecked()
        self.zeichne()

    # -- actions -----------------------------------------------------------------------------------
    def drehe(self, grad: int) -> None:
        a = self.einstellung()
        if a is None:
            return
        a.drehung = (a.drehung + grad) % 360
        a.masken = []  # drawn on the old orientation
        self.zeichne()

    def maske(self, rect: tuple[float, float, float, float]) -> None:
        a = self.einstellung()
        if a is None:
            return
        a.masken.append(rect)
        self.zeichne()

    def loesche_masken(self) -> None:
        a = self.einstellung()
        if a is not None:
            a.masken = []
            self.zeichne()

    def vorschlag_wiederherstellen(self) -> None:
        if self.dokument is None or self.dokument.vorlage is None:
            return
        self.dokument.aufbereitung[self.seite] = _kopie(
            self.dokument.vorlage.vorschlaege.get(self.seite))
        self._formular_fuellen()
        self.zeichne()

    def nichts_aufbereiten(self) -> None:
        if self.dokument is None:
            return
        self.dokument.aufbereitung[self.seite] = aufmodul.Aufbereitung()
        self._formular_fuellen()
        self.zeichne()

    def auf_alle_uebertragen(self) -> None:
        """Copy the switches (not the page's own tilt, corners or masks) to every
        candidate page of every document."""
        quelle = self.einstellung()
        if quelle is None:
            return
        for d in self.sitzung.dokumente:
            if d.vorlage is None:
                continue
            for n in d.vorlage.kandidaten:
                ziel = d.aufbereitung.setdefault(n, _kopie(d.vorlage.vorschlaege.get(n)))
                ziel.begradigen = quelle.begradigen and abs(ziel.winkel) >= 0.05
                ziel.entzerren = quelle.entzerren and bool(ziel.ecken)
                ziel.hochskalieren = quelle.hochskalieren
                ziel.hintergrund = quelle.hintergrund
                ziel.schwarzweiss = quelle.schwarzweiss
        self.zeichne()

    # -- drawing -----------------------------------------------------------------------------------
    def zeichne(self) -> None:
        a = self.einstellung()
        if a is None or self.dokument is None or self.dokument.vorlage is None:
            return
        vorlage = self.dokument.vorlage
        self.vorher.skala = VORSCHAU_DPI / 72.0
        self.vorher.zeige(vorlage.seitenbild(self.seite, VORSCHAU_DPI), [], None)
        try:
            png = vorlage.vorschau(self.seite, a, VORSCHAU_DPI)
        except Exception as e:  # noqa: BLE001 - shown instead of crashing the step
            self.hinweis.setText(self.tr("Aufbereitung fehlgeschlagen: {fehler}").format(
                fehler=e))
            return
        bild = QImage.fromData(png)
        self.nachher.skala = bild.width() / vorlage.seitenbreite(self.seite, a)
        self.nachher.zeige(png, [], None)
        text = aufmodul.beschreibung(a)
        if a.ecken is None and not vorlage.foto:
            text += " · " + self.tr("kein fotografiertes Blatt erkannt")
        self.hinweis.setText(text)

    def _zoom(self, faktor: float) -> None:
        self.vorher.zoom(faktor)
        self.nachher.zoom(faktor)

    def _zoom_zuruecksetzen(self) -> None:
        self.vorher.zoom_zuruecksetzen()
        self.nachher.zoom_zuruecksetzen()


def _kopie(a: aufmodul.Aufbereitung | None) -> aufmodul.Aufbereitung:
    if a is None:
        return aufmodul.Aufbereitung()
    return aufmodul.Aufbereitung(**{**a.__dict__, "masken": list(a.masken),
                                    "ecken": list(a.ecken) if a.ecken else None})
