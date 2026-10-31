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

## Die sechs Schritte

1. **Patient.** Im Adressbuch suchen (Name, Geburtsjahr), eine Patientendatei oder eine vCard laden. Prüfen Sie die Felder: Alles, was dort steht, wird geschwärzt. Tragen Sie Angehörige, Zuweiser und weitere Begriffe ein (je Zeile einer); aus den Notizen einer Kontaktkarte werden sie nicht automatisch übernommen. „Patient übernehmen“.
2. **Dokumente.** PDFs, Fotos oder Textdateien (`.txt`, `.md`, `.srt`, `.vtt`, Whisper-`.json`, `.eml`, etwa das Transkript eines mitgeschnittenen Gesprächs oder die E-Mail eines Patienten) hineinziehen; sie werden sofort geöffnet. Seiten ohne Textebene (Scans, Fotos) erscheinen darunter zum Aufbereiten vor der Texterkennung, je Seite mit Vorschlag: Schräglage begradigen (Winkel einstellbar), fotografiertes Blatt entzerren, Hochskalieren 2×, 3× oder 4× (die OCR liest die Seite mit dem Vielfachen der Auflösung, die Seite selbst wird nicht vergrößert gespeichert), Buchstabenmodus (nutzt, dass der Inhalt Text ist: Lanczos-Vergrößerung, lokale Schwelle nach Sauvola für saubere Striche, geglättete Kanten), Hintergrund glätten, Schwarz-Weiß, und unter „Weitere Schritte“: dunkle Ränder säubern, farbige Tinte entfernen (Stempel, Marker, blaue Unterschriften), Entrauschen, Kontrast anheben, Schärfen, Linien entfernen, Ausrichtung erkennen (Hoch-/Querformat aus den Zeilen, Kopfstand per kurzem OCR-Vergleich), Drehen in 90°-Schritten. Auf dem „Nachher“-Bild gezogene Rechtecke löschen den Bereich weiß, bevor die OCR ihn sieht („Gelöschte Bereiche zurückholen“ macht das rückgängig). Links das Original, rechts das Ergebnis, beide Ansichten laufen beim Verschieben (Umschalt oder Option + Ziehen, mittlere Taste) und Zoomen gekoppelt; „Schritte auf alle Seiten übertragen“ überträgt die Haken. Ein gelerntes Superresolution-Modell für Text ist nicht enthalten, es bräuchte zusätzliche Bibliotheken und Modelle. „Weiter: Analysieren“.
3. **Analysieren.** Hier stehen die Einstellungen zum Lesen: OCR-Programm, Form des Geburtsdatums, Barcodes, unlesbare Zeilen, Fachbegriffe, unscharfe Suche in Textdateien (weil Spracherkennung Namen so verschreibt wie OCR), NER. Voreingestellt: Fachbegriffe markieren und schützen, danach NER (sofern das Extra installiert ist). „Analysieren“ liest alle Dokumente und zeigt den Fortschritt; danach öffnet sich der Prüfschritt.
4. **Prüfen.** Ampelfarben: Rot wird geschwärzt (gestrichelt, wenn NER oder die unscharfe Suche nur raten), Gelb hat die OCR unsicher gelesen (unlesbare Zeilen werden vorsorglich geschwärzt), Grün bleibt stehen: Fachbegriffe und verworfene Treffer, die sichtbar bleiben und sich per Klick wiederherstellen lassen. Die Quelle jedes Treffers steht in der Tabelle. Ein Fehltreffer (etwa ein Medikamentenname, der wie ein Nachname aussieht): anklicken, „Treffer verwerfen“. Ziehen mit der Maus zeichnet einen Kasten mit dem Ersatztext aus dem Feld neben den Knöpfen; mit „Maske zeichnen“ eine schwarze Maske, unter der Text und Bild verschwinden; Umschalt + Ziehen verschiebt den Ausschnitt, Strg/Cmd + Mausrad zoomt. „Verworfene Treffer für diesen Patienten merken“ speichert Ihre Entscheidungen beim Export in der Pseudonym-Datenbank: Beim nächsten Dokument desselben Patienten ist derselbe Text unter derselben Regel von vornherein grün und lässt sich wiederherstellen. Bei OCR-Seiten steht der erkannte Text unter dem Bild und lässt sich korrigieren; „Text übernehmen“ sucht darin neu und verwendet ihn für Textdatei, Markdown und die unsichtbare Textebene. Was die OCR im Bild falsch gelesen hat, schwärzen Sie mit Kasten oder Maske. Etwas Übersehenes, zum Beispiel ein handschriftlicher Name: mit der Maus einen Kasten darüber ziehen. Zoom mit Strg/Cmd und Mausrad oder den Knöpfen. Eine Textdatei erscheint statt als Seitenbild als Text mit farbigen Stellen; Klick auf eine Stelle wählt den Treffer, markierter Text wird mit „Auswahl schwärzen“ zum manuellen Treffer. Orange sind dort die Treffer der unscharfen Suche: Vorschläge, die Sie ansehen sollten.
5. **Exportieren.** Beim Öffnen des Schritts läuft zuerst die komplette Abschlussphase in einen Arbeitsordner, samt erneuter Prüfung des Ergebnisses auf alle Angaben aus „1 Patient“. Die Ampel rechts oben zeigt das Ergebnis: Grün heißt nichts mehr gefunden, bereit zum Export; Gelb heißt freigegeben, aber mit Warnungen, die Sie lesen sollten; Rot heißt, es wurden noch Daten gefunden, der Exportknopf bleibt gesperrt, und eine Meldung nennt die Resttreffer, die Sie im Prüfschritt mit Kasten oder Maske nacharbeiten. Der Export kopiert dann die geprüften Dateien. Der Bericht hält fest, was erneut geprüft wurde („Erneut geprüft nach dem Prüfschritt: Name, Geburtsdatum, …“) und welche Treffer Sie bewusst belassen haben. Zielordner wählen. Je Dokument entstehen `name_anonym.pdf` (mit Textebene: bei Scans und Fotos wird der OCR-Text ohne die Treffer unsichtbar eingebettet, auch nach „vollständig rastern“), `name_anonym.txt` (der Text in Lesereihenfolge, das geben Sie dem Sprachmodell), wahlweise `name_anonym.md` und `name_anonym.bericht.json`; aus einer Textdatei wird nur `name_anonym.txt` (gleiche Endung wie die Eingabe, Zeitmarken von Untertiteln bleiben erhalten) und der Bericht. Die Gegenprobe liest das Ergebnis erneut; bleibt ein Identifikator übrig, wird nichts freigegeben und der Bericht sagt warum. Bei OCR-Seiten entstehen Vorschaubilder: Sehen Sie sie an, bevor Sie einen Scan weitergeben.
6. **Rückersetzen.** Den Text des Sprachmodells einfügen, die Person wählen, „Rückersetzen“. „Herr/Frau Nachname“ setzt die Anrede nach Fall, „voller Name“ den Namen. Verdrehte Pseudonyme (falsches Prüfzeichen) werden gemeldet, mit Vorschlag.

