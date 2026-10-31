"""The main window: five steps as tabs, a language menu."""

from __future__ import annotations

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMainWindow, QMessageBox, QTabWidget

from anonymed import WERKZEUG
from anonymed.gui.aufbereiten_seite import AufbereitenSeite
from anonymed.gui.dokumente_seite import DokumenteSeite
from anonymed.gui.export_seite import ExportSeite
from anonymed.gui.patient_seite import PatientSeite
from anonymed.gui.pruefen_seite import PruefenSeite
from anonymed.gui.rueckersetzen_seite import RueckersetzenSeite
from anonymed.gui.sitzung import Sitzung
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
        self.aufbereiten = AufbereitenSeite(self.sitzung)
        self.pruefen = PruefenSeite(self.sitzung)
        self.export = ExportSeite(self.sitzung)
        self.rueckersetzen = RueckersetzenSeite(self.sitzung)
        for seite, titel in ((self.patient, self.tr("1 Patient")),
                             (self.dokumente, self.tr("2 Dokumente")),
                             (self.aufbereiten, self.tr("3 Aufbereiten")),
                             (self.pruefen, self.tr("4 Prüfen")),
                             (self.export, self.tr("5 Exportieren")),
                             (self.rueckersetzen, self.tr("6 Rückersetzen"))):
            self.tabs.addTab(seite, titel)
        self.setCentralWidget(self.tabs)
        self.patient.uebernommen.connect(lambda _p: self.tabs.setCurrentWidget(self.dokumente))
        self.dokumente.geladen.connect(self._nach_laden)
        self.aufbereiten.analysieren_gewuenscht.connect(self.dokumente.analysiere_alle)
        self.dokumente.analysiert.connect(self._nach_analyse)
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

    def _nach_laden(self) -> None:
        """Scans and photos go through the clean-up step first; everything else is
        analysed right away."""
        if any(d.aufzubereiten for d in self.sitzung.dokumente):
            self.aufbereiten.aktualisiere()
            self.tabs.setCurrentWidget(self.aufbereiten)
        else:
            self.dokumente.analysiere_alle()

    def _nach_analyse(self) -> None:
        self.pruefen.aktualisiere()
        self.tabs.setCurrentWidget(self.pruefen)

    def _tab_gewechselt(self, index: int) -> None:
        if self.tabs.widget(index) is self.rueckersetzen:
            self.rueckersetzen.aktualisiere()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.sitzung.schliessen()
        super().closeEvent(event)
