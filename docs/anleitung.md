# AnonyMed – Anleitung

AnonyMed schwärzt Patientendokumente auf Ihrem Rechner, bevor Sie sie einem Sprachmodell geben, und setzt die Klardaten anschließend wieder ein. Nichts verlässt den Rechner außer der Suche im eigenen Nextcloud-Adressbuch.

## Installation

**Fertiges Paket:** Archiv für Ihr System entpacken (macOS: `AnonyMed.app` in den Programme-Ordner ziehen; Windows und Linux: Ordner `AnonyMed` an einen beliebigen Ort). Darin liegen die Oberfläche `anonymed-gui` (macOS: die App) und die Kommandozeile `anonymed`. Die Pakete sind nicht signiert; macOS fragt beim ersten Start nach (Rechtsklick → Öffnen), Windows zeigt den SmartScreen-Hinweis.

**Aus dem Quelltext:** Python 3.12 oder neuer und [uv](https://docs.astral.sh/uv/), dann

```sh
uv sync --extra ocr --extra codes --extra gui
uv run anonymed gui
```

Tesseract wird als zweites OCR-Programm benutzt, wenn es installiert ist (macOS: `brew install tesseract tesseract-lang`, Ubuntu: `apt install tesseract-ocr tesseract-ocr-deu`); nötig ist es nicht, RapidOCR ist enthalten.

## Erster Start

Beim ersten Start legt AnonyMed im Nutzerdatenordner die Pseudonym-Datenbank (`pseudonyme.sqlite`) und daneben den geheimen Schlüssel (`pseudonymschluessel.txt`) an. `anonymed konfig` zeigt die Pfade. **Sichern Sie den Schlüssel.** Mit demselben Schlüssel erzeugt jeder Rechner dieselben Pseudonyme; ohne ihn lassen sich alte Kennungen nicht mehr nachbilden. Die Datenbank enthält die Zuordnung Pseudonym → Klardaten für die Rückersetzung; sie gehört zu den Patientendaten und bleibt auf dem Rechner.

Adressbuch einrichten (einmalig, in der Kommandozeile):

```sh
anonymed kontakte einrichten --url https://ihre-nextcloud.example --benutzer ihrname --adressbuch patienten
```

Das App-Passwort (Nextcloud → Einstellungen → Sicherheit) wird im Schlüsselbund des Systems abgelegt.

## Die fünf Schritte

1. **Patient.** Im Adressbuch suchen (Name, Geburtsjahr), eine Patientendatei oder eine vCard laden. Prüfen Sie die Felder: Alles, was dort steht, wird geschwärzt. Tragen Sie Angehörige, Zuweiser und weitere Begriffe ein (je Zeile einer); aus den Notizen einer Kontaktkarte werden sie nicht automatisch übernommen. „Patient übernehmen“.
2. **Dokumente.** PDFs oder Fotos hineinziehen. Seiten ohne Textebene (Scans, Fotos) werden per OCR gelesen. „Analysieren“.
3. **Prüfen.** Jede Seite erscheint mit farbigen Kästen: blau aus der Textebene, orange aus der OCR, lila Barcodes, rot manuell, grau unlesbare Zeilen. Ein Fehltreffer (etwa ein Medikamentenname, der wie ein Nachname aussieht): anklicken, „Treffer verwerfen“. Etwas Übersehenes, zum Beispiel ein handschriftlicher Name: mit der Maus einen Kasten darüber ziehen. Zoom mit Strg/Cmd und Mausrad oder den Knöpfen.
4. **Exportieren.** Zielordner wählen. Je Dokument entstehen `name_anonym.pdf`, `name_anonym.txt` (der Text in Lesereihenfolge, das geben Sie dem Sprachmodell) und `name_anonym.bericht.json`. Die Gegenprobe liest das Ergebnis erneut; bleibt ein Identifikator übrig, wird nichts freigegeben und der Bericht sagt warum. Bei OCR-Seiten entstehen Vorschaubilder: Sehen Sie sie an, bevor Sie einen Scan weitergeben.
5. **Rückersetzen.** Den Text des Sprachmodells einfügen, die Person wählen, „Rückersetzen“. „Herr/Frau Nachname“ setzt die Anrede nach Fall, „voller Name“ den Namen. Verdrehte Pseudonyme (falsches Prüfzeichen) werden gemeldet, mit Vorschlag.

## Was geschwärzt wird und was nicht

Geschwärzt: Name in allen Schreibweisen, Geburtsdatum in allen Formaten, Anschrift, Telefon, E-Mail, Versichertennummer, Patienten-ID, Aktenzeichen, eingetragene Angehörige und Zuweiser, Barcodes, sowie generisch jede Versichertennummer, IBAN und E-Mail-Adresse und Werte hinter Etiketten wie „Versicherten-Nr.“ oder „Pat.-ID“.

Nicht geschwärzt: Diagnosen, Medikamente, Messwerte, Befundtexte, Termine. Nicht erkannt werden unbekannte Namen (ein Angehöriger, der nirgends eingetragen ist) und Handschrift, die die OCR nicht als Text findet; dafür gibt es den Prüfschritt.

## Pseudonyme

Die Hauptperson heißt `Patient K7Q4MX` (Buchstaben und Ziffern aus einem Schlüssel-Hash über Name und Geburtsdatum, mit Prüfzeichen), Angehörige `Angehörige K7Q4MX-1`, Zuweiser `Dr. Z1`, Straßen `[Straße …]`, Orte `[Ort …]` und `[PLZ Ort …]`, das Geburtsdatum `[geb. 1959]` oder `[Alter 66]`. Schreibvarianten desselben Namens ergeben dieselbe Kennung; ähnelt eine Person einem vorhandenen Eintrag nur ungefähr, fragt AnonyMed nach. Einzelheiten: `docs/pseudonyme.md`.

## Kommandozeile

```sh
anonymed anonymisieren befund.pdf --kontakt "Müller 1959"
anonymed anonymisieren scan.pdf --patient patient.json --ocr tesseract
anonymed pruefen befund_anonym.pdf --patient patient.json
anonymed rueckersetzen brief.txt --kennung K7Q4MX --form anrede
anonymed pseudonyme
anonymed konfig
```

Rückgabewerte: 0 freigegeben, 1 Eingabefehler, 2 nicht freigegeben, 3 Pseudonyme nach der Rückersetzung offen, 4 unsicherer Abgleich (`--gleich KENNUNG` oder `--neu`).
