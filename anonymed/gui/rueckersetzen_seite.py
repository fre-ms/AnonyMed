"""Step 5: put the clear data back into the model's text."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from anonymed.gui.sitzung import Sitzung
from anonymed.pseudonyme.rueckersetzung import rueckersetzen


class RueckersetzenSeite(QWidget):
    def __init__(self, sitzung: Sitzung, eltern: QWidget | None = None) -> None:
        super().__init__(eltern)
        self.sitzung = sitzung
        aussen = QVBoxLayout(self)

        oben = QHBoxLayout()
        oben.addWidget(QLabel("Person:"))
        self.person = QComboBox()
        oben.addWidget(self.person, 1)
        self.form = QComboBox()
        self.form.addItem("voller Name", "vollname")
        self.form.addItem("Herr/Frau Nachname", "anrede")
        oben.addWidget(self.form)
        neu = QPushButton("Liste aktualisieren")
        neu.clicked.connect(self.aktualisiere)
        oben.addWidget(neu)
        aussen.addLayout(oben)

        self.eingabe = QPlainTextEdit()
        self.eingabe.setPlaceholderText("Text des Sprachmodells hier einfügen …")
        aussen.addWidget(self.eingabe, 1)
        mitte = QHBoxLayout()
        laden = QPushButton("Datei laden …")
        laden.clicked.connect(self.laden)
        knopf = QPushButton("Rückersetzen")
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
        kopieren = QPushButton("Kopieren")
        kopieren.clicked.connect(self.kopieren)
        speichern = QPushButton("Speichern …")
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
        pfad, _ = QFileDialog.getOpenFileName(self, "Text", "", "Text (*.txt *.md);;Alle (*)")
        if pfad:
            with open(pfad, encoding="utf-8", errors="replace") as f:
                self.eingabe.setPlainText(f.read())

    def rueckersetzen(self) -> None:
        schluessel = self.person.currentData()
        if not schluessel:
            self.hinweis.setText("Keine Person gewählt.")
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
            self.hinweis.setText(f"{r.ersetzt} Ersetzungen; nicht aufgelöst: " + ", ".join(teile))
        else:
            self.hinweis.setText(f"{r.ersetzt} Ersetzungen, nichts offen.")

    def kopieren(self) -> None:
        QApplication.clipboard().setText(self.ausgabe.toPlainText())

    def speichern(self) -> None:
        pfad, _ = QFileDialog.getSaveFileName(self, "Speichern", "", "Text (*.txt)")
        if pfad:
            with open(pfad, "w", encoding="utf-8") as f:
                f.write(self.ausgabe.toPlainText())
