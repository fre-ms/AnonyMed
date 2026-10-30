"""The main window: five steps as tabs."""

from __future__ import annotations

from PySide6.QtWidgets import QMainWindow, QTabWidget

from anonymed import WERKZEUG
from anonymed.gui.dokumente_seite import DokumenteSeite
from anonymed.gui.export_seite import ExportSeite
from anonymed.gui.patient_seite import PatientSeite
from anonymed.gui.pruefen_seite import PruefenSeite
from anonymed.gui.rueckersetzen_seite import RueckersetzenSeite
from anonymed.gui.sitzung import Sitzung


class Hauptfenster(QMainWindow):
    def __init__(self, sitzung: Sitzung | None = None) -> None:
        super().__init__()
        self.sitzung = sitzung or Sitzung()
        self.setWindowTitle(WERKZEUG)
        self.resize(1200, 820)
        self.tabs = QTabWidget()
        self.patient = PatientSeite(self.sitzung)
        self.dokumente = DokumenteSeite(self.sitzung)
        self.pruefen = PruefenSeite(self.sitzung)
        self.export = ExportSeite(self.sitzung)
        self.rueckersetzen = RueckersetzenSeite(self.sitzung)
        for seite, titel in ((self.patient, "1 Patient"), (self.dokumente, "2 Dokumente"),
                             (self.pruefen, "3 Prüfen"), (self.export, "4 Exportieren"),
                             (self.rueckersetzen, "5 Rückersetzen")):
            self.tabs.addTab(seite, titel)
        self.setCentralWidget(self.tabs)
        self.patient.uebernommen.connect(lambda _p: self.tabs.setCurrentWidget(self.dokumente))
        self.dokumente.analysiert.connect(self._nach_analyse)
        self.tabs.currentChanged.connect(self._tab_gewechselt)
        self.statusBar().showMessage("Alles bleibt auf diesem Rechner; nur das Adressbuch wird"
                                     " über das Netz gelesen.")

    def _nach_analyse(self) -> None:
        self.pruefen.aktualisiere()
        self.tabs.setCurrentWidget(self.pruefen)

    def _tab_gewechselt(self, index: int) -> None:
        if self.tabs.widget(index) is self.rueckersetzen:
            self.rueckersetzen.aktualisiere()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.sitzung.schliessen()
        super().closeEvent(event)
