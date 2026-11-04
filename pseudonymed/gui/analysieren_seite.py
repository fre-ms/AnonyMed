"""Step 3: how the documents are read, then the analysis itself with visible progress."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pseudonymed.erkennung import fachbegriffe as fachmodul
from pseudonymed.erkennung import ner as nermodul
from pseudonymed.gui.arbeiter import Arbeiter
from pseudonymed.gui.sitzung import Dokument, Sitzung
from pseudonymed.konfig import lade_einstellungen, speichere_einstellungen
from pseudonymed.pseudonyme import pgp as pgpmodul
from pseudonymed.pseudonyme.speicher import UnsichererAbgleich


class AnalysierenSeite(QWidget):
    analysiert = Signal()

    def __init__(self, sitzung: Sitzung, eltern: QWidget | None = None) -> None:
        super().__init__(eltern)
        self.sitzung = sitzung
        self.synchron = False
        self.laedt = lambda: False  # the main window tells whether documents are still loading
        self._arbeiter: Arbeiter | None = None
        self._warteschlange: list[Dokument] = []
        self._gesamt = 0
        aussen = QVBoxLayout(self)

        optionen = QGroupBox(self.tr("Erkennung der Identifikatoren"))
        form = QFormLayout(optionen)
        self.datum = QComboBox()
        self.datum.addItem(self.tr("Geburtsjahr [geb. 1990]"), "jahr")
        self.datum.addItem(self.tr("Alter [Alter 36]"), "alter")
        form.addRow(self.tr("Geburtsdatum als"), self.datum)
        self.codes = QCheckBox(self.tr("Barcodes und QR-Codes schwärzen"))
        self.codes.setChecked(True)
        form.addRow(self.codes)
        self.unlesbar = QCheckBox(self.tr("Unlesbare OCR-Zeilen schwärzen"))
        self.unlesbar.setChecked(True)
        form.addRow(self.unlesbar)
        self.fachbegriffe = QCheckBox(
            self.tr("Fachbegriffe markieren und vor NER und unscharfer OCR-Suche schützen"))
        self.fachbegriffe.setChecked(True)
        form.addRow(self.fachbegriffe)
        self.identifikatoren = QCheckBox(self.tr(
            "Identifikatoren schwärzen: Kliniken, Berufe, Studien, Titel"
            " (siehe „2 Identifikatoren“)"))
        self.identifikatoren.setChecked(True)
        form.addRow(self.identifikatoren)
        self.medikamente = QCheckBox(self.tr("Medikamente schwärzen"))
        self.medikamente.setToolTip(self.tr(
            "Medikamentennamen aus dem Lexikon (Wirkstoffe, einige Handelsnamen) und alle"
            " Begriffe der Kategorie „Medikament“ in „3 Fachvokabular“. Standardmäßig aus:"
            " Medikamente gehören zum medizinischen Inhalt."))
        form.addRow(self.medikamente)
        self.laborwerte = QCheckBox(self.tr("Laborwerte schwärzen (Name und Messwert)"))
        self.laborwerte.setToolTip(self.tr(
            "Ein Laborname zusammen mit dem Messwert direkt dahinter, etwa „TSH 2,3 mU/l“;"
            " ein Laborname ohne Wert bleibt stehen. Eigene Namen in „3 Fachvokabular“ mit der"
            " Kategorie „Laborwert“. Standardmäßig aus: Laborwerte gehören zum medizinischen"
            " Inhalt."))
        form.addRow(self.laborwerte)
        self.unscharf = QCheckBox(
            self.tr("Textdateien: abweichend geschriebene Namen unscharf suchen (Transkripte)"))
        self.unscharf.setChecked(True)
        form.addRow(self.unscharf)
        self.ner = QCheckBox(self.tr("Unbekannte Personen und Orte per NER suchen (rät)"))
        self.ner.setEnabled(nermodul.verfuegbar())
        self.ner.setChecked(nermodul.verfuegbar())
        if not nermodul.verfuegbar():
            self.ner.setToolTip(
                self.tr("Extra 'ner' und ein deutsches spaCy-Modell installieren."))
        form.addRow(self.ner)
        aussen.addWidget(optionen)

        vokabular = QGroupBox(self.tr("Fachvokabular (geschützt vor NER und unscharfer Suche)"))
        vz = QHBoxLayout(vokabular)
        self.vokabular_info = QLabel("")
        self.vokabular_info.setWordWrap(True)
        vz.addWidget(self.vokabular_info, 1)
        aussen.addWidget(vokabular)

        weitergabe = QGroupBox(self.tr("Empfänger (Pseudonyme zum Weitergeben)"))
        wf = QFormLayout(weitergabe)
        self.weitergabe = QCheckBox(self.tr(
            "Für einen Empfänger pseudonymisieren: Einmalschlüssel, die Zuordnung steht nur in"
            " einer verschlüsselten Kapsel für den Empfänger; lokal wird nichts gespeichert"))
        wf.addRow(self.weitergabe)
        zeile = QHBoxLayout()
        self.empfaenger_quelle = QLineEdit()
        self.empfaenger_quelle.setPlaceholderText(self.tr(
            "Öffentlicher Schlüssel: Datei (.asc) oder https-Adresse"))
        waehlen = QPushButton("…")
        waehlen.clicked.connect(self.empfaenger_waehlen)
        self.empfaenger_knopf = QPushButton(self.tr("Schlüssel prüfen"))
        self.empfaenger_knopf.clicked.connect(self.empfaenger_pruefen)
        zeile.addWidget(self.empfaenger_quelle, 1)
        zeile.addWidget(waehlen)
        zeile.addWidget(self.empfaenger_knopf)
        wf.addRow(zeile)
        self.empfaenger_info = QLabel("")
        self.empfaenger_info.setWordWrap(True)
        self.empfaenger_info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        wf.addRow(self.empfaenger_info)
        self.zertifikat: pgpmodul.Zertifikat | None = None
        einstellungen = lade_einstellungen()
        self.empfaenger_quelle.setText(einstellungen.empfaenger_schluessel)
        if not pgpmodul.verfuegbar():
            weitergabe.setEnabled(False)
            weitergabe.setToolTip(self.tr("Extra 'pgp' (pysequoia) oder GnuPG installieren."))
        aussen.addWidget(weitergabe)

        aussen.addWidget(QLabel(self.tr("Dokumente dieser Sitzung:")))
        self.liste = QListWidget()
        aussen.addWidget(self.liste, 1)
        self.fortschritt = QProgressBar()
        self.fortschritt.setRange(0, 1)
        self.fortschritt.setValue(0)
        aussen.addWidget(self.fortschritt)

        unten = QHBoxLayout()
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.knopf = QPushButton(self.tr("Analysieren"))
        self.knopf.setDefault(True)
        self.knopf.clicked.connect(self.analysieren)
        unten.addWidget(self.status, 1)
        unten.addWidget(self.knopf)
        aussen.addLayout(unten)

    # -- state -----------------------------------------------------------------------------------
    def aktualisiere(self) -> None:
        self.liste.clear()
        for d in self.sitzung.dokumente:
            self.liste.addItem(f"{d.pfad.name}  –  {d.zustand}")
        self._vokabular_anzeigen()

    # -- vocabulary --------------------------------------------------------------------------------
    def _vokabular_anzeigen(self) -> None:
        fb = fachmodul.standard()
        quellen = ", ".join(f"{name} {n}" for name, n in fb.quellen)
        self.vokabular_info.setText(self.tr(
            "{gesamt} geschützte Begriffe ({quellen}). Ansehen, ergänzen und importieren unter"
            " „3 Fachvokabular“.").format(gesamt=fb.anzahl, quellen=quellen))

    def _optionen_uebernehmen(self) -> None:
        o = self.sitzung.optionen
        o.datumsform = self.datum.currentData()
        o.codes = self.codes.isChecked()
        o.unlesbar = "schwaerzen" if self.unlesbar.isChecked() else "lassen"
        o.ner = self.ner.isChecked()
        o.fachbegriffe = self.fachbegriffe.isChecked()
        o.identifikatoren = self.identifikatoren.isChecked()
        o.medikamente_schwaerzen = self.medikamente.isChecked()
        o.laborwerte_schwaerzen = self.laborwerte.isChecked()
        o.unscharf_text = self.unscharf.isChecked()

    def _melde(self, titel: str, text: str) -> None:
        if self.synchron:
            raise RuntimeError(text)
        QMessageBox.information(self, titel, text)

    # -- recipient ---------------------------------------------------------------------------------
    def empfaenger_waehlen(self) -> None:
        pfad, _ = QFileDialog.getOpenFileName(
            self, self.tr("Öffentlicher Schlüssel des Empfängers"), "",
            self.tr("OpenPGP-Schlüssel (*.asc *.gpg *.pgp);;Alle Dateien (*)"))
        if pfad:
            self.empfaenger_quelle.setText(pfad)
            self.empfaenger_pruefen()

    def empfaenger_pruefen(self) -> pgpmodul.Zertifikat | None:
        """Load the key, show fingerprint and names, pin the fingerprint in the settings
        and warn when a key at the same address changed."""
        quelle = self.empfaenger_quelle.text().strip()
        if not quelle:
            self.empfaenger_info.setText(self.tr("Bitte Schlüsseldatei oder Adresse angeben."))
            self.zertifikat = None
            return None
        try:
            z = pgpmodul.lade_zertifikat(quelle)
        except pgpmodul.PGPFehler as e:
            self.zertifikat = None
            self.empfaenger_info.setText(self.tr("Schlüssel nicht brauchbar: {fehler}").format(
                fehler=e))
            return None
        e = lade_einstellungen()
        warnung = ""
        if e.empfaenger_schluessel == quelle and e.empfaenger_fingerabdruck \
                and e.empfaenger_fingerabdruck != z.fingerabdruck:
            warnung = self.tr(" ACHTUNG: Der Fingerabdruck weicht vom früher geprüften ab"
                              " ({alt}). Nur weiterverwenden, wenn der Empfänger den neuen"
                              " Schlüssel bestätigt hat.").format(alt=e.empfaenger_fingerabdruck)
        e.empfaenger_schluessel, e.empfaenger_fingerabdruck = quelle, z.fingerabdruck
        try:
            speichere_einstellungen(e)
        except OSError:
            pass
        self.zertifikat = z
        self.empfaenger_info.setText(self.tr(
            "Empfänger: {beschreibung}. Fingerabdruck mit dem Empfänger auf einem zweiten Weg"
            " abgleichen (Webseite, Briefkopf, Telefon).").format(beschreibung=z.beschreibung())
            + warnung)
        return z

    # -- analysis ----------------------------------------------------------------------------------
    def analysieren(self) -> None:
        if self.weitergabe.isChecked():
            if self.zertifikat is None and self.empfaenger_pruefen() is None:
                self._melde(self.tr("Empfänger"), self.tr(
                    "Für einen Empfänger braucht es dessen öffentlichen Schlüssel."))
                return
            if self.sitzung.empfaenger is None or \
                    self.sitzung.empfaenger.fingerabdruck != self.zertifikat.fingerabdruck:
                self.sitzung.setze_empfaenger(self.zertifikat)
        elif self.sitzung.empfaenger is not None:
            self.sitzung.setze_empfaenger(None)
        if self.sitzung.patient is None:
            self._melde(self.tr("Analysieren"), self.tr("Bitte zuerst einen Patienten übernehmen."))
            return
        if not self.sitzung.dokumente:
            self._melde(self.tr("Analysieren"), self.tr("Bitte zuerst Dokumente hinzufügen."))
            return
        if self.laedt():
            self._melde(self.tr("Analysieren"),
                        self.tr("Die Dokumente werden noch geladen; bitte einen Moment warten."))
            return
        self._optionen_uebernehmen()
        self._warteschlange = [d for d in self.sitzung.dokumente
                               if d.vorbereitung is None or d.vorbereitung.abgeschlossen]
        self._gesamt = len(self._warteschlange)
        self.fortschritt.setRange(0, max(1, self._gesamt))
        self.fortschritt.setValue(0)
        self._naechstes()

    def _naechstes(self) -> None:
        self.aktualisiere()
        if not self._warteschlange:
            self.knopf.setEnabled(True)
            self.fortschritt.setValue(self.fortschritt.maximum())
            self.status.setText(self.tr("Fertig: {n} Dokument(e) analysiert.").format(
                n=self._gesamt))
            self.analysiert.emit()
            return
        d = self._warteschlange[0]
        self.knopf.setEnabled(False)
        nummer = self._gesamt - len(self._warteschlange) + 1
        self.fortschritt.setValue(nummer - 1)
        self.status.setText(self.tr("Analysiere {nummer} von {gesamt}: {name} …").format(
            nummer=nummer, gesamt=self._gesamt, name=d.pfad.name))
        if self.synchron:
            try:
                self.sitzung.analysiere(d)
            except Exception as e:  # noqa: BLE001 - shown to the user
                self._fehler(e, d)
                return
            self._fertig(d)
            return
        self._arbeiter = Arbeiter(lambda: self.sitzung.analysiere(d), self)
        self._arbeiter.fertig.connect(lambda _r, d=d: self._fertig(d))
        self._arbeiter.fehler.connect(lambda e, d=d: self._fehler(e, d))
        self._arbeiter.start()

    def _fertig(self, d: Dokument) -> None:
        if self._warteschlange and self._warteschlange[0] is d:
            self._warteschlange.pop(0)
        self._naechstes()

    def _fehler(self, e: Exception, d: Dokument) -> None:
        if self.synchron:  # tests: never block on a modal dialog
            raise e
        if isinstance(e, UnsichererAbgleich):
            entscheidung = self.unsicher_dialog(e)
            if entscheidung is None:
                self._warteschlange.clear()
                self.knopf.setEnabled(True)
                self.status.setText(self.tr("Abgebrochen."))
                return
            self.sitzung.abgleich = entscheidung
            self._naechstes()  # same document again
            return
        d.fehler = str(e)
        QMessageBox.warning(self, d.pfad.name, str(e))
        if self._warteschlange and self._warteschlange[0] is d:
            self._warteschlange.pop(0)
        self._naechstes()

    def unsicher_dialog(self, e: UnsichererAbgleich) -> str | None:
        """Ask whether the patient is one of the similar stored people; None = cancel."""
        zeilen = [self.tr("{kennung}: {nachname}, {vorname}, geb. {geb} (Übereinstimmung {score})")
                  .format(kennung=k.kennung, nachname=k.nachname, vorname=k.vorname,
                          geb=k.geburtsdatum or "–", score=f"{k.score:.2f}") for k in e.kandidaten]
        box = QMessageBox(self)
        box.setWindowTitle(self.tr("Ähnliche Person im Pseudonymspeicher"))
        box.setText(self.tr("Diese Person ähnelt einem vorhandenen Eintrag. Dieselbe Person?"))
        box.setInformativeText("\n".join(zeilen))
        gleich = box.addButton(self.tr("Ja, {kennung}").format(kennung=e.kandidaten[0].kennung),
                               QMessageBox.ButtonRole.YesRole)
        neu = box.addButton(self.tr("Nein, neue Person"), QMessageBox.ButtonRole.NoRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() is gleich:
            return f"gleich:{e.kandidaten[0].kennung}"
        if box.clickedButton() is neu:
            return "neu"
        return None