## Texte und Transkripte

Wenn Sie Gespräche aufzeichnen und lokal transkribieren, ist das Transkript eine Textdatei: Sie läuft durch dieselbe Erkennung wie ein PDF, und das Ergebnis ist wieder Text mit Pseudonymen, sonst unverändert. Besonderheiten: Die unscharfe Suche findet den Patientennamen auch verschrieben („Flanz“ statt „Flantz“), verlangt bei Namen und Orten aber einen Großbuchstaben am Anfang; sie kann an Satzanfängen ähnliche Wörter vorschlagen („Land“ für Lang), die Sie im Prüfschritt verwerfen. `--genau` schaltet sie ab. Zahlwörter werden vor dem Abgleich als Ziffern gelesen: „am einundzwanzigsten Juli neunzehnhundertneunundfünfzig“ ist das Geburtsdatum, „Sieringgasse zweiundvierzig“ die Straße, „acht zwei vier sechs zwei“ die Postleitzahl; ersetzt wird dann die ganze Wortfolge.

**Whisper-JSON** (openai-whisper, whisper.cpp, WhisperX) wird zu einer Zeile je Segment mit Zeitmarke und Sprecher aufbereitet und als `.txt` ausgegeben; Wortzeiten und Token-Nummern werden nicht übernommen, denn die Token-Nummern enthalten den Klartext in anderer Form. **E-Mails** (`.eml`, aus dem Mailprogramm gezogen oder exportiert) werden zu Von, An, Cc, Datum, Betreff, dem Textkörper (notfalls aus HTML) und einer Liste der Anhangsnamen; Zustellkopfzeilen wie Message-ID bleiben weg, Anhänge sind nicht enthalten: Ein mitgeschicktes PDF oder Foto anonymisieren Sie als eigene Datei.

## Was geschwärzt wird und was nicht

Geschwärzt: Name in allen Schreibweisen, Geburtsdatum in allen Formaten, Anschrift, Telefon, E-Mail, Versichertennummer, Patienten-ID, Aktenzeichen, eingetragene Angehörige und Zuweiser, Barcodes, sowie generisch jede Versichertennummer, IBAN und E-Mail-Adresse und Werte hinter Etiketten wie „Versicherten-Nr.“ oder „Pat.-ID“.

Nicht geschwärzt: Diagnosen, Medikamente, Messwerte, Befundtexte, Termine. Nicht erkannt werden unbekannte Namen (ein Angehöriger, der nirgends eingetragen ist) und Handschrift, die die OCR nicht als Text findet; dafür gibt es den Prüfschritt.

## NER

