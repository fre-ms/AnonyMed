"""Step 2: identifiers beyond the patient's data as a sortable, editable table.

Professions, hospitals, study acronyms, titles and the user's own identifiers from the
lexicon of the pseudonym database.  Like the patient's data they are redacted by default;
each class has a context rule (see :mod:`pseudonymed.erkennung.identifikatoren`), and the
visual review may discard every hit.  Search, sort by any column, edit a cell, add and
delete rows, exchange CSV; deleted bundled rows can be brought back."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from pseudonymed.erkennung import identifikatoren as identmodul
from pseudonymed.gui.sitzung import Sitzung


class IdentifikatorenModell(QAbstractTableModel):
    def __init__(self, seite: IdentifikatorenSeite) -> None:
        super().__init__(seite)
        self.seite = seite
        self.eintraege: list[identmodul.Eintrag] = []
        self.titel = [self.tr("Begriff"), self.tr("Klasse"), self.tr("Angaben"),
                      self.tr("Quelle")]

    def lade(self, eintraege: list[identmodul.Eintrag]) -> None:
        self.beginResetModel()
        self.eintraege = list(eintraege)
        self.endResetModel()

    def rowCount(self, parent: QModelIndex | None = None) -> int:  # noqa: N802
        return 0 if parent is not None and parent.isValid() else len(self.eintraege)

    def columnCount(self, parent: QModelIndex | None = None) -> int:  # noqa: N802
        return 4

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            e = self.eintraege[index.row()]
            return (e.begriff, e.klasse, e.angaben,
                    self.tr("eigene") if e.eigen else e.quelle)[index.column()]
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.titel[section]
        return None

    def flags(self, index):
        basis = super().flags(index)
        if index.column() < 2:
            return basis | Qt.ItemFlag.ItemIsEditable
        return basis

    def setData(self, index, wert, role=Qt.ItemDataRole.EditRole) -> bool:  # noqa: N802
        if role != Qt.ItemDataRole.EditRole or not index.isValid():
            return False
        e = self.eintraege[index.row()]
        neu = self.seite.aendere(e, index.column(), str(wert))
        if neu is None:
            return False
        self.eintraege[index.row()] = neu
        links, rechts = self.index(index.row(), 0), self.index(index.row(), 3)
        self.dataChanged.emit(links, rechts)
        return True


class IdentifikatorenSeite(QWidget):
    def __init__(self, sitzung: Sitzung, eltern: QWidget | None = None) -> None:
        super().__init__(eltern)
        self.sitzung = sitzung
        self.synchron = False
        self._version = -1
        aussen = QVBoxLayout(self)

        kopf = QLabel(self.tr(
            "Identifikatoren außerhalb der Patientendaten: Kliniken, Berufe, Studien, Titel"
            " und Ihre eigenen (Arbeitgeber, Vereine, Einrichtungen). Wie die Patientendaten"
            " werden sie standardmäßig geschwärzt; in der Sichtprüfung lässt sich jeder Treffer"
            " verwerfen. Kliniken und eigene Einträge zählen überall, Berufe nur mit Hinweis"
            " wie „arbeitet als“ oder „von Beruf“, Studienkürzel nur neben „Studie“"
            " (Registernummern wie NCT immer), Titel nur vor oder nach einem erkannten Namen."
            " Der Erwerbsstatus (berentet, arbeitslos) bleibt nach der GeMTeX-Leitlinie stehen."))
        kopf.setWordWrap(True)
        aussen.addWidget(kopf)

        filter_ = QHBoxLayout()
        self.suche = QLineEdit()
        self.suche.setPlaceholderText(self.tr("Suchen …"))
        self.suche.textChanged.connect(self._filtern)
        self.klassen_filter = QComboBox()
        self.klassen_filter.currentIndexChanged.connect(lambda _i: self.aktualisiere(True))
        self.anzahl = QLabel("")
        filter_.addWidget(self.suche, 2)
        filter_.addWidget(QLabel(self.tr("Klasse")))
        filter_.addWidget(self.klassen_filter, 1)
        filter_.addWidget(self.anzahl)
        aussen.addLayout(filter_)

        self.modell = IdentifikatorenModell(self)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.modell)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.proxy.setFilterKeyColumn(-1)
        self.proxy.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.ansicht = QTableView()
        self.ansicht.setModel(self.proxy)
        self.ansicht.setSortingEnabled(True)
        self.ansicht.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.ansicht.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.ansicht.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        kopfzeile = self.ansicht.horizontalHeader()
        kopfzeile.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for spalte in (1, 2, 3):
            kopfzeile.setSectionResizeMode(spalte, QHeaderView.ResizeMode.ResizeToContents)
        self.ansicht.verticalHeader().setVisible(False)
        aussen.addWidget(self.ansicht, 1)

        neu = QHBoxLayout()
        self.neuer_begriff = QLineEdit()
        self.neuer_begriff.setPlaceholderText(self.tr("Neuer Identifikator (auch mehrere Wörter)"))
        self.neuer_begriff.returnPressed.connect(self.hinzufuegen)
        self.neue_klasse = QComboBox()
        self.neue_klasse.setEditable(True)
        self.neue_klasse.addItems(list(identmodul.KLASSEN))
        self.neue_klasse.setCurrentText(identmodul.KLASSE_NUTZER)
        knopf_neu = QPushButton(self.tr("Hinzufügen"))
        knopf_neu.clicked.connect(self.hinzufuegen)
        neu.addWidget(self.neuer_begriff, 2)
        neu.addWidget(self.neue_klasse, 1)
        neu.addWidget(knopf_neu)
        aussen.addLayout(neu)

        unten = QHBoxLayout()
        loeschen = QPushButton(self.tr("Ausgewählte löschen"))
        loeschen.clicked.connect(self.loeschen)
        export = QPushButton(self.tr("CSV exportieren …"))
        export.clicked.connect(self.exportiere_waehlen)
        import_ = QPushButton(self.tr("CSV oder Liste importieren …"))
        import_.setToolTip(self.tr(
            "CSV (begriff,klasse; Kopfzeile optional; Komma, Semikolon oder Tabulator) oder"
            " eine Liste mit einem Identifikator je Zeile"))
        import_.clicked.connect(self.importiere_waehlen)
        self.wiederherstellen_knopf = QPushButton(self.tr("Gelöschte wiederherstellen"))
        self.wiederherstellen_knopf.setToolTip(self.tr(
            "Gelöschte mitgelieferte Einträge zurückholen; selbst geladene Zusatzdaten"
            " (Kliniken Österreich) bitte in „11 Datenbank“ neu laden"))
        self.wiederherstellen_knopf.clicked.connect(self.wiederherstellen)
        neu_laden = QPushButton(self.tr("Aktualisieren"))
        neu_laden.clicked.connect(lambda: self.aktualisiere(True))
        self.status = QLabel("")
        self.status.setWordWrap(True)
        for w in (loeschen, export, import_, self.wiederherstellen_knopf, neu_laden):
            unten.addWidget(w)
        unten.addWidget(self.status, 1)
        aussen.addLayout(unten)

    # -- data ----------------------------------------------------------------------------------
    def _identifikatoren(self) -> identmodul.Identifikatoren:
        return identmodul.Identifikatoren()

    def aktualisiere(self, erzwingen: bool = False) -> int:
        """Reload rows when the table changed (or when asked); returns the row count."""
        with self._identifikatoren() as ident:
            version = ident.version()
            if not erzwingen and version == self._version:
                return self.modell.rowCount()
            klasse = self.klassen_filter.currentData() or ""
            eintraege = ident.alle(klasse)
            klassen = ident.klassen()
            self._gesamt = ident.anzahl()
            geloescht = ident.geloescht()
        self._version = version
        self.modell.lade(eintraege)
        self._klassen_fuellen(klassen)
        self.wiederherstellen_knopf.setEnabled(geloescht > 0)
        self._filtern()
        return len(eintraege)

    def _klassen_fuellen(self, klassen: list[tuple[str, int]]) -> None:
        gewaehlt = self.klassen_filter.currentData() or ""
        self.klassen_filter.blockSignals(True)
        self.klassen_filter.clear()
        self.klassen_filter.addItem(self.tr("alle"), "")
        for k, n in klassen:
            self.klassen_filter.addItem(f"{k} ({n})", k)
        i = self.klassen_filter.findData(gewaehlt)
        self.klassen_filter.setCurrentIndex(max(0, i))
        self.klassen_filter.blockSignals(False)
        aktuell = self.neue_klasse.currentText() or identmodul.KLASSE_NUTZER
        self.neue_klasse.clear()
        self.neue_klasse.addItems(list(dict.fromkeys(
            list(identmodul.KLASSEN) + [k for k, _n in klassen])))
        self.neue_klasse.setCurrentText(aktuell)

    def _filtern(self) -> None:
        self.proxy.setFilterFixedString(self.suche.text().strip())
        self.anzahl.setText(self.tr("{n} von {gesamt} Einträgen").format(
            n=self.proxy.rowCount(), gesamt=getattr(self, "_gesamt", self.modell.rowCount())))

    def _melde(self, titel: str, text: str) -> None:
        if self.synchron:
            raise RuntimeError(text)
        QMessageBox.information(self, titel, text)

    def ausgewaehlte(self) -> list[identmodul.Eintrag]:
        gewaehlt = self.ansicht.selectionModel().selectedRows()
        zeilen = {self.proxy.mapToSource(i).row() for i in gewaehlt}
        return [self.modell.eintraege[r] for r in sorted(zeilen)]

    def _geaendert(self) -> None:
        identmodul.standard.cache_clear()
        with self._identifikatoren() as ident:
            self._version = ident.version()
            self._gesamt = ident.anzahl()

    # -- editing -----------------------------------------------------------------------------------
    def aendere(self, eintrag: identmodul.Eintrag, spalte: int,
                wert: str) -> identmodul.Eintrag | None:
        """Change term (column 0) or class (column 1); returns the row as it is now."""
        with self._identifikatoren() as ident:
            neu = ident.aendere(eintrag.schluessel, begriff=wert if spalte == 0 else None,
                                klasse=wert if spalte == 1 else None)
        if neu is not None:
            self._geaendert()
            self.status.setText(self.tr("Geändert: {begriff}").format(begriff=neu.begriff))
        else:
            self.status.setText(self.tr(
                "Nicht geändert: {begriff} (zu kurz oder schon vorhanden)").format(begriff=wert))
        return neu

    def hinzufuegen(self, begriff: str | None = None, klasse: str | None = None) -> int:
        begriff = (begriff if begriff is not None else self.neuer_begriff.text()).strip()
        klasse = (klasse if klasse is not None else self.neue_klasse.currentText()).strip()
        if len(begriff) < identmodul.MIN_LAENGE:
            self.status.setText(self.tr("Identifikatoren haben mindestens drei Zeichen."))
            return 0
        with self._identifikatoren() as ident:
            neu = ident.fuege_hinzu([begriff], klasse or identmodul.KLASSE_NUTZER)
        identmodul.standard.cache_clear()
        self.status.setText(self.tr("Hinzugefügt: {begriff}").format(begriff=begriff) if neu
                            else self.tr("Schon vorhanden: {begriff}").format(begriff=begriff))
        self.neuer_begriff.clear()
        self.aktualisiere(True)
        return neu

    def loeschen(self, eintraege: list[identmodul.Eintrag] | None = None) -> int:
        eintraege = eintraege if eintraege is not None else self.ausgewaehlte()
        if not eintraege:
            self.status.setText(self.tr("Nichts ausgewählt."))
            return 0
        if not self.synchron and QMessageBox.question(
                self, self.tr("Löschen"),
                self.tr("{n} Identifikator(en) löschen? Mitgelieferte Einträge bleiben auch nach"
                        " Updates gelöscht, bis Sie sie wiederherstellen.").format(
                            n=len(eintraege))) != QMessageBox.StandardButton.Yes:
            return 0
        with self._identifikatoren() as ident:
            n = ident.entferne(e.schluessel for e in eintraege)
        identmodul.standard.cache_clear()
        self.status.setText(self.tr("{n} Identifikator(en) gelöscht.").format(n=n))
        self.aktualisiere(True)
        return n

    def wiederherstellen(self) -> int:
        with self._identifikatoren() as ident:
            n = ident.wiederherstellen()
        identmodul.standard.cache_clear()
        self.status.setText(self.tr("{n} gelöschte Einträge wiederhergestellt.").format(n=n))
        self.aktualisiere(True)
        return n

    # -- CSV ---------------------------------------------------------------------------------------
    def exportiere_waehlen(self) -> None:
        pfad, _ = QFileDialog.getSaveFileName(self, self.tr("Identifikatoren als CSV"),
                                              "identifikatoren.csv", self.tr("CSV (*.csv)"))
        if pfad:
            self.exportiere_csv(Path(pfad))

    def exportiere_csv(self, pfad: Path) -> int:
        klasse = self.klassen_filter.currentData() or ""
        with self._identifikatoren() as ident:
            n = ident.exportiere_csv(pfad, klasse)
        self.status.setText(self.tr("{n} Einträge nach {pfad} geschrieben.").format(
            n=n, pfad=pfad))
        return n

    def importiere_waehlen(self) -> None:
        pfad, _ = QFileDialog.getOpenFileName(
            self, self.tr("Identifikatoren importieren"), "",
            self.tr("CSV und Listen (*.csv *.txt);;Alle Dateien (*)"))
        if pfad:
            self.importiere_datei(Path(pfad))

    def importiere_datei(self, pfad: Path, klasse: str | None = None) -> int:
        klasse = (klasse if klasse is not None
                  else self.neue_klasse.currentText()).strip() or identmodul.KLASSE_NUTZER
        try:
            with self._identifikatoren() as ident:
                neu, gelesen = ident.importiere_csv(pfad, klasse)
        except OSError as e:
            self._melde(self.tr("Identifikatoren"), str(e))
            return 0
        identmodul.standard.cache_clear()
        self.status.setText(self.tr(
            "{neu} neue von {gelesen} Identifikatoren aus {name} übernommen.").format(
                neu=neu, gelesen=gelesen, name=pfad.name))
        self.aktualisiere(True)
        return neu
