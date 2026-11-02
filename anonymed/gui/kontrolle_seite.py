"""Step 7: control before export.

Two questions, two lights.  First the re-check: are all identifiers from step 1 gone
from the result (the finishing phase runs into a scratch folder, exactly as the export
will)?  Second the residual-risk estimate: how many people share what the text still
says (see :mod:`anonymed.pruefung.restrisiko`)?  Documents of the same patient are
judged together, earlier released ones included."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from anonymed.gui.arbeiter import Arbeiter
from anonymed.gui.export_seite import AMPELFARBEN
from anonymed.gui.sitzung import Dokument, Sitzung
from anonymed.konfig import lade_einstellungen, speichere_einstellungen
from anonymed.pruefung import restrisiko as risikomodul
from anonymed.pruefung.bericht import als_text


class KontrolleSeite(QWidget):
    def __init__(self, sitzung: Sitzung, eltern: QWidget | None = None) -> None:
        super().__init__(eltern)
        self.sitzung = sitzung
        self.synchron = False
        self._arbeiter: Arbeiter | None = None
        self._warteschlange: list[Dokument] = []
        e = lade_einstellungen()
        sitzung.optionen.referenzpopulation = e.referenzpopulation
        sitzung.optionen.eigene_orte = e.eigene_orte
        aussen = QVBoxLayout(self)

        # -- light 1: re-check --------------------------------------------------------------------
        zeile1 = QHBoxLayout()
        zeile1.addWidget(QLabel(self.tr(
            "Erneute Prüfung: alle Angaben aus „1 Patientendaten“ entfernt?")))
        self.ampel_text = QLabel("")
        self.ampel_text.setWordWrap(True)
        zeile1.addWidget(self.ampel_text, 1)
        self.ampel = QLabel()
        self.ampel.setFixedSize(28, 28)
        zeile1.addWidget(self.ampel)
        self.erneut = QPushButton(self.tr("Erneut prüfen"))
        self.erneut.clicked.connect(self.pruefen)
        zeile1.addWidget(self.erneut)
        aussen.addLayout(zeile1)

        # -- light 2: residual risk ---------------------------------------------------------------
        zeile2 = QHBoxLayout()
        zeile2.addWidget(QLabel(self.tr("Restrisiko: wie viele Menschen teilen, was im Text"
                                        " bleibt?")))
        self.risiko_text = QLabel("")
        self.risiko_text.setWordWrap(True)
        zeile2.addWidget(self.risiko_text, 1)
        self.risiko_ampel = QLabel()
        self.risiko_ampel.setFixedSize(28, 28)
        zeile2.addWidget(self.risiko_ampel)
        aussen.addLayout(zeile2)
        hinweis = QLabel(self.tr(
            "Schätzung nach dem k-map-Prinzip (El Emam, EMA Policy 0070, Rocher et al.): Referenz"
            " ÷ Produkt der Häufigkeiten von Geschlecht, Geburtsjahr, kleinstem genanntem Ort"
            " (Destatis) und seltenster genannter Erkrankung (Orphanet). Grün ab 11 gleichartigen"
            " Personen (κ ≤ 0,09), Gelb ab 5, Rot darunter. Eigene Synthese, kein Standard; die"
            " Unabhängigkeitsannahme ist bei untypischen Kombinationen zu optimistisch."))
        hinweis.setWordWrap(True)
        aussen.addWidget(hinweis)
        zeile3 = QHBoxLayout()
        zeile3.addWidget(QLabel(self.tr("Referenzpopulation")))
        self.referenz = QLineEdit(str(e.referenzpopulation))
        self.referenz.setMaximumWidth(120)
        self.referenz.setToolTip(self.tr(
            "Aus wie vielen Menschen ein Angreifer wählt: Deutschland 83.600.000, oder das"
            " Einzugsgebiet der Sprechstunde, wenn der Absender bekannt ist"))
        zeile3.addWidget(self.referenz)
        zeile3.addWidget(QLabel(self.tr("Eigene Orte (nicht gezählt)")))
        self.eigene_orte = QLineEdit(e.eigene_orte)
        self.eigene_orte.setPlaceholderText(self.tr("Ort der Sprechstunde, Klinik … (Komma)"))
        zeile3.addWidget(self.eigene_orte, 1)
        neu = QPushButton(self.tr("Neu berechnen"))
        neu.clicked.connect(self.neu_berechnen)
        zeile3.addWidget(neu)
        aussen.addLayout(zeile3)

        teiler = QSplitter(Qt.Orientation.Vertical)
        self.tabelle = QTableWidget(0, 6)
        self.tabelle.setHorizontalHeaderLabels([
            self.tr("Dokument"), self.tr("Erneute Prüfung"), self.tr("λ (Personen)"),
            self.tr("κ"), self.tr("Restrisiko"), self.tr("Größter Beitrag")])
        self.tabelle.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tabelle.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.tabelle.verticalHeader().setVisible(False)
        self.tabelle.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.tabelle.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tabelle.itemSelectionChanged.connect(self._zeige_details)
        teiler.addWidget(self.tabelle)
        unten = QWidget()
        ul = QHBoxLayout(unten)
        ul.setContentsMargins(0, 0, 0, 0)
        self.merkmale = QTableWidget(0, 3)
        self.merkmale.setHorizontalHeaderLabels([self.tr("Merkmal"), self.tr("Bit"),
                                                 self.tr("Beleg")])
        self.merkmale.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.merkmale.verticalHeader().setVisible(False)
        self.merkmale.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        ul.addWidget(self.merkmale, 1)
        self.protokoll = QPlainTextEdit()
        self.protokoll.setReadOnly(True)
        ul.addWidget(self.protokoll, 1)
        teiler.addWidget(unten)
        aussen.addWidget(teiler, 1)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        aussen.addWidget(self.status)
        self._ampel_setzen(self.ampel, "grau")
        self._ampel_setzen(self.risiko_ampel, "grau")

    # -- lights ------------------------------------------------------------------------------------
    @staticmethod
    def _ampel_setzen(label: QLabel, farbe: str) -> None:
        label.setStyleSheet(
            f"background: {AMPELFARBEN[farbe]}; border-radius: 14px; border: 1px solid #555;")
        label.setProperty("farbe", farbe)

    @property
    def farbe(self) -> str:
        return self.ampel.property("farbe") or "grau"

    @property
    def risiko_farbe(self) -> str:
        return self.risiko_ampel.property("farbe") or "grau"

    def aktualisiere(self) -> None:
        """Called when the step is shown: run the re-check for what needs it, then fill in."""
        if any(d.pruefbereit and not d.vorpruefung_aktuell for d in self.sitzung.dokumente):
            self.pruefen()
        else:
            self._darstellen()

    def neu_berechnen(self) -> None:
        """New reference population or own places: store them and run the check again."""
        try:
            referenz = int(self.referenz.text().replace(".", "").replace(" ", "") or 0)
        except ValueError:
            referenz = 0
        if referenz < 100:
            self.status.setText(self.tr("Referenzpopulation: bitte eine Zahl ab 100."))
            return
        e = lade_einstellungen()
        e.referenzpopulation, e.eigene_orte = referenz, self.eigene_orte.text().strip()
        try:
            speichere_einstellungen(e)
        except OSError:
            pass
        self.sitzung.optionen.referenzpopulation = referenz
        self.sitzung.optionen.eigene_orte = e.eigene_orte
        for d in self.sitzung.dokumente:
            d.vorpruefung_version = -1  # forces a new pre-check
        self.pruefen()

    def pruefen(self) -> None:
        if self._warteschlange:
            return
        self._warteschlange = [d for d in self.sitzung.dokumente if d.pruefbereit]
        if not self._warteschlange:
            self._darstellen()
            return
        self._ampel_setzen(self.ampel, "grau")
        self.ampel_text.setText(self.tr("Erneute Prüfung läuft …"))
        self._naechstes()

    def _naechstes(self) -> None:
        if not self._warteschlange:
            self.erneut.setEnabled(True)
            self._darstellen()
            return
        d = self._warteschlange.pop(0)
        self.erneut.setEnabled(False)
        self.status.setText(self.tr("Prüfe {name} …").format(name=d.pfad.name))
        if self.synchron:
            try:
                self.sitzung.pruefe(d)
            except Exception as e:  # noqa: BLE001
                d.fehler = str(e)
            self._naechstes()
            return
        self._arbeiter = Arbeiter(lambda d=d: self.sitzung.pruefe(d), self)
        self._arbeiter.fertig.connect(lambda _e: self._naechstes())
        self._arbeiter.fehler.connect(lambda e, d=d: (setattr(d, "fehler", str(e)),
                                                      self._naechstes()))
        self._arbeiter.start()

    # -- display -----------------------------------------------------------------------------------
    def _darstellen(self) -> None:
        farbe, text = self.sitzung.ampel()
        self._ampel_setzen(self.ampel, farbe)
        self.ampel_text.setText(self.tr(text))
        gesamt, fruehere = self.sitzung.kontrolle()
        if gesamt is None:
            self._ampel_setzen(self.risiko_ampel, "grau")
            self.risiko_text.setText(self.tr("Noch kein geprüftes Dokument."))
        else:
            self._ampel_setzen(self.risiko_ampel, gesamt.ampel)
            dominant = gesamt.dominant
            self.risiko_text.setText(
                self.tr("Alle Dokumente dieses Patienten zusammen: {kurz}; {schwelle}.").format(
                    kurz=gesamt.kurz(), schwelle=risikomodul.ampel_text(gesamt.ampel))
                + (self.tr(" Größter Beitrag: {merkmal} ({bits:.1f} Bit).").format(
                    merkmal=dominant.beschreibung, bits=dominant.bits) if dominant else "")
                + (self.tr(" Einschließlich {n} früher freigegebener Dokument(e).").format(
                    n=fruehere) if fruehere else ""))
        dokumente = [d for d in self.sitzung.dokumente if d.vorpruefung is not None]
        self.tabelle.setRowCount(len(dokumente))
        for i, d in enumerate(dokumente):
            e = d.vorpruefung
            r = e.restrisiko
            werte = [d.pfad.name,
                     self.tr("bestanden") if e.freigegeben else self.tr("NICHT bestanden"),
                     risikomodul._zahl(r.lam) if r else "–", f"{r.kappa:.2f}" if r else "–",
                     r.ampel.upper() if r else "–",
                     f"{r.dominant.beschreibung} ({r.dominant.bits:.1f} Bit)"
                     if r and r.dominant else "–"]
            for j, w in enumerate(werte):
                eintrag = QTableWidgetItem(w)
                eintrag.setData(Qt.ItemDataRole.UserRole, id(d))
                self.tabelle.setItem(i, j, eintrag)
        if dokumente and self.tabelle.currentRow() < 0:
            self.tabelle.selectRow(0)
        self._zeige_details()
        self.status.setText("")

    def _zeige_details(self) -> None:
        zeile = self.tabelle.currentRow()
        eintrag = self.tabelle.item(zeile, 0) if zeile >= 0 else None
        d = next((d for d in self.sitzung.dokumente
                  if eintrag is not None and id(d) == eintrag.data(Qt.ItemDataRole.UserRole)), None)
        if d is None or d.vorpruefung is None:
            self.merkmale.setRowCount(0)
            self.protokoll.clear()
            return
        r = d.vorpruefung.restrisiko
        zeilen = []
        if r is not None:
            for m in sorted(r.merkmale, key=lambda m: (-m.gezaehlt, -m.bits)):
                zeilen.append((m.beschreibung + ("" if m.gezaehlt else self.tr(" (nicht gezählt)")),
                               f"{m.bits:.1f}", m.beleg))
            for mk in r.markierungen:
                zeilen.append(("! " + mk.text, "", mk.beleg))
        self.merkmale.setRowCount(len(zeilen))
        for i, (a, b, c) in enumerate(zeilen):
            for j, w in enumerate((a, b, c)):
                self.merkmale.setItem(i, j, QTableWidgetItem(w))
        self.protokoll.setPlainText(als_text(d.vorpruefung.bericht))
