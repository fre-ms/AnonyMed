"""Step 2: the protected vocabulary as a sortable, editable table.

Everything NER and the fuzzy search must leave alone, in one table of the pseudonym
database: term and category.  Search, sort by any column, edit a cell, add and delete
terms, exchange CSV; the bundled lists can be loaded again."""

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

from pseudonymed.erkennung import fachbegriffe as fachmodul
from pseudonymed.erkennung import vokabular as vokmodul
from pseudonymed.gui.sitzung import Sitzung


class VokabularModell(QAbstractTableModel):
    def __init__(self, seite: VokabularSeite) -> None:
        super().__init__(seite)
        self.seite = seite
        self.zeilen: list[list[str]] = []
        self.titel = [self.tr("Begriff"), self.tr("Kategorie"), self.tr("Angelegt")]

    def lade(self, zeilen: list[tuple[str, str, str]]) -> None:
        self.beginResetModel()
        self.zeilen = [list(z) for z in zeilen]
        self.endResetModel()

    def rowCount(self, parent: QModelIndex | None = None) -> int:  # noqa: N802
        return 0 if parent is not None and parent.isValid() else len(self.zeilen)

    def columnCount(self, parent: QModelIndex | None = None) -> int:  # noqa: N802
        return 3

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            wert = self.zeilen[index.row()][index.column()]
            return wert[:16].replace("T", " ") if index.column() == 2 else wert
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
        if not self.seite.aendere(self.zeilen[index.row()][0], index.column(), str(wert)):
            return False
        self.zeilen[index.row()][index.column()] = " ".join(str(wert).split())
        self.dataChanged.emit(index, index)
        return True


