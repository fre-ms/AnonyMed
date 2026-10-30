"""CardDAV client against the in-process fake server."""

from __future__ import annotations

import pytest

from anonymed.kern.modell import Patient
from anonymed.quellen.carddav import CardDAVClient, CardDAVFehler, passt
from tests.carddav_fake import FakeCardDAV


def _client(server: FakeCardDAV, passwort: str | None = None) -> CardDAVClient:
    return CardDAVClient(server.url, server.benutzer, passwort or server.passwort)


def test_adressbuecher(fake_carddav: FakeCardDAV) -> None:
    buecher = _client(fake_carddav).adressbuecher()
    assert [(b.uri, b.name) for b in buecher] == [("contacts", "Kontakte"),
                                                  ("medifed-patients", "Patienten")]
    anfrage = fake_carddav.anfragen[-1]
    assert anfrage.methode == "PROPFIND" and anfrage.headers["Depth"] == "1"
    assert "application/xml" in anfrage.headers["Content-Type"]
    assert "displayname" in anfrage.body


def test_adressbuch_waehlen(fake_carddav: FakeCardDAV) -> None:
    c = _client(fake_carddav)
    assert c.adressbuch("medifed-patients").name == "Patienten"
    assert c.adressbuch("patienten").uri == "medifed-patients"
    with pytest.raises(CardDAVFehler, match="Mehrere Adressbücher"):
        c.adressbuch("")
    with pytest.raises(CardDAVFehler, match="nicht gefunden"):
        c.adressbuch("gibtsnicht")


def test_suche(fake_carddav: FakeCardDAV, patient: Patient, patientin: Patient) -> None:
    c = _client(fake_carddav)
    buch = c.adressbuch("medifed-patients")
    treffer = c.suche(buch, patient.nachname[:4].lower())
    assert [k.patient.nachname for k in treffer] == [patient.nachname]
    k = treffer[0]
    assert k.etag == '"etag0"' and k.href.endswith("karte0.vcf")
    assert k.patient.geburtsdatum == patient.geburtsdatum
    assert k.patient.schluessel() == patient.schluessel()
    report = fake_carddav.anfragen[-1]
    assert report.methode == "REPORT" and report.headers["Depth"] == "1"
    assert "addressbook-query" in report.body and "i;unicode-casemap" in report.body
    # second token is filtered locally
    jahr = str(patient.geburtsdatum.year)
    assert len(c.suche(buch, f"{patient.nachname} {jahr}")) == 1
    assert c.suche(buch, f"{patient.nachname} 1800") == []
    assert len(c.suche(buch, patient.geburtsdatum.strftime("%d.%m.%Y"))) == 1
    alle = c.alle(buch)
    assert {k.patient.nachname for k in alle} == {patient.nachname, patientin.nachname}
    assert c.suche(c.adressbuch("contacts"), "x") == []


def test_falsches_passwort(fake_carddav: FakeCardDAV) -> None:
    with pytest.raises(CardDAVFehler, match="Anmeldung abgelehnt"):
        _client(fake_carddav, "falsch").adressbuecher()


def test_nur_https_ausser_localhost() -> None:
    with pytest.raises(CardDAVFehler, match="https"):
        CardDAVClient("http://cloud.example.org", "u", "p")
    CardDAVClient("https://cloud.example.org/", "u", "p")  # fine
    with pytest.raises(CardDAVFehler, match="Server nicht erreichbar"):
        CardDAVClient("http://127.0.0.1:9", "u", "p", zeitlimit=2).adressbuecher()


def test_passt(patient: Patient) -> None:
    assert passt(patient, patient.nachname.upper())
    assert passt(patient, f"{patient.vorname[:3]} {patient.geburtsdatum.year}")
    assert passt(patient, patient.geburtsdatum.strftime("%d.%m.%Y"))
    assert not passt(patient, "Zyxwv")
    assert not passt(patient, "1800")
    p = Patient(nachname="Müller", vorname="Jörg")
    assert passt(p, "mueller") and passt(p, "muller") and passt(p, "Jörg")
