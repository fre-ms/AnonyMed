"""Text files (transcripts, notes, subtitles): rules, fuzzy search, marking, output."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pseudonymed.ablauf import (
    Ablaufoptionen,
    Textvorbereitung,
    abschliessen,
    pseudonymisiere,
    vorbereiten,
)
from pseudonymed.cli import app
from pseudonymed.dokument.textdatei import analysetext, ist_textdatei, lese_textdatei
from pseudonymed.erkennung import ner as nermodul
from pseudonymed.kern.modell import Patient
from pseudonymed.pruefung.bericht import als_text
from pseudonymed.pseudonyme.speicher import Pseudonymspeicher
from tests.synthetisch import erzeuge_transkript, verschrieben
from tests.test_ablauf import gefundene_klardaten

_TIMING = re.compile(r"^\d\d:\d\d:\d\d[,.]\d{3} --> .*$", re.MULTILINE)


@pytest.fixture
def transkript(tmp_path: Path, patient: Patient) -> Path:
    return erzeuge_transkript(tmp_path / "gespraech.txt", patient)


def test_textdatei_rundlauf(transkript: Path, patient: Patient,
                            speicher: Pseudonymspeicher) -> None:
    erg = pseudonymisiere(transkript, patient, Ablaufoptionen(), speicher=speicher)
    assert erg.freigegeben and erg.ist_text and erg.ziel_pdf is None, als_text(erg.bericht)
    assert erg.ziel == erg.ziel_txt == transkript.with_name("gespraech_pseudonym.txt")
    aus = erg.ziel.read_text(encoding="utf-8")
    assert gefundene_klardaten(aus, patient) == []
    assert verschrieben(patient.nachname) not in aus  # the misspelt surname: fuzzy search
    assert any(f.quelle == "unscharf" for f in erg.fundstellen)
    for behalten in ("Ramipril", "Atorvastatin", "Arterielle Hypertonie", "MoCA ergab 22 von 30"):
        assert behalten in aus
    assert f"Patient {erg.kennung}" in aus and f"Angehörige {erg.kennung}-1" in aus
    assert "[geb. " in aus and "[Straße " in aus and "[Ort " in aus
    assert "[Telefon] oder [E-Mail]." in aus  # sentence end after an address is kept
    assert aus.count("\n") == transkript.read_text(encoding="utf-8").count("\n")
    bericht = json.loads(erg.ziel_bericht.read_text(encoding="utf-8"))
    assert bericht["bestanden"] and bericht["optionen"]["unscharf_text"] is True
    assert gefundene_klardaten(json.dumps(bericht), patient) == []


@pytest.mark.parametrize("format_", ["srt", "vtt", "md"])
def test_untertitel_und_markdown_bleiben_intakt(tmp_path: Path, patient: Patient,
                                                speicher: Pseudonymspeicher, format_: str) -> None:
    quelle = erzeuge_transkript(tmp_path / f"gespraech.{format_}", patient, format_)
    erg = pseudonymisiere(quelle, patient, Ablaufoptionen(), speicher=speicher)
    assert erg.freigegeben, als_text(erg.bericht)
    assert erg.ziel.suffix == f".{format_}"
    vorher = quelle.read_text(encoding="utf-8")
    aus = erg.ziel.read_text(encoding="utf-8")
    assert gefundene_klardaten(aus, patient) == []
    assert _TIMING.findall(vorher) == _TIMING.findall(aus)
    if format_ == "srt":
        assert re.findall(r"^\d+$", vorher, re.MULTILINE) == re.findall(r"^\d+$", aus, re.MULTILINE)
    if format_ == "vtt":
        assert aus.startswith("WEBVTT")
    if format_ == "md":
        assert aus.startswith("# Gespräch") and aus.count("\n- ") == vorher.count("\n- ")


def test_analysetext_blendet_nur_zeitmarken_aus() -> None:
    srt = ("1\n00:00:01,000 --> 00:00:03,500 X:10\nHallo Herr Flantz.\n\n"
           "2\n00:00:04,000 --> 00:00:05,000\n")
    a = analysetext(srt, ".srt")
    assert len(a) == len(srt) and a.count("\n") == srt.count("\n")
    assert "Hallo Herr Flantz." in a and "-->" not in a and a.split("\n")[0].strip() == ""
    txt = "[00:00.000 --> 00:04.000]  Guten Tag"
    assert analysetext(txt, ".txt").strip() == "Guten Tag"
    assert analysetext("3\nZeile 3\n", ".txt") == "3\nZeile 3\n"  # numbers stay outside subtitles
    assert ist_textdatei("a.TXT") and ist_textdatei("b.vtt") and not ist_textdatei("c.pdf")


def test_genau_laesst_verschriebenes_stehen(transkript: Path, patient: Patient,
                                            speicher: Pseudonymspeicher) -> None:
    erg = pseudonymisiere(transkript, patient, Ablaufoptionen(unscharf_text=False),
                          speicher=speicher)
    assert erg.freigegeben
    aus = erg.ziel.read_text(encoding="utf-8")
    assert verschrieben(patient.nachname) in aus and patient.nachname not in aus
    assert not any(f.quelle == "unscharf" for f in erg.fundstellen)


def test_grossschreibung_schuetzt_kleine_woerter(tmp_path: Path,
                                                 speicher: Pseudonymspeicher) -> None:
    p = Patient(nachname="Lang", vorname="Erwin", geschlecht="m")
    quelle = tmp_path / "n.txt"
    quelle.write_text("Herr Lanq wartet schon lang. Land und Leute. Herr Lang kommt.\n",
                      encoding="utf-8")
    erg = pseudonymisiere(quelle, p, Ablaufoptionen(), speicher=speicher)
    aus = erg.ziel.read_text(encoding="utf-8")
    assert "lang." in aus and "Lanq" not in aus and "Herr Lang" not in aus
    # "Land" is capitalised and within tolerance: a guess the review step shows in orange
    assert any(f.quelle == "unscharf" and f.text == "Land" for f in erg.fundstellen)


def test_verwerfen_markieren_und_gegenprobe(transkript: Path, patient: Patient,
                                            speicher: Pseudonymspeicher) -> None:
    v = vorbereiten(transkript, patient, Ablaufoptionen(), speicher=speicher)
    assert isinstance(v, Textvorbereitung) and v.ist_text and v.text
    assert v.fachbegriffe_anzahl >= 3 and v.fachbegriffe_spannen(0)
    assert v.fachbegriffe_rects(0) == [] and v.ocr_seiten == []
    zuweiser = next(f for f in v.fundstellen if f.regel == "zuweiser1.name")
    v.verwerfe(zuweiser)
    assert zuweiser.text not in [f.text for f in v.fundstellen]
    a = v.text.index("Wörter vergesse")
    f = v.markiere(0, a, a + len("Wörter vergesse"), "[Symptom]")
    assert f.quelle == "manuell" and f.text == "Wörter vergesse"
    with pytest.raises(ValueError):
        v.markiere(0, 0, 0)
    erg = abschliessen(v)
    assert erg.freigegeben, als_text(erg.bericht)
    aus = erg.ziel.read_text(encoding="utf-8")
    assert zuweiser.text in aus and "[Symptom]" in aus and "Wörter vergesse" not in aus
    assert gefundene_klardaten(aus.replace(zuweiser.text, ""), patient) == []


def test_markierung_ersetzt_ueberlappende_treffer(transkript: Path, patient: Patient,
                                                   speicher: Pseudonymspeicher) -> None:
    v = vorbereiten(transkript, patient, Ablaufoptionen(), speicher=speicher)
    a = v.text.index("Meine Frau")
    b = v.text.index("hat gemerkt")
    v.markiere(0, a, b, "[Angabe]")
    assert not any(f.regel == "angehoerige1.vollname" for f in v.fundstellen)
    erg = abschliessen(v)
    assert erg.freigegeben and "[Angabe]" in erg.text


def test_kodierung_windows(tmp_path: Path, patient: Patient, speicher: Pseudonymspeicher) -> None:
    quelle = tmp_path / "notiz.txt"
    quelle.write_bytes(f"Herr {patient.nachname} klagt über Schwäche.\r\n".encode("cp1252"))
    assert lese_textdatei(quelle) == f"Herr {patient.nachname} klagt über Schwäche.\n"
    erg = pseudonymisiere(quelle, patient, Ablaufoptionen(), speicher=speicher)
    assert erg.freigegeben and "Schwäche" in erg.ziel.read_text(encoding="utf-8")


@pytest.mark.skipif(not nermodul.verfuegbar(), reason="Extra 'ner' nicht installiert")
def test_ner_in_textdatei(tmp_path: Path, patient: Patient, speicher: Pseudonymspeicher) -> None:
    quelle = tmp_path / "n.txt"
    quelle.write_text("Die Nachbarin Erika Hoffmann brachte ihn. Verdacht auf Morbus Parkinson,"
                      " Babinski negativ. Er lebt in Dresden.\n", encoding="utf-8")
    erg = pseudonymisiere(quelle, patient, Ablaufoptionen(ner=True), speicher=speicher)
    aus = erg.ziel.read_text(encoding="utf-8")
    assert "Erika Hoffmann" not in aus and "Dresden" not in aus
    assert "Morbus Parkinson" in aus and "Babinski" in aus
    assert any(f.regel == "ner.person" for f in erg.fundstellen)
    quelle.write_text("Überwiesen hat Sie die Hausärztin. Geboren ist er in Dresden.\n",
                      encoding="utf-8")
    erg = pseudonymisiere(quelle, patient, Ablaufoptionen(ner=True), speicher=speicher)
    aus = erg.ziel.read_text(encoding="utf-8")
    assert aus.startswith("Überwiesen hat") and "Dresden" not in aus  # a verb is not a place


def test_cli_textdatei(tmp_path: Path, patient: Patient) -> None:
    from tests.test_cli import _patientendatei

    runner = CliRunner()
    pdatei = _patientendatei(tmp_path, patient)
    quelle = erzeuge_transkript(tmp_path / "gespraech.txt", patient)
    sp = str(tmp_path / "p.sqlite")
    r = runner.invoke(app, ["pseudonymisieren", str(quelle), "--patient", str(pdatei),
                            "--speicher", sp])
    assert r.exit_code == 0, r.output
    ziel = tmp_path / "gespraech_pseudonym.txt"
    assert ziel.exists() and "Freigegeben" in r.output and "Text:" not in r.output
    assert gefundene_klardaten(r.output, patient) == []
    r = runner.invoke(app, ["pruefen", str(ziel), "--patient", str(pdatei), "--speicher", sp])
    assert r.exit_code == 0 and "bestanden" in r.output, r.output
    r = runner.invoke(app, ["pruefen", str(quelle), "--patient", str(pdatei), "--speicher", sp])
    assert r.exit_code == 2 and "NICHT" in r.output
    r = runner.invoke(app, ["pseudonymisieren", str(quelle), "--patient", str(pdatei),
                            "--speicher", sp, "--genau"])
    assert r.exit_code == 0 and verschrieben(patient.nachname) in ziel.read_text(encoding="utf-8")


# -- Whisper JSON and e-mail ------------------------------------------------------------------


def _whisper_json(zeilen: list[str], dialekt: str) -> dict | list:
    """The same transcript as openai-whisper, whisper.cpp or WhisperX would write it."""
    if dialekt == "openai":
        segmente = [{"id": i, "seek": 0, "start": 4.0 * i, "end": 4.0 * i + 3.5, "text": " " + z,
                     "tokens": [50364, 1000 + i, 50540], "temperature": 0.0,
                     "avg_logprob": -0.2, "compression_ratio": 1.3, "no_speech_prob": 0.01,
                     "words": [{"word": " " + w, "start": 4.0 * i, "end": 4.0 * i + 0.3,
                                "probability": 0.9} for w in z.split()]}
                    for i, z in enumerate(zeilen)]
        return {"text": " ".join(zeilen), "segments": segmente, "language": "de"}
    if dialekt == "cpp":
        return {"systeminfo": "AVX = 1", "model": {"type": "large"}, "params": {"language": "de"},
                "result": {"language": "de"},
                "transcription": [{"timestamps": {"from": f"00:00:{4 * i:02d},000",
                                                  "to": f"00:00:{4 * i + 3:02d},500"},
                                   "offsets": {"from": 4000 * i, "to": 4000 * i + 3500},
                                   "text": " " + z} for i, z in enumerate(zeilen)]}
    if dialekt == "whisperx":
        return {"segments": [{"start": 4.0 * i, "end": 4.0 * i + 3.5, "text": z,
                              "speaker": f"SPEAKER_0{i % 2}",
                              "words": [{"word": w, "score": 0.9} for w in z.split()]}
                             for i, z in enumerate(zeilen)],
                "word_segments": []}
    raise ValueError(dialekt)


@pytest.mark.parametrize("dialekt", ["openai", "cpp", "whisperx"])
def test_whisper_json(tmp_path: Path, patient: Patient, speicher: Pseudonymspeicher,
                      dialekt: str) -> None:
    from tests.synthetisch import transkript_text

    zeilen = transkript_text(patient)
    quelle = tmp_path / "gespraech.json"
    quelle.write_text(json.dumps(_whisper_json(zeilen, dialekt), ensure_ascii=False),
                      encoding="utf-8")
    assert ist_textdatei(quelle)
    erg = pseudonymisiere(quelle, patient, Ablaufoptionen(), speicher=speicher)
    assert erg.freigegeben, als_text(erg.bericht)
    assert erg.ziel == tmp_path / "gespraech_pseudonym.txt"
    aus = erg.ziel.read_text(encoding="utf-8")
    assert gefundene_klardaten(aus, patient) == []
    assert verschrieben(patient.nachname) not in aus
    assert aus.count("\n") == len(zeilen)
    assert "[00:00:00.000 --> 00:00:03.500] " in aus
    assert "[00:00:04.000 --> 00:00:07.500] " in aus
    assert "Ramipril und Atorvastatin" in aus and "50364" not in aus
    if dialekt == "whisperx":
        assert "SPEAKER_00: " in aus and "SPEAKER_01: " in aus
    assert f"Patient {erg.kennung}" in aus


def test_json_ohne_transkript(tmp_path: Path, patient: Patient,
                              speicher: Pseudonymspeicher) -> None:
    from pseudonymed.dokument.textdatei import rendere_whisper

    assert rendere_whisper({"text": " Nur Text. "}) == "Nur Text.\n"
    assert rendere_whisper([{"text": "a"}, {"text": "b"}]) == "a\nb\n"
    with pytest.raises(ValueError):
        rendere_whisper({"foo": 1})
    quelle = tmp_path / "x.json"
    quelle.write_text('{"foo": 1}', encoding="utf-8")
    with pytest.raises(ValueError):
        vorbereiten(quelle, patient, Ablaufoptionen(), speicher=speicher)


def _mail(patient: Patient, html_nur: bool = False) -> bytes:
    from email.message import EmailMessage

    m = EmailMessage()
    m["From"] = f"{patient.vollname} <{patient.emails[0]}>"
    m["To"] = "Sprechstunde Kognitive Neurologie <sprechstunde@example.org>"
    m["Cc"] = f"{patient.angehoerige[0].vollname} <partner@example.net>"
    m["Subject"] = f"Rückfrage zu meinem Termin – {patient.nachname}"
    m["Date"] = "Tue, 21 Jul 2026 10:00:00 +0200"
    m["Message-ID"] = "<geheim-123@mail.example.org>"
    m["Received"] = "from mx.example.org by mail.example.org"
    text = (f"Sehr geehrte Frau Doktor,\n\nmein Name ist {patient.vollname}, geboren am "
            f"{patient.geburtsdatum.strftime('%d.%m.%Y')}, wohnhaft {patient.strasse}, "
            f"{patient.plz} {patient.ort}. Meine Versichertennummer lautet "
            f"{patient.versichertennummer}.\nIch nehme Ramipril 5 mg und habe Schwäche im "
            f"linken Arm.\n\nViele Grüße\n{patient.vorname} {patient.nachname}\n"
            f"Tel. {patient.telefone[0]}\n")
    html = "<html><head><style>p{color:red}</style></head><body>" + "".join(
        f"<p>{z}</p>" for z in text.split("\n\n")) + "<br>&Uuml;bermittelt per Portal</body></html>"
    if html_nur:
        m.set_content(html, subtype="html", charset="utf-8")
    else:
        m.set_content(text, charset="utf-8", cte="quoted-printable")
        m.add_alternative(html, subtype="html", charset="utf-8")
    m.add_attachment(b"%PDF-1.4 synthetisch", maintype="application", subtype="pdf",
                     filename=f"Befund_{patient.nachname}_2026.pdf")
    return bytes(m)


def test_eml(tmp_path: Path, patient: Patient, speicher: Pseudonymspeicher) -> None:
    from pseudonymed.dokument.textdatei import rendere_mail

    roh = _mail(patient)
    assert "quoted-printable" in roh.decode("ascii", "replace")
    text = rendere_mail(roh)
    assert text.startswith("Von: ") and "\nBetreff: Rückfrage zu meinem Termin" in text
    assert "Message-ID" not in text and "geheim-123" not in text and "Received" not in text
    assert "Sehr geehrte Frau Doktor," in text and "<p>" not in text and "color:red" not in text
    assert f"[Anhänge, nicht enthalten: Befund {patient.nachname} 2026 (pdf)]" in text
    quelle = tmp_path / "anfrage.eml"
    quelle.write_bytes(roh)
    erg = pseudonymisiere(quelle, patient, Ablaufoptionen(), speicher=speicher)
    assert erg.freigegeben, als_text(erg.bericht)
    assert erg.ziel == tmp_path / "anfrage_pseudonym.txt"
    aus = erg.ziel.read_text(encoding="utf-8")
    assert gefundene_klardaten(aus, patient) == []
    assert "partner@example.net" not in aus and "sprechstunde@example.org" not in aus
    assert "Ramipril 5 mg" in aus and "Schwäche im linken Arm" in aus and "Viele Grüße" in aus
    assert f"Patient {erg.kennung}" in aus and "[E-Mail]" in aus
    assert "[Anhänge, nicht enthalten: Befund Patient" in aus


def test_eml_nur_html(tmp_path: Path, patient: Patient, speicher: Pseudonymspeicher) -> None:
    from pseudonymed.dokument.textdatei import html_zu_text, rendere_mail

    text = rendere_mail(_mail(patient, html_nur=True))
    assert "Sehr geehrte Frau Doktor,\n" in text and "Übermittelt per Portal" in text
    assert "<" not in text.split("\n\n", 1)[1] and "color:red" not in text
    assert html_zu_text("<ul><li>a</li><li>b &amp; c</li></ul><script>x()</script>") == "a\nb & c"
    quelle = tmp_path / "html.eml"
    quelle.write_bytes(_mail(patient, html_nur=True))
    erg = pseudonymisiere(quelle, patient, Ablaufoptionen(), speicher=speicher)
    assert erg.freigegeben and gefundene_klardaten(erg.text, patient) == []


def test_zahlwoerter_im_transkript(tmp_path: Path, patient: Patient,
                                   speicher: Pseudonymspeicher) -> None:
    """Spoken numbers: date of birth, house number, postcode as words."""
    from pseudonymed.erkennung.zahlwoerter import kardinal
    from tests.synthetisch import MONATE

    geb = patient.geburtsdatum
    assert geb is not None
    tag = {1: "ersten", 2: "zweiten", 3: "dritten", 7: "siebten", 8: "achten"}.get(
        geb.day, f"{_zahlwort(geb.day)}{'sten' if geb.day >= 20 else 'ten'}")
    jahr = f"neunzehnhundert{_zahlwort(geb.year % 100)}" if geb.year < 2000 else _zahlwort(geb.year)
    monat = MONATE[geb.month - 1]
    strasse, nummer = patient.strasse.rsplit(" ", 1)
    plz = " ".join(_zahlwort(int(z)) for z in patient.plz)
    text = (f"Arzt: Wann sind Sie geboren?\nPatient: Am {tag} {monat} {jahr}.\n"
            f"Arzt: Und Ihre Anschrift?\nPatient: {strasse} {_zahlwort(int(nummer))}, "
            f"Postleitzahl {plz} {patient.ort}.\nArzt: Sie nehmen zwei Tabletten Ramipril.\n")
    assert kardinal(jahr) == geb.year
    quelle = tmp_path / "zahlen.txt"
    quelle.write_text(text, encoding="utf-8")
    erg = pseudonymisiere(quelle, patient, Ablaufoptionen(), speicher=speicher)
    assert erg.freigegeben, als_text(erg.bericht)
    aus = erg.ziel.read_text(encoding="utf-8")
    assert f"Patient: Am [geb. {geb.year}].\n" in aus, aus
    assert "Patient: [Straße " in aus and ", Postleitzahl [PLZ Ort " in aus, aus
    assert strasse not in aus and patient.ort not in aus and jahr not in aus and tag not in aus
    assert "zwei Tabletten Ramipril" in aus
    assert gefundene_klardaten(aus, patient) == []


def _zahlwort(n: int) -> str:
    einer = ["null", "ein", "zwei", "drei", "vier", "fünf", "sechs", "sieben", "acht", "neun",
             "zehn", "elf", "zwölf", "dreizehn", "vierzehn", "fünfzehn", "sechzehn", "siebzehn",
             "achtzehn", "neunzehn"]
    zehner = {2: "zwanzig", 3: "dreißig", 4: "vierzig", 5: "fünfzig", 6: "sechzig", 7: "siebzig",
              8: "achtzig", 9: "neunzig"}
    if n == 1:
        return "eins"
    if n < 20:
        return einer[n]
    if n < 100:
        z, e = divmod(n, 10)
        return (f"{einer[e]}und" if e else "") + zehner[z]
    if n < 1000:
        h, r = divmod(n, 100)
        return f"{einer[h]}hundert" + (_zahlwort(r).replace("eins", "eins") if r else "")
    t, r = divmod(n, 1000)
    return f"{einer[t]}tausend" + (_zahlwort(r) if r else "")