class VokabularSeite(QWidget):
    def __init__(self, sitzung: Sitzung, eltern: QWidget | None = None) -> None:
        super().__init__(eltern)
        self.sitzung = sitzung
        self.synchron = False
        self._version = -1
        aussen = QVBoxLayout(self)

        kopf = QLabel(self.tr(
            "Begriffe, die NER und die unscharfe Suche nie schwärzen dürfen: Fachbegriffe,"
            " Körperteile, Befundwörter, Eponyme und Ihre eigenen. Spaltenkopf anklicken"
            " sortiert; Doppelklick ändert Begriff oder Kategorie."))
        kopf.setWordWrap(True)
        aussen.addWidget(kopf)

        filter_ = QHBoxLayout()
        self.suche = QLineEdit()
        self.suche.setPlaceholderText(self.tr("Suchen …"))
        self.suche.textChanged.connect(self._filtern)
        self.kategorie_filter = QComboBox()
        self.kategorie_filter.currentIndexChanged.connect(lambda _i: self.aktualisiere(True))
        self.anzahl = QLabel("")
        filter_.addWidget(self.suche, 2)
        filter_.addWidget(QLabel(self.tr("Kategorie")))
        filter_.addWidget(self.kategorie_filter, 1)
        filter_.addWidget(self.anzahl)
        aussen.addLayout(filter_)

        self.modell = VokabularModell(self)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.modell)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.proxy.setFilterKeyColumn(0)
        self.proxy.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.ansicht = QTableView()
        self.ansicht.setModel(self.proxy)
        self.ansicht.setSortingEnabled(True)
        self.ansicht.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.ansicht.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.ansicht.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.ansicht.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.ansicht.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents)
        self.ansicht.verticalHeader().setVisible(False)
        aussen.addWidget(self.ansicht, 1)

        neu = QHBoxLayout()
        self.neuer_begriff = QLineEdit()
        self.neuer_begriff.setPlaceholderText(self.tr("Neuer Begriff (auch mehrere Wörter)"))
        self.neuer_begriff.returnPressed.connect(self.hinzufuegen)
        self.neue_kategorie = QComboBox()
        self.neue_kategorie.setEditable(True)
        knopf_neu = QPushButton(self.tr("Hinzufügen"))
        knopf_neu.clicked.connect(self.hinzufuegen)
        neu.addWidget(self.neuer_begriff, 2)
        neu.addWidget(self.neue_kategorie, 1)
        neu.addWidget(knopf_neu)
        aussen.addLayout(neu)

        unten = QHBoxLayout()
        loeschen = QPushButton(self.tr("Ausgewählte löschen"))
        loeschen.clicked.connect(self.loeschen)
        export = QPushButton(self.tr("CSV exportieren …"))
        export.clicked.connect(self.exportiere_waehlen)
        import_ = QPushButton(self.tr("CSV oder Liste importieren …"))
        import_.setToolTip(self.tr(
            "CSV (begriff,kategorie; Kopfzeile optional; Komma, Semikolon oder Tabulator) oder"
            " eine Liste mit einem Begriff je Zeile, etwa die beim Export geschriebene"
            " .vokabular.txt"))
        import_.clicked.connect(self.importiere_waehlen)
        grund = QPushButton(self.tr("Mitgelieferte Listen erneut einspielen"))
        grund.setToolTip(self.tr("Fehlende Begriffe der mitgelieferten Listen wieder aufnehmen"))
        grund.clicked.connect(self.grundbestand_einspielen)
        neu_laden = QPushButton(self.tr("Aktualisieren"))
        neu_laden.clicked.connect(lambda: self.aktualisiere(True))
        self.status = QLabel("")
        self.status.setWordWrap(True)
        for w in (loeschen, export, import_, grund, neu_laden):
            unten.addWidget(w)
        unten.addWidget(self.status, 1)
        aussen.addLayout(unten)

    # -- data ----------------------------------------------------------------------------------
    def _vokabular(self) -> vokmodul.Vokabular:
        return vokmodul.Vokabular()

    def aktualisiere(self, erzwingen: bool = False) -> int:
        """Reload rows when the table changed (or when asked); returns the row count."""
        with self._vokabular() as v:
            version = v.version()
            if not erzwingen and version == self._version:
                return self.modell.rowCount()
            kategorie = self.kategorie_filter.currentData() or ""
            zeilen = v.alle(kategorie=kategorie)
            kategorien = v.kategorien()
            gesamt = v.anzahl()
        self._version = version
        self.modell.lade(zeilen)
        self._kategorien_fuellen(kategorien)
        self.anzahl.setText(self.tr("{n} von {gesamt} Begriffen").format(
            n=len(zeilen), gesamt=gesamt))
        self._filtern()
        return len(zeilen)

    def _kategorien_fuellen(self, kategorien: list[tuple[str, int]]) -> None:
        gewaehlt = self.kategorie_filter.currentData() or ""
        self.kategorie_filter.blockSignals(True)
        self.kategorie_filter.clear()
        self.kategorie_filter.addItem(self.tr("alle"), "")
        for k, n in kategorien:
            self.kategorie_filter.addItem(f"{k} ({n})", k)
        i = self.kategorie_filter.findData(gewaehlt)
        self.kategorie_filter.setCurrentIndex(max(0, i))
        self.kategorie_filter.blockSignals(False)
        aktuell = self.neue_kategorie.currentText() or vokmodul.KATEGORIE_NUTZER
        self.neue_kategorie.clear()
        self.neue_kategorie.addItems([k for k, _n in kategorien]
                                     or [vokmodul.KATEGORIE_NUTZER])
        if vokmodul.KATEGORIE_NUTZER not in [k for k, _n in kategorien]:
            self.neue_kategorie.addItem(vokmodul.KATEGORIE_NUTZER)
        self.neue_kategorie.setCurrentText(aktuell)

    def _filtern(self) -> None:
        self.proxy.setFilterFixedString(self.suche.text().strip())
        self.anzahl.setText(self.tr("{n} von {gesamt} Begriffen").format(
            n=self.proxy.rowCount(), gesamt=self.anzahl.text().split()[-2]
            if "von" in self.anzahl.text() else self.modell.rowCount()))

    def _melde(self, titel: str, text: str) -> None:
        if self.synchron:
            raise RuntimeError(text)
        QMessageBox.information(self, titel, text)

    def ausgewaehlte(self) -> list[str]:
        gewaehlt = self.ansicht.selectionModel().selectedRows()
        zeilen = {self.proxy.mapToSource(i).row() for i in gewaehlt}
        return [self.modell.zeilen[r][0] for r in sorted(zeilen)]

    # -- editing -----------------------------------------------------------------------------------
    def aendere(self, begriff: str, spalte: int, wert: str) -> bool:
        with self._vokabular() as v:
            if spalte == 0:
                ok = v.benenne_um(begriff, wert)
            else:
                ok = v.setze_kategorie([begriff], wert) > 0
        if ok:
            fachmodul.standard.cache_clear()
            self.status.setText(self.tr("Geändert: {begriff}").format(begriff=begriff))
        else:
            self.status.setText(self.tr(
                "Nicht geändert: {begriff} (zu kurz oder schon vorhanden)").format(begriff=wert))
        return ok

    def hinzufuegen(self, begriff: str | None = None, kategorie: str | None = None) -> int:
        begriff = (begriff if begriff is not None else self.neuer_begriff.text()).strip()
        kategorie = (kategorie if kategorie is not None
                     else self.neue_kategorie.currentText()).strip()
        if len(begriff) < vokmodul.MIN_LAENGE:
            self.status.setText(self.tr("Begriffe haben mindestens drei Zeichen."))
            return 0
        with self._vokabular() as v:
            neu = v.fuege_hinzu([begriff], kategorie or vokmodul.KATEGORIE_NUTZER)
        fachmodul.standard.cache_clear()
        self.status.setText(self.tr("Hinzugefügt: {begriff}").format(begriff=begriff) if neu
                            else self.tr("Schon vorhanden: {begriff}").format(begriff=begriff))
        self.neuer_begriff.clear()
        self.aktualisiere(True)
        return neu

    def loeschen(self, begriffe: list[str] | None = None) -> int:
        begriffe = begriffe if begriffe is not None else self.ausgewaehlte()
        if not begriffe:
            self.status.setText(self.tr("Nichts ausgewählt."))
            return 0
        if not self.synchron and QMessageBox.question(
                self, self.tr("Löschen"),
                self.tr("{n} Begriff(e) aus dem Vokabular löschen?").format(n=len(begriffe))) \
                != QMessageBox.StandardButton.Yes:
            return 0
        with self._vokabular() as v:
            n = v.entferne(begriffe)
        fachmodul.standard.cache_clear()
        self.status.setText(self.tr("{n} Begriff(e) gelöscht.").format(n=n))
        self.aktualisiere(True)
        return n

    # -- CSV ---------------------------------------------------------------------------------------
    def exportiere_waehlen(self) -> None:
        pfad, _ = QFileDialog.getSaveFileName(self, self.tr("Vokabular als CSV"),
                                              "vokabular.csv", self.tr("CSV (*.csv)"))
        if pfad:
            self.exportiere_csv(Path(pfad))

    def exportiere_csv(self, pfad: Path) -> int:
        kategorie = self.kategorie_filter.currentData() or ""
        with self._vokabular() as v:
            n = v.exportiere_csv(pfad, kategorie)
        self.status.setText(self.tr("{n} Begriffe nach {pfad} geschrieben.").format(
            n=n, pfad=pfad))
        return n

    def importiere_waehlen(self) -> None:
        pfad, _ = QFileDialog.getOpenFileName(
            self, self.tr("Vokabular importieren"), "",
            self.tr("CSV und Listen (*.csv *.txt);;Alle Dateien (*)"))
        if pfad:
            self.importiere_datei(Path(pfad))

    def importiere_datei(self, pfad: Path, kategorie: str | None = None) -> int:
        kategorie = (kategorie if kategorie is not None
                     else self.neue_kategorie.currentText()).strip() or vokmodul.KATEGORIE_NUTZER
        try:
            with self._vokabular() as v:
                neu, gelesen = v.importiere_csv(pfad, kategorie)
        except OSError as e:
            self._melde(self.tr("Vokabular"), str(e))
            return 0
        fachmodul.standard.cache_clear()
        self.status.setText(self.tr("{neu} neue von {gelesen} Begriffen aus {name} übernommen.")
                            .format(neu=neu, gelesen=gelesen, name=pfad.name))
        self.aktualisiere(True)
        return neu

    def grundbestand_einspielen(self) -> int:
        with self._vokabular() as v:
            zaehlung = v.grundbestand()
        fachmodul.standard.cache_clear()
        n = sum(zaehlung.values())
        self.status.setText(self.tr(
            "{n} fehlende Begriffe der mitgelieferten Listen aufgenommen.").format(n=n))
        self.aktualisiere(True)
        return n
