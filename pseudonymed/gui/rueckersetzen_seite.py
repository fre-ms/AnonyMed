"""Step 5: put the clear data back into the model's text."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pseudonymed.gui.sitzung import Sitzung
from pseudonymed.konfig import lade_einstellungen, speichere_einstellungen
from pseudonymed.pseudonyme import kapsel as kapselmodul
from pseudonymed.pseudonyme import pgp as pgpmodul
from pseudonymed.pseudonyme.abgleich import abgleichen
from pseudonymed.pseudonyme.kapsel import Kapsel
from pseudonymed.pseudonyme.rueckersetzung import rueckersetzen


class RueckersetzenSeite(QWidget):
    def __init__(self, sitzung: Sitzung, eltern: QWidget | None = None) -> None:
        super().__init__(eltern)
        self.sitzung = sitzung
        self.synchron = False
        self.kapsel: Kapsel | None = None
        self.passphrase: str | None = None  # tests set it; the app asks
        aussen = QVBoxLayout(self)

        kapselgruppe = QGroupBox(self.tr("Schlüsselkapsel eines Absenders"))
        kz = QVBoxLayout(kapselgruppe)
        zeile = QHBoxLayout()
        self.kapsel_knopf = QPushButton(self.tr("Kapsel öffnen …"))
        self.kapsel_knopf.clicked.connect(self.kapsel_waehlen)
        self.kapsel_schliessen_knopf = QPushButton(self.tr("Kapsel schließen"))
        self.kapsel_schliessen_knopf.clicked.connect(self.kapsel_schliessen)
        self.kapsel_schliessen_knopf.setEnabled(False)
        zeile.addWidget(self.kapsel_knopf)
        zeile.addWidget(self.kapsel_schliessen_knopf)
        zeile.addWidget(QLabel(self.tr("Privater Schlüssel (Datei; leer = GnuPG-Schlüsselbund)")))
        self.privater_schluessel = QLineEdit(lade_einstellungen().pgp_privater_schluessel)
        self.privater_schluessel.editingFinished.connect(self._schluessel_merken)
        zeile.addWidget(self.privater_schluessel, 1)
        schluessel_waehlen = QPushButton("…")
        schluessel_waehlen.clicked.connect(self.schluessel_waehlen)
        zeile.addWidget(schluessel_waehlen)
        kz.addLayout(zeile)
        self.kapsel_info = QLabel(self.tr(
            "Eine Kapsel (<name>_pseudonym.kapsel.asc) kommt vom Absender eines pseudonymisierten"
            " Dokuments und enthält die Zuordnung, verschlüsselt für Ihren Schlüssel."))
        self.kapsel_info.setWordWrap(True)
        kz.addWidget(self.kapsel_info)
        if not pgpmodul.verfuegbar():
            kapselgruppe.setEnabled(False)
            kapselgruppe.setToolTip(self.tr("Extra 'pgp' (pysequoia) oder GnuPG installieren."))
        aussen.addWidget(kapselgruppe)

        oben = QHBoxLayout()
        oben.addWidget(QLabel(self.tr("Person:")))
        self.person = QComboBox()
        oben.addWidget(self.person, 1)
        self.form = QComboBox()
        self.form.addItem(self.tr("voller Name"), "vollname")
        self.form.addItem(self.tr("Herr/Frau Nachname"), "anrede")
        oben.addWidget(self.form)
        neu = QPushButton(self.tr("Liste aktualisieren"))
        neu.clicked.connect(self.aktualisiere)
        oben.addWidget(neu)
        aussen.addLayout(oben)

        self.eingabe = QPlainTextEdit()
        self.eingabe.setPlaceholderText(self.tr("Text des Sprachmodells hier einfügen …"))
        aussen.addWidget(self.eingabe, 1)
        mitte = QHBoxLayout()
        laden = QPushButton(self.tr("Datei laden …"))
        laden.clicked.connect(self.laden)
        knopf = QPushButton(self.tr("Rückersetzen"))
        knopf.clicked.connect(self.rueckersetzen)
        mitte.addWidget(laden)
        mitte.addStretch(1)
        mitte.addWidget(knopf)
        aussen.addLayout(mitte)

        self.ausgabe = QPlainTextEdit()
        self.ausgabe.setReadOnly(True)
        aussen.addWidget(self.ausgabe, 1)
        unten = QHBoxLayout()
        self.hinweis = QLabel("")
        self.hinweis.setWordWrap(True)
        kopieren = QPushButton(self.tr("Kopieren"))
        kopieren.clicked.connect(self.kopieren)
        speichern = QPushButton(self.tr("Speichern …"))
        speichern.clicked.connect(self.speichern)
        unten.addWidget(self.hinweis, 1)
        unten.addWidget(kopieren)
        unten.addWidget(speichern)
        aussen.addLayout(unten)
        self.aktualisiere()

    def aktualisiere(self) -> None:
        self.person.clear()
        with self.sitzung.speicher() as sp:
            for e in sp.finde():
                self.person.addItem(f"{e['kennung']}  {e['nachname']}, {e['vorname']}",
                                    e["schluessel"])
        if self.sitzung.patient is not None:
            with self.sitzung.speicher() as sp:
                info = sp.patient_info(self.sitzung.patient.schluessel())
            if info:
                i = self.person.findData(info["schluessel"])
                if i >= 0:
                    self.person.setCurrentIndex(i)

    def laden(self) -> None:
        pfad, _ = QFileDialog.getOpenFileName(self, self.tr("Text"), "",
                                              self.tr("Text (*.txt *.md);;Alle (*)"))
        if pfad:
            with open(pfad, encoding="utf-8", errors="replace") as f:
                self.eingabe.setPlainText(f.read())

    # -- capsules ----------------------------------------------------------------------------------
    def schluessel_waehlen(self) -> None:
        pfad, _ = QFileDialog.getOpenFileName(
            self, self.tr("Privater Schlüssel"), "",
            self.tr("OpenPGP-Schlüssel (*.asc *.gpg *.pgp);;Alle Dateien (*)"))
        if pfad:
            self.privater_schluessel.setText(pfad)
            self._schluessel_merken()

    def _schluessel_merken(self) -> None:
        e = lade_einstellungen()
        if e.pgp_privater_schluessel != self.privater_schluessel.text().strip():
            e.pgp_privater_schluessel = self.privater_schluessel.text().strip()
            try:
                speichere_einstellungen(e)
            except OSError:
                pass

    def kapsel_waehlen(self) -> None:
        pfad, _ = QFileDialog.getOpenFileName(
            self, self.tr("Schlüsselkapsel"), "",
            self.tr("Kapseln (*.kapsel.asc *.asc);;Alle Dateien (*)"))
        if pfad:
            self.kapsel_oeffnen(Path(pfad))

    def kapsel_oeffnen(self, pfad: Path, passphrase: str | None = None) -> Kapsel | None:
        """Decrypt a capsule (asking for the passphrase when a key file needs one) and
        switch the re-identification to its mapping."""
        schluessel = self.privater_schluessel.text().strip() or None
        passphrase = passphrase if passphrase is not None else self.passphrase
        try:
            try:
                k = kapselmodul.oeffne(pfad, schluessel, passphrase)
            except pgpmodul.PGPFehler as e:
                if "Passphrase" not in str(e) or self.synchron:
                    raise
                eingabe, ok = QInputDialog.getText(
                    self, self.tr("Passphrase"),
                    self.tr("Passphrase des privaten Schlüssels:"), QLineEdit.EchoMode.Password)
                if not ok:
                    return None
                k = kapselmodul.oeffne(pfad, schluessel, eingabe)
        except (pgpmodul.PGPFehler, kapselmodul.KapselFehler, OSError) as e:
            self.kapsel_info.setText(self.tr("Kapsel nicht lesbar: {fehler}").format(fehler=e))
            if self.synchron:
                raise RuntimeError(str(e)) from e
            return None
        self.kapsel = k
        lokal = ""
        try:
            with self.sitzung.speicher() as sp:
                gleiche = [v for v in abgleichen(k.als_patient(), sp.kandidaten()) if v.gleich]
            if gleiche:
                lokal = self.tr(" Entspricht dem lokalen Eintrag {kennung}.").format(
                    kennung=gleiche[0].kennung)
            else:
                lokal = self.tr(" Kein passender Eintrag in der lokalen Datenbank.")
        except OSError:
            pass
        self.kapsel_info.setText(self.tr("Kapsel geöffnet: {beschreibung}.").format(
            beschreibung=k.beschreibung()) + lokal)
        self.person.setEnabled(False)
        self.kapsel_schliessen_knopf.setEnabled(True)
        return k

    def kapsel_schliessen(self) -> None:
        self.kapsel = None
        self.person.setEnabled(True)
        self.kapsel_schliessen_knopf.setEnabled(False)
        self.kapsel_info.setText(self.tr("Kapsel geschlossen; wieder lokale Datenbank."))

    def rueckersetzen(self) -> None:
        if self.kapsel is not None:
            zuordnung, info, bekannte = self.kapsel.zuordnungen, self.kapsel.info(), \
                [self.kapsel.kennung]
        else:
            schluessel = self.person.currentData()
            if not schluessel:
                self.hinweis.setText(self.tr("Keine Person gewählt."))
                return
            with self.sitzung.speicher() as sp:
                zuordnung = sp.zuordnung(schluessel)
                info = sp.patient_info(schluessel)
                bekannte = sp.kennungen()
        r = rueckersetzen(self.eingabe.toPlainText(), zuordnung, info,
                          form=self.form.currentData(), bekannte_kennungen=bekannte)
        self.ausgabe.setPlainText(r.text)
        if r.verbleibend:
            teile = [f"{rest} ({r.hinweise[rest]})" if rest in r.hinweise else rest
                     for rest in r.verbleibend]
            self.hinweis.setText(self.tr("{n} Ersetzungen; nicht aufgelöst: ").format(n=r.ersetzt)
                                 + ", ".join(teile))
        else:
            self.hinweis.setText(self.tr("{n} Ersetzungen, nichts offen.").format(n=r.ersetzt))

    def kopieren(self) -> None:
        QApplication.clipboard().setText(self.ausgabe.toPlainText())

    def speichern(self) -> None:
        pfad, _ = QFileDialog.getSaveFileName(self, self.tr("Speichern"), "",
                                              self.tr("Text (*.txt)"))
        if pfad:
            with open(pfad, "w", encoding="utf-8") as f:
                f.write(self.ausgabe.toPlainText())
