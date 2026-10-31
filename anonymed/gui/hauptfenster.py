"""The main window: five steps as tabs, a language menu."""

from __future__ import annotations

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMainWindow, QMessageBox, QTabWidget

from anonymed import WERKZEUG
from anonymed.gui.analysieren_seite import AnalysierenSeite
from anonymed.gui.dokumente_seite import DokumenteSeite
from anonymed.gui.export_seite import ExportSeite
from anonymed.gui.patient_seite import PatientSeite
from anonymed.gui.pruefen_seite import PruefenSeite
from anonymed.gui.rueckersetzen_seite import RueckersetzenSeite
from anonymed.gui.sitzung import Sitzung
from anonymed.gui.texterkennung_seite import TexterkennungSeite
from anonymed.konfig import lade_einstellungen, speichere_einstellungen


class Hauptfenster(QMainWindow):
    def __init__(self, sitzung: Sitzung | None = None) -> None:
        super().__init__()
        self.sitzung = sitzung or Sitzung()
        self.setWindowTitle(WERKZEUG)
        self.resize(1200, 820)
        self.tabs = QTabWidget()
        self.patient = PatientSeite(self.sitzung)
        self.dokumente = DokumenteSeite(self.sitzung)
        self.texterkennung = TexterkennungSeite(self.sitzung)
        self.texterkennung.laedt = lambda: self.dokumente.laedt
        self.analysieren = AnalysierenSeite(self.sitzung)
        self.analysieren.laedt = lambda: self.dokumente.laedt
        self.pruefen = PruefenSeite(self.sitzung)
        self.export = ExportSeite(self.sitzung)
        self.rueckersetzen = RueckersetzenSeite(self.sitzung)
        for seite, titel in ((self.patient, self.tr("1 Patient")),
                             (self.dokumente, self.tr("2 Dokumente")),
                             (self.texterkennung, self.tr("3 Texterkennung")),
                             (self.analysieren, self.tr("4 Analysieren")),
                             (self.pruefen, self.tr("5 Prüfen")),
                             (self.export, self.tr("6 Exportieren")),
                             (self.rueckersetzen, self.tr("7 Rückersetzen"))):
            self.tabs.addTab(seite, titel)
        self.setCentralWidget(self.tabs)
        self.patient.uebernommen.connect(lambda _p: self.tabs.setCurrentWidget(self.dokumente))
        self.dokumente.weiter.connect(lambda: self.tabs.setCurrentWidget(self.texterkennung))
        self.dokumente.geladen.connect(self.texterkennung.aktualisiere)
        self.texterkennung.weiter.connect(lambda: self.tabs.setCurrentWidget(self.analysieren))
        self.texterkennung.gelesen.connect(self.analysieren.aktualisiere)
        self.analysieren.analysiert.connect(self._nach_analyse)
        self.tabs.currentChanged.connect(self._tab_gewechselt)
        self.statusBar().showMessage(self.tr(
            "Alles bleibt auf diesem Rechner; nur das Adressbuch wird über das Netz gelesen."))
        self._sprachmenue()

    def _sprachmenue(self) -> None:
        menue = self.menuBar().addMenu(self.tr("Sprache"))
        for code, name in (("de", "Deutsch"), ("en", "English")):
            aktion = QAction(name, self)
            aktion.triggered.connect(lambda _c=False, code=code: self.sprache_waehlen(code))
            menue.addAction(aktion)

    def sprache_waehlen(self, code: str) -> None:
        e = lade_einstellungen()
        e.gui_sprache = code
        speichere_einstellungen(e)
        QMessageBox.information(self, self.tr("Sprache"), self.tr(
            "Die Sprache gilt ab dem nächsten Start von AnonyMed."))

    def _nach_analyse(self) -> None:
        self.dokumente.aktualisiere()
        self.pruefen.aktualisiere()
        self.tabs.setCurrentWidget(self.pruefen)

    def _tab_gewechselt(self, index: int) -> None:
        if self.tabs.widget(index) is self.rueckersetzen:
            self.rueckersetzen.aktualisiere()
        elif self.tabs.widget(index) is self.analysieren:
            self.analysieren.aktualisiere()
        elif self.tabs.widget(index) is self.texterkennung:
            self.texterkennung.aktualisiere()
        elif self.tabs.widget(index) is self.export:
            self.export.aktualisiere()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.sitzung.schliessen()
        super().closeEvent(event)