Named-Entity-Recognition ist ein statistisches Modell (spaCy, trainiert auf deutschen Nachrichten), das Personen- und Ortsnamen im Fließtext ohne Liste erkennt. Es findet die Angehörige oder den Kollegen, die niemand eingetragen hat, aber es rät: Es kann ein Medikament oder eine Abteilung für eine Person halten und einen Namen übersehen. Deshalb erscheinen seine Treffer im Prüfschritt in eigener Farbe, medizinische Eponyme (Alzheimer, Parkinson, Babinski …) und alles, was das Fachvokabular markiert, sind ausgenommen, gewöhnliche Wörter, die nur am Satzanfang großgeschrieben sind („Überwiesen hat Sie …“), werden verworfen, und es entscheidet nie über die Freigabe. Die Oberfläche schaltet es nach der Fachbegriff-Markierung standardmäßig ein, sobald das Extra installiert ist; auf der Kommandozeile bleibt es Zuwahl (`--ner`). Installation:

```sh
uv sync --extra ner
uv pip install https://github.com/explosion/spacy-models/releases/download/de_core_news_md-3.8.0/de_core_news_md-3.8.0-py3-none-any.whl
```

Gefundene Personen werden zu `Person <Code>`, Orte zu `[Ort <Code>]`, mit demselben Schlüssel-Hash wie die Patientenkennung; derselbe Name bekommt denselben Code, und die Rückersetzung kennt ihn.

## Fachbegriffe: was Rateverfahren nie anfassen dürfen

NER und die unscharfe OCR-Suche laufen hinter einer Schranke: Bevor sie eine Seite ansehen, werden alle medizinischen Fachbegriffe darauf markiert, und diese Stellen sind für beide tabu. Die festen Regeln für die Identifikatoren des Patienten bleiben unberührt (ein Patient, der wirklich Herz heißt, wird weiterhin geschwärzt). Das mitgelieferte Basisvokabular (rund 40 000 deutsche Begriffe) stammt aus Wikidata (CC0): deutsche Bezeichnungen und Synonyme aller Einträge mit ICD-10-Code, ATC-Code, INN oder MeSH-Deskriptor in den medizinischen MeSH-Bäumen (Anatomie, Organismen, Krankheiten, Arzneimittel, Verfahren, Psychiatrie, Phänomene); Geographie und Berufe sind ausgenommen, damit Orte und Nachnamen erkennbar bleiben. Beugungen, Bindestrich-Zusammensetzungen (*Parkinson-Syndrom*) und Umlaut-Umschriften werden erkannt, Mehrwortbegriffe als Ganzes. Eine zweite mitgelieferte Liste (`koerperteile_de.txt`, rund 580 Einträge) enthält Körperteile, Organe, Knochen, Muskeln, Gefäße und Nerven in Alltagsdeutsch sowie Seiten- und Lagewörter (links, rechts, proximal …), weil der Wikidata-Liste viele Alltagsformen fehlen (Kopf, Nacken, Oberbauch, Wade) und NER sie gern für Namen hielt.

Eigene Listen: `anonymed fachbegriffe importieren DATEI --name NAME` aus MeSH-XML (`--format mesh-xml`, NLM-Datei oder Übersetzung), aus ICD-10-GM, Alpha-ID-SE oder ATC-Tabellen (`--format csv --spalte N`) oder aus jeder einfachen Liste (ein Begriff je Zeile). `anonymed fachbegriffe pruefen WORT` sagt, ob ein Wort geschützt ist, `anonymed fachbegriffe status` zeigt die Quellen. SNOMED CT darf nicht mitgeliefert werden (Lizenz des BfArM nötig); eine lokal lizenzierte Beschreibungsdatei lässt sich wie jede CSV importieren.

## Pseudonyme

Die Hauptperson heißt `Patient K7Q4MX` (Buchstaben und Ziffern aus einem Schlüssel-Hash über Name und Geburtsdatum, mit Prüfzeichen), Angehörige `Angehörige K7Q4MX-1`, Zuweiser `Dr. Z1`, Straßen `[Straße …]`, Orte `[Ort …]` und `[PLZ Ort …]`, das Geburtsdatum `[geb. 1959]` oder `[Alter 66]`. Schreibvarianten desselben Namens ergeben dieselbe Kennung; ähnelt eine Person einem vorhandenen Eintrag nur ungefähr, fragt AnonyMed nach. Einzelheiten: `docs/pseudonyme.md`.

## Kommandozeile

```sh
anonymed anonymisieren befund.pdf --kontakt "Müller 1959"
anonymed anonymisieren scan.pdf --patient patient.json --ocr tesseract --ner
anonymed anonymisieren gespraech.txt --kontakt "Müller 1959"          # Transkript, unscharfe Suche an
anonymed anonymisieren gespraech.srt --patient patient.json --genau   # nur exakte Treffer
anonymed pruefen befund_anonym.pdf --patient patient.json
anonymed rueckersetzen brief.txt --kennung K7Q4MX --form anrede
anonymed pseudonyme
anonymed konfig
```

Rückgabewerte: 0 freigegeben, 1 Eingabefehler, 2 nicht freigegeben, 3 Pseudonyme nach der Rückersetzung offen, 4 unsicherer Abgleich (`--gleich KENNUNG` oder `--neu`).
