# AnonyMed – Anleitung

AnonyMed schwärzt Patientendokumente auf Ihrem Rechner, bevor Sie sie einem Sprachmodell geben, und setzt die Klardaten anschließend wieder ein. Nichts verlässt den Rechner außer der Suche im eigenen Nextcloud-Adressbuch.

## Installation

**Fertiges Paket:** Archiv für Ihr System entpacken (macOS: `AnonyMed.app` in den Programme-Ordner ziehen; Windows und Linux: Ordner `AnonyMed` an einen beliebigen Ort). Darin liegen die Oberfläche `anonymed-gui` (macOS: die App) und die Kommandozeile `anonymed`. Die Pakete sind nicht signiert; macOS fragt beim ersten Start nach (Rechtsklick → Öffnen), Windows zeigt den SmartScreen-Hinweis.

**Aus dem Quelltext:** Python 3.12 oder neuer und [uv](https://docs.astral.sh/uv/), dann

```sh
uv sync --extra ocr --extra codes --extra gui
uv run anonymed gui
```

Tesseract wird als zweites OCR-Programm benutzt, wenn es installiert ist (macOS: `brew install tesseract tesseract-lang`, Ubuntu: `apt install tesseract-ocr tesseract-ocr-deu`); es wird an den üblichen Orten gefunden, auch wenn die App aus dem Finder startet. Nötig ist es nicht, RapidOCR ist enthalten. Wer Tesseract bevorzugt, trägt `backend = "tesseract"` unter `[ocr]` in die Konfigurationsdatei ein (`anonymed konfig` zeigt den Pfad) oder setzt `ANONYMED_OCR=tesseract`.

**Sprache.** Die Oberfläche folgt der Systemsprache; `--sprache en` oder `--sprache de` beim Start, das Menü *Sprache* im Fenster oder `ANONYMED_SPRACHE=en` legen sie fest. Berichte und Kommandozeile bleiben deutsch.

## Erster Start

Beim ersten Start legt AnonyMed im Nutzerdatenordner die Pseudonym-Datenbank (`pseudonyme.sqlite`) und daneben den geheimen Schlüssel (`pseudonymschluessel.txt`) an. `anonymed konfig` zeigt die Pfade. **Sichern Sie den Schlüssel.** Mit demselben Schlüssel erzeugt jeder Rechner dieselben Pseudonyme; ohne ihn lassen sich alte Kennungen nicht mehr nachbilden. Die Datenbank enthält die Zuordnung Pseudonym → Klardaten für die Rückersetzung; sie gehört zu den Patientendaten und bleibt auf dem Rechner.

Adressbuch einrichten (einmalig, in der Kommandozeile):

```sh
anonymed kontakte einrichten --url https://ihre-nextcloud.example --benutzer ihrname --adressbuch patienten
```

Das App-Passwort (Nextcloud → Einstellungen → Sicherheit) wird im Schlüsselbund des Systems abgelegt.

## Die fünf Schritte

1. **Patient.** Im Adressbuch suchen (Name, Geburtsjahr), eine Patientendatei oder eine vCard laden. Prüfen Sie die Felder: Alles, was dort steht, wird geschwärzt. Tragen Sie Angehörige, Zuweiser und weitere Begriffe ein (je Zeile einer); aus den Notizen einer Kontaktkarte werden sie nicht automatisch übernommen. „Patient übernehmen“.
2. **Dokumente.** PDFs oder Fotos hineinziehen. Seiten ohne Textebene (Scans, Fotos) werden per OCR gelesen. Wahlweise NER einschalten, um Personen und Orte zu finden, die niemand eingetragen hat. „Analysieren“.
3. **Prüfen.** Jede Seite erscheint mit farbigen Kästen: blau aus der Textebene, orange aus der OCR, lila Barcodes, rot manuell, grau unlesbare Zeilen, türkis NER. Ein Fehltreffer (etwa ein Medikamentenname, der wie ein Nachname aussieht): anklicken, „Treffer verwerfen“. Etwas Übersehenes, zum Beispiel ein handschriftlicher Name: mit der Maus einen Kasten darüber ziehen. Zoom mit Strg/Cmd und Mausrad oder den Knöpfen.
4. **Exportieren.** Zielordner wählen. Je Dokument entstehen `name_anonym.pdf`, `name_anonym.txt` (der Text in Lesereihenfolge, das geben Sie dem Sprachmodell) und `name_anonym.bericht.json`. Die Gegenprobe liest das Ergebnis erneut; bleibt ein Identifikator übrig, wird nichts freigegeben und der Bericht sagt warum. Bei OCR-Seiten entstehen Vorschaubilder: Sehen Sie sie an, bevor Sie einen Scan weitergeben.
5. **Rückersetzen.** Den Text des Sprachmodells einfügen, die Person wählen, „Rückersetzen“. „Herr/Frau Nachname“ setzt die Anrede nach Fall, „voller Name“ den Namen. Verdrehte Pseudonyme (falsches Prüfzeichen) werden gemeldet, mit Vorschlag.

## Was geschwärzt wird und was nicht

Geschwärzt: Name in allen Schreibweisen, Geburtsdatum in allen Formaten, Anschrift, Telefon, E-Mail, Versichertennummer, Patienten-ID, Aktenzeichen, eingetragene Angehörige und Zuweiser, Barcodes, sowie generisch jede Versichertennummer, IBAN und E-Mail-Adresse und Werte hinter Etiketten wie „Versicherten-Nr.“ oder „Pat.-ID“.

Nicht geschwärzt: Diagnosen, Medikamente, Messwerte, Befundtexte, Termine. Nicht erkannt werden unbekannte Namen (ein Angehöriger, der nirgends eingetragen ist) und Handschrift, die die OCR nicht als Text findet; dafür gibt es den Prüfschritt.

## NER

Named-Entity-Recognition ist ein statistisches Modell (spaCy, trainiert auf deutschen Nachrichten), das Personen- und Ortsnamen im Fließtext ohne Liste erkennt. Es findet die Angehörige oder den Kollegen, die niemand eingetragen hat, aber es rät: Es kann ein Medikament oder eine Abteilung für eine Person halten und einen Namen übersehen. Deshalb ist es standardmäßig aus, seine Treffer erscheinen im Prüfschritt in eigener Farbe, medizinische Eponyme (Alzheimer, Parkinson, Babinski …) sind ausgenommen, und es entscheidet nie über die Freigabe. Installation:

```sh
uv sync --extra ner
uv pip install https://github.com/explosion/spacy-models/releases/download/de_core_news_md-3.8.0/de_core_news_md-3.8.0-py3-none-any.whl
```

Gefundene Personen werden zu `Person <Code>`, Orte zu `[Ort <Code>]`, mit demselben Schlüssel-Hash wie die Patientenkennung; derselbe Name bekommt denselben Code, und die Rückersetzung kennt ihn.

## Fachbegriffe: was Rateverfahren nie anfassen dürfen

NER und die unscharfe OCR-Suche laufen hinter einer Schranke: Bevor sie eine Seite ansehen, werden alle medizinischen Fachbegriffe darauf markiert, und diese Stellen sind für beide tabu. Die festen Regeln für die Identifikatoren des Patienten bleiben unberührt (ein Patient, der wirklich Herz heißt, wird weiterhin geschwärzt). Das mitgelieferte Basisvokabular (rund 40 000 deutsche Begriffe) stammt aus Wikidata (CC0): deutsche Bezeichnungen und Synonyme aller Einträge mit ICD-10-Code, ATC-Code, INN oder MeSH-Deskriptor in den medizinischen MeSH-Bäumen (Anatomie, Organismen, Krankheiten, Arzneimittel, Verfahren, Psychiatrie, Phänomene); Geographie und Berufe sind ausgenommen, damit Orte und Nachnamen erkennbar bleiben. Beugungen, Bindestrich-Zusammensetzungen (*Parkinson-Syndrom*) und Umlaut-Umschriften werden erkannt, Mehrwortbegriffe als Ganzes.

Eigene Listen: `anonymed fachbegriffe importieren DATEI --name NAME` aus MeSH-XML (`--format mesh-xml`, NLM-Datei oder Übersetzung), aus ICD-10-GM, Alpha-ID-SE oder ATC-Tabellen (`--format csv --spalte N`) oder aus jeder einfachen Liste (ein Begriff je Zeile). `anonymed fachbegriffe pruefen WORT` sagt, ob ein Wort geschützt ist, `anonymed fachbegriffe status` zeigt die Quellen. SNOMED CT darf nicht mitgeliefert werden (Lizenz des BfArM nötig); eine lokal lizenzierte Beschreibungsdatei lässt sich wie jede CSV importieren.

## Pseudonyme

Die Hauptperson heißt `Patient K7Q4MX` (Buchstaben und Ziffern aus einem Schlüssel-Hash über Name und Geburtsdatum, mit Prüfzeichen), Angehörige `Angehörige K7Q4MX-1`, Zuweiser `Dr. Z1`, Straßen `[Straße …]`, Orte `[Ort …]` und `[PLZ Ort …]`, das Geburtsdatum `[geb. 1959]` oder `[Alter 66]`. Schreibvarianten desselben Namens ergeben dieselbe Kennung; ähnelt eine Person einem vorhandenen Eintrag nur ungefähr, fragt AnonyMed nach. Einzelheiten: `docs/pseudonyme.md`.

## Kommandozeile

```sh
anonymed anonymisieren befund.pdf --kontakt "Müller 1959"
anonymed anonymisieren scan.pdf --patient patient.json --ocr tesseract --ner
anonymed pruefen befund_anonym.pdf --patient patient.json
anonymed rueckersetzen brief.txt --kennung K7Q4MX --form anrede
anonymed pseudonyme
anonymed konfig
```

Rückgabewerte: 0 freigegeben, 1 Eingabefehler, 2 nicht freigegeben, 3 Pseudonyme nach der Rückersetzung offen, 4 unsicherer Abgleich (`--gleich KENNUNG` oder `--neu`).
