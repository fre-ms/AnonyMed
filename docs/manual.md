# AnonyMed – Manual

AnonyMed redacts patient documents on your own computer before you hand them to a language model, and puts the clear data back afterwards. Nothing leaves the computer except the search in your own Nextcloud address book.

## Installation

**Packaged:** unpack the archive for your system (macOS: drag `AnonyMed.app` to Applications; Windows and Linux: put the folder `AnonyMed` anywhere). It contains the desktop interface `anonymed-gui` (macOS: the app) and the command line `anonymed`. The packages are not code-signed; macOS asks on first start (right-click, Open), Windows shows the SmartScreen notice.

**From source:** Python 3.12 or newer and [uv](https://docs.astral.sh/uv/), then

```sh
uv sync --extra ocr --extra codes --extra gui
uv run anonymed gui --language en
```

Tesseract is used as a second OCR engine when it is installed (macOS: `brew install tesseract tesseract-lang`, Ubuntu: `apt install tesseract-ocr tesseract-ocr-deu`); it is found in the usual places even when the app is started from the Finder. It is not required, RapidOCR is included. To prefer Tesseract, put `backend = "tesseract"` under `[ocr]` in the configuration file (`anonymed konfig` shows the path) or set `ANONYMED_OCR=tesseract`.

**Language.** The interface follows the system language; `--language en` or `--language de` on the command line, the menu *Language* in the window, or `ANONYMED_SPRACHE=en` choose it explicitly. Reports and command-line messages are in German.

## First start

AnonyMed creates the pseudonym database (`pseudonyme.sqlite`) and the secret key next to it (`pseudonymschluessel.txt`) in the user data folder; `anonymed konfig` shows the paths. **Back up the key.** With the same key every computer produces the same pseudonyms; without it old identifiers cannot be reproduced. The database holds the mapping pseudonym → clear data for re-identification; it is patient data and stays on the computer. `ANONYMED_DATENORDNER` puts database, key and configuration into one folder, for a portable installation on an encrypted stick.

Set up the address book once (command line):

```sh
anonymed kontakte einrichten --url https://your-nextcloud.example --benutzer yourname --adressbuch patients
```

The app password (Nextcloud → Settings → Security) goes into the operating system's keyring.

## The five steps

1. **Patient.** Search the address book (name, year of birth), load a patient file or a vCard. Check the fields: everything listed there will be redacted. Add relatives, referrers and further terms (one per line); they are not taken from a contact's notes automatically. *Use this patient*.
2. **Documents.** Drop PDFs or photos. Pages without a text layer (scans, photos) are read by OCR. Optionally switch on NER to find people and places nobody entered. *Analyse*.
3. **Review.** Every page appears with coloured boxes: blue from the text layer, orange from OCR, purple barcodes, red manual, grey unreadable lines, teal NER. A false hit (a drug name that looks like a surname): click it, *Discard hit*. Something missed, a handwritten name for instance: drag a box over it. Zoom with Ctrl/Cmd and the wheel or the buttons.
4. **Export.** Choose a target folder. Per document you get `name_anonym.pdf`, `name_anonym.txt` (the text in reading order, this is what you give the language model) and `name_anonym.bericht.json`. The re-check reads the result again; if an identifier survives, nothing is released and the report says why. OCR pages get preview images: look at them before you pass a scan on.
5. **Re-identify.** Paste the language model's text, pick the person, *Re-identify*. *Mr/Ms surname* inserts the salutation by grammatical case, *full name* the name. Mangled pseudonyms (wrong check character) are reported with a suggestion.

## What is redacted and what is not

Redacted: the name in all spellings, the date of birth in all formats, address, telephone, e-mail, insurance number, patient ID, file numbers, the relatives and referrers you entered, barcodes, and generically every insurance number, IBAN and e-mail address as well as values behind labels such as "Versicherten-Nr." or "Pat.-ID". With NER switched on, additionally people and places the model recognises.

Not redacted: diagnoses, medication, measurements, findings, appointment dates. Not recognised: unknown names when NER is off, and handwriting the OCR does not detect as text; the review step is there for that.

## NER

Named-entity recognition is a statistical model (spaCy, trained on German news) that tags person and place names in free text without a list. It catches the relative or colleague nobody entered, but it guesses: it may tag a drug or a department as a person, and it may miss a name. Therefore it is off by default, its hits are shown in their own colour in the review step, medical eponyms (Alzheimer, Parkinson, Babinski …) are excluded, and it never decides about the release. Install with

```sh
uv sync --extra ner
uv pip install https://github.com/explosion/spacy-models/releases/download/de_core_news_md-3.8.0/de_core_news_md-3.8.0-py3-none-any.whl
```

People it finds become `Person <code>`, places `[Ort <code>]`, with the same keyed hash as the patient's identifier, so the same name gets the same code and the re-identification knows it.

## Pseudonyms

The main person is `Patient K7Q4MX` (letters and digits from a keyed hash of name and date of birth, with a check character), relatives `Angehörige K7Q4MX-1`, referrers `Dr. Z1`, streets `[Straße …]`, towns `[Ort …]` and `[PLZ Ort …]`, the date of birth `[geb. 1959]` or `[Alter 66]`. Spelling variants of the same name give the same identifier; when a person only roughly resembles an existing entry, AnonyMed asks. Details in `docs/pseudonyme.md`.

## Command line

```sh
anonymed anonymisieren befund.pdf --kontakt "Müller 1959"
anonymed anonymisieren scan.pdf --patient patient.json --ocr tesseract --ner
anonymed pruefen befund_anonym.pdf --patient patient.json
anonymed rueckersetzen brief.txt --kennung K7Q4MX --form anrede
anonymed pseudonyme
anonymed konfig
```

Exit codes: 0 released, 1 input error, 2 not released, 3 pseudonyms left after re-identification, 4 uncertain match (`--gleich KENNUNG` or `--neu`).
