"""Step 1: who is the patient.  Address book search, vCard or patient file, editable fields."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from anonymed.gui.sitzung import Sitzung
from anonymed.kern.modell import Patient, Person, parse_datum
from anonymed.konfig import lade_konfig, passwort_laden
from anonymed.quellen.carddav import CardDAVClient, CardDAVFehler
from anonymed.quellen.manuell import lade_patient
from anonymed.quellen.vcf import patienten_aus_vcf


def _personen(text: str) -> list[Person]:
    personen = []
    for zeile in text.splitlines():
        zeile = zeile.strip()
        if not zeile:
            continue
        titel = ""
        teile = zeile.split()
        while teile and teile[0].rstrip(".").lower() in ("dr", "prof", "med", "pd", "dipl"):
            titel = (titel + " " + teile.pop(0)).strip()
        if not teile:
            continue
        if "," in zeile:
            nach, _, vor = zeile.replace(titel, "", 1).partition(",")
            personen.append(Person(nachname=nach.strip(), vorname=vor.strip(), titel=titel))
        else:
            personen.append(Person(nachname=teile[-1], vorname=" ".join(teile[:-1]), titel=titel))
    return personen


class PatientSeite(QWidget):
    uebernommen = Signal(object)  # Patient

    def __init__(self, sitzung: Sitzung, eltern: QWidget | None = None) -> None:
        super().__init__(eltern)
        self.sitzung = sitzung
        self._treffer: list[Patient] = []
        aussen = QVBoxLayout(self)

        quelle = QGroupBox(self.tr("Quelle"))
        q = QHBoxLayout(quelle)
        self.suche = QLineEdit()
        self.suche.setPlaceholderText(self.tr("Name, Geburtsjahr oder Geburtsdatum …"))
        self.suche.returnPressed.connect(self.suchen)
        knopf_suche = QPushButton(self.tr("Im Adressbuch suchen"))
        knopf_suche.clicked.connect(self.suchen)
        knopf_datei = QPushButton(self.tr("Patientendatei …"))
        knopf_datei.clicked.connect(self.datei_laden)
        knopf_vcf = QPushButton(self.tr("vCard …"))
        knopf_vcf.clicked.connect(self.vcf_laden)
        q.addWidget(self.suche, 1)
        q.addWidget(knopf_suche)
        q.addWidget(knopf_datei)
        q.addWidget(knopf_vcf)
        aussen.addWidget(quelle)

        self.liste = QListWidget()
        self.liste.setMaximumHeight(120)
        self.liste.itemSelectionChanged.connect(self._treffer_gewaehlt)
        aussen.addWidget(self.liste)

        felder = QGroupBox(self.tr("Identifikatoren (werden geschwärzt)"))
        form = QFormLayout(felder)
        self.f: dict[str, QLineEdit] = {}
        for name, titel in (("nachname", self.tr("Nachname")), ("vorname", self.tr("Vorname(n)")),
                            ("geburtsname", self.tr("Geburtsname")), ("titel", self.tr("Titel")),
                            ("geburtsdatum", self.tr("Geburtsdatum (TT.MM.JJJJ)")),
                            ("strasse", self.tr("Straße und Hausnummer")), ("plz", self.tr("PLZ")),
                            ("ort", self.tr("Ort")),
                            ("telefone", self.tr("Telefon (mehrere mit ;)")),
                            ("emails", self.tr("E-Mail")),
                            ("versichertennummer", self.tr("Versichertennummer")),
                            ("patienten_id", self.tr("Patienten-ID")),
                            ("aktenzeichen", self.tr("Aktenzeichen (mehrere mit ;)"))):
            self.f[name] = QLineEdit()
            form.addRow(titel, self.f[name])
        self.geschlecht = QComboBox()
        self.geschlecht.addItems([self.tr("unbekannt"), self.tr("Herr"),
                                  self.tr("Frau")])
        form.addRow(self.tr("Anrede"), self.geschlecht)
        aussen.addWidget(felder)

        zusatz = QGroupBox(self.tr("Weitere Personen und Begriffe (eine je Zeile)"))
        z = QHBoxLayout(zusatz)
        self.angehoerige = QPlainTextEdit()
        self.angehoerige.setPlaceholderText(self.tr("Angehörige: Vorname Nachname"))
        self.zuweiser = QPlainTextEdit()
        self.zuweiser.setPlaceholderText(self.tr("Zuweiser: Dr. med. Vorname Nachname"))
        self.begriffe = QPlainTextEdit()
        self.begriffe.setPlaceholderText(self.tr("Begriffe: weitere Namen, Praxen, Nummern"))
        for w in (self.angehoerige, self.zuweiser, self.begriffe):
            w.setMaximumHeight(90)
            z.addWidget(w)
        aussen.addWidget(zusatz)

        unten = QHBoxLayout()
        self.hinweis = QLabel("")
        self.hinweis.setWordWrap(True)
        knopf = QPushButton(self.tr("Patient übernehmen"))
        knopf.setDefault(True)
        knopf.clicked.connect(self.uebernehmen)
        unten.addWidget(self.hinweis, 1)
        unten.addWidget(knopf)
        aussen.addLayout(unten)

    # -- sources -----------------------------------------------------------------------------------
    def suchen(self) -> None:
        begriff = self.suche.text().strip()
        if not begriff:
            return
        k = lade_konfig()
        passwort = passwort_laden(k) if k.vollstaendig else None
        if not k.vollstaendig or not passwort:
            QMessageBox.information(self, self.tr("Adressbuch"), self.tr(
                "CardDAV ist nicht eingerichtet. Bitte zuerst 'anonymed kontakte einrichten'"
                " ausführen."))
            return
        try:
            client = CardDAVClient(k.url, k.benutzer, passwort)
            kontakte = client.suche(client.adressbuch(k.adressbuch), begriff)
        except CardDAVFehler as e:
            QMessageBox.warning(self, self.tr("Adressbuch"), str(e))
            return
        self.zeige_treffer([kk.patient for kk in kontakte])

    def datei_laden(self) -> None:
        pfad, _ = QFileDialog.getOpenFileName(self, self.tr("Patientendatei"), "",
                                              self.tr("JSON/TOML (*.json *.toml)"))
        if pfad:
            try:
                self.zeige_treffer([lade_patient(pfad)])
            except (OSError, ValueError) as e:
                QMessageBox.warning(self, self.tr("Patientendatei"), str(e))

    def vcf_laden(self) -> None:
        pfad, _ = QFileDialog.getOpenFileName(self, self.tr("vCard"), "", self.tr("vCard (*.vcf)"))
        if pfad:
            self.zeige_treffer([p for p in patienten_aus_vcf(pfad) if p.nachname or p.vorname])

    def zeige_treffer(self, patienten: list[Patient]) -> None:
        self._treffer = patienten
        self.liste.clear()
        for p in patienten:
            geb = p.geburtsdatum.strftime("%d.%m.%Y") if p.geburtsdatum else "–"
            ort = " ".join(t for t in (p.plz, p.ort) if t)
            QListWidgetItem(self.tr("{nachname}, {vorname}  geb. {geb}  {ort}").format(
                nachname=p.nachname, vorname=p.vorname, geb=geb, ort=ort), self.liste)
        if len(patienten) == 1:
            self.liste.setCurrentRow(0)
        elif not patienten:
            self.hinweis.setText(self.tr("Kein Treffer."))

    def _treffer_gewaehlt(self) -> None:
        zeile = self.liste.currentRow()
        if 0 <= zeile < len(self._treffer):
            self.fuelle(self._treffer[zeile])

    # -- form ------------------------------------------------------------------------------------
    def fuelle(self, p: Patient) -> None:
        werte = {
            "nachname": p.nachname, "vorname": p.vorname, "geburtsname": p.geburtsname,
            "titel": p.titel,
            "geburtsdatum": p.geburtsdatum.strftime("%d.%m.%Y") if p.geburtsdatum else "",
            "strasse": p.strasse, "plz": p.plz, "ort": p.ort, "telefone": "; ".join(p.telefone),
            "emails": "; ".join(p.emails), "versichertennummer": p.versichertennummer,
            "patienten_id": p.patienten_id, "aktenzeichen": "; ".join(p.aktenzeichen),
        }
        for name, wert in werte.items():
            self.f[name].setText(wert)
        self.geschlecht.setCurrentIndex({"m": 1, "w": 2}.get(p.geschlecht, 0))
        self.angehoerige.setPlainText("\n".join(a.vollname for a in p.angehoerige))
        self.zuweiser.setPlainText("\n".join(" ".join(t for t in (z.titel, z.vollname) if t)
                                             for z in p.zuweiser))
        self.begriffe.setPlainText("\n".join(p.begriffe))
        if p.hinweise:
            self.hinweis.setText(self.tr("Notizen der Karte: ") + " | ".join(p.hinweise))
        else:
            self.hinweis.setText("")

    def patient(self) -> Patient:
        def liste(name: str) -> list[str]:
            return [t.strip() for t in self.f[name].text().split(";") if t.strip()]

        geburtsdatum: dt.date | None = parse_datum(self.f["geburtsdatum"].text()) \
            if self.f["geburtsdatum"].text().strip() else None
        return Patient(
            nachname=self.f["nachname"].text().strip(),
            vorname=self.f["vorname"].text().strip(),
            geschlecht={1: "m", 2: "w"}.get(self.geschlecht.currentIndex(), ""),
            titel=self.f["titel"].text().strip(),
            geburtsname=self.f["geburtsname"].text().strip(),
            geburtsdatum=geburtsdatum,
            strasse=self.f["strasse"].text().strip(),
            plz=self.f["plz"].text().strip(),
            ort=self.f["ort"].text().strip(),
            telefone=liste("telefone"),
            emails=liste("emails"),
            versichertennummer=self.f["versichertennummer"].text().strip(),
            patienten_id=self.f["patienten_id"].text().strip(),
            aktenzeichen=liste("aktenzeichen"),
            angehoerige=_personen(self.angehoerige.toPlainText()),
            zuweiser=_personen(self.zuweiser.toPlainText()),
            begriffe=[b.strip() for b in self.begriffe.toPlainText().splitlines() if b.strip()],
        )

    def uebernehmen(self) -> None:
        p = self.patient()
        if not p.nachname:
            QMessageBox.warning(self, self.tr("Patient"), self.tr("Der Nachname fehlt."))
            return
        if self.f["geburtsdatum"].text().strip() and p.geburtsdatum is None:
            QMessageBox.warning(self, self.tr("Patient"),
                                self.tr("Das Geburtsdatum ist nicht lesbar (TT.MM.JJJJ)."))
            return
        self.sitzung.patient = p
        self.sitzung.abgleich = None
        self.hinweis.setText(self.tr("Übernommen: {name}").format(name=p.vollname))
        self.uebernommen.emit(p)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.suche.hasFocus():
            self.suchen()
            return
        super().keyPressEvent(event)


__all__ = ["PatientSeite", "Path"]
