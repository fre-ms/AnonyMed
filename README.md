# AnonyMed

[![Tests](https://github.com/fre-ms/AnonyMed/actions/workflows/tests.yml/badge.svg)](https://github.com/fre-ms/AnonyMed/actions/workflows/tests.yml)

Local de-identification of patient documents (PDF, later scans and photos) so that they can be handed to cloud language models for drafting letters. Identifiers are replaced by **stable pseudonyms** rather than black bars, so the text stays readable and consistent for the model; afterwards the clear data is put back locally. The pseudonym key never leaves the machine.

Status (31 October 2026): milestones M0 (core), M1 (command line), M2 (CardDAV address book), M3 (scans, photos, OCR, barcodes) and M4 (desktop interface) are done. Installers (M5) follow. The design, open decisions and milestones are kept in the project's internal hand-over notes.

## The round trip

1. The patient's identifiers (name, date of birth, address, phone, e-mail, insurance number, relatives, referring physicians, file numbers) come from the CardDAV address book of your Nextcloud, a local vCard file or a patient file.
2. Every occurrence in the document is removed from the PDF content stream (real redaction with PyMuPDF) and the pseudonym is written into the gap: `Patient K7Q4MX`, `[geb. 1959]`, `[Straße 7HQ2NB]`, `[PLZ Ort 2F7QV4]`, `Angehörige K7Q4MX-1`, `Dr. Z1` … Streets and towns get their own codes from the same keyed hash of their phonetic form, so *Musterstr. 12* and *Musterstraße 12*, *München* and *Muenchen* always map to the same code. The identifier is a keyed hash of the phonetically normalised name and the date of birth, so the same person gets the same code on every machine that holds the key, and spelling variants do not matter; an error-tolerant record linkage in the manner of the Mainzelliste recognises people already in the local store (see `docs/pseudonyme.md`).
3. Metadata, XMP, bookmarks, annotations, form fields and attachments are stripped.
4. The result is re-read and checked against the same rules; nothing is released if a single identifier survives.
5. The language model writes the letter using the pseudonyms.
6. `anonymed rueckersetzen` puts the clear data back, including German case forms ("Patient K7Q4MX" → "Herr X", "Patienten K7Q4MX" → "Herrn X"), and flags codes the model mangled (check character).

## Install

**Packaged:** unpack the archive for your system (macOS: `AnonyMed.app`; Windows and Linux: the folder `AnonyMed` with `anonymed-gui` and the command line `anonymed`). The packages are built by `packaging/build.py` with PyInstaller and are not code-signed yet; macOS asks on first start (right-click, Open). Manuals: `docs/anleitung.md` (German), `docs/manual.md` (English).

**From source:** Python 3.12 or newer and [uv](https://docs.astral.sh/uv/).

```sh
git clone <repository> AnonyMed
cd AnonyMed
uv sync --extra ocr --extra codes --extra gui
uv run anonymed --help
```

On first start AnonyMed creates the pseudonym database and the secret key in the user data folder (`anonymed konfig` shows where; `ANONYMED_DATENORDNER` overrides the folder, for a portable installation on an encrypted stick for instance). Back the key up.

## Usage

```sh
# 1. say who the patient is: address book, vCard file or patient file
uv run anonymed kontakte einrichten --url https://cloud.example.org --benutzer dr   # once; asks for an app password
uv run anonymed kontakte suchen "Müller 1959"                                        # name parts, year or date of birth
uv run anonymed kontakte zeigen "Müller 1959"                                        # what will be redacted
uv run anonymed kontakte export "Müller 1959" -o patient.json                        # to add relatives, referrers, terms
uv run anonymed vorlage > patient.json                                               # or write the file from scratch

# 2. anonymise one or more files
uv run anonymed anonymisieren befund.pdf --kontakt "Müller 1959"      # straight from the address book
uv run anonymed anonymisieren befund.pdf --vcf export.vcf --nr 2      # from a vCard file
uv run anonymed anonymisieren befund.pdf --patient patient.json       # from a patient file
#    -> befund_anonym.pdf          redacted PDF with text layer, pseudonyms in place, metadata removed
#    -> befund_anonym.md           optional Markdown (--markdown)
#    -> befund_anonym.txt          text in reading order (paste this into the model)
#    -> befund_anonym.bericht.json audit report without any clear data

# 3. check any file again
uv run anonymed pruefen befund_anonym.pdf --patient patient.json

# 4. put the clear data back into the model's answer
uv run anonymed rueckersetzen brief.txt --kennung K7Q4MX --form anrede

# housekeeping
uv run anonymed pseudonyme        # which identifier belongs to whom, with linked spellings (local only)
uv run anonymed konfig            # version, pseudonym database and key file (back the key up!)
```

Options of `anonymisieren`: `--datum alter` writes `[Alter 36]` instead of `[geb. 1990]`; `--rastern` produces an image-only PDF; `--ocr auto|rapidocr|tesseract|aus`; `--keine-tabellen` (no table pass with Tesseract); `--roh` (OCR text as plain lines instead of Markdown); `--papier a4|a5|a3|letter|legal|bild` (sheet for images); `--kein-vokabular`; `--keine-codes`; `--vorschau auto|immer|nie`; `--kein-text`, `--kein-bericht`, `--ohne-vermerk`; `--ziel DIR`; `--speicher FILE` for a different pseudonym database; `--konfig FILE` for a different configuration file.

### Desktop interface

Seven steps as tabs: patient; documents (files are opened as soon as they are dropped, photos and images go on a sheet of the chosen paper size, A4 by default, and scans and photos appear underneath for the clean-up before OCR); text recognition (engine and language packs, Tesseract's table pass, then the recognised text next to the cleaned-up page, correctable by hand before anything is searched); analysis (the detection settings, with progress, and the import of your own vocabulary); review (redaction only, the text is no longer edited there); export; re-identify. Every step sits in a scroll area, so the window can be made as small as you like. Entering the export step runs the whole finishing phase into a scratch folder first, including the re-check with the patient's identifiers, and shows a traffic light: green means nothing was found and the export is ready, yellow means released but with warnings to read, red means identifiers from step 1 are still there, the export button stays off and a warning names the leftovers. The export then copies the checked files. The report states what was searched again ("Erneut geprüft nach dem Prüfschritt: Name, Geburtsdatum, …") and which hits were kept on purpose; what was done on purpose (the clean-up steps, the OCR summary, the general reminder that handwriting is not read, blanked unreadable lines, NER counts, fuzzy hits, remembered discards) is listed under *Hinweise*, and only what needs attention under *Warnungen* (pages without OCR, NER requested but missing, an uncertain patient match, a failed re-check), which alone turn the light yellow; a passed re-check on a plain scan is green. Alongside the files the export writes `<name>_anonym.vokabular.txt`, the discarded guesses (NER, fuzzy search) one per line, which the analysis step imports into your own vocabulary (see below). The review uses traffic-light colours: red is everything that will be redacted (dashed when NER or the fuzzy search merely suggest it), yellow is text the OCR read with low confidence (unreadable lines are blanked as a precaution), green is what stays: protected medical terms and hits you discarded, which remain visible and can be restored with a click. Dragging on a page draws a box with the replacement text from the field next to the buttons (no dialog); with *Maske zeichnen* it draws a black mask that removes text and image under it and writes nothing; Shift + drag pans the view. Discarded hits can be remembered per patient in the pseudonym store (box in the review step, stored on export): next time the same text under the same rule starts out green and can be restored. The recognised text is corrected in the text-recognition step (what the OCR misread in the image is masked in the review, not retyped); the correction feeds the text file, the Markdown and the invisible text layer.

```sh
uv sync --extra gui        # PySide6 (Qt 6)
uv run anonymed gui        # or: uv run anonymed-gui
```

Five tabs in one window: **1 Patient** (address-book search, patient file or vCard; every identifier in an editable field; relatives, referrers and free terms one per line), **2 Dokumente** (drag and drop, OCR backend, date form, codes), **3 Prüfen** (every page as an image with a coloured box per hit: blue text layer, orange OCR, purple code, red manual, grey unreadable; click a box or a table row and *Treffer verwerfen* drops a false hit; drag on the page to draw a box over what was missed), **4 Exportieren** (target folder, options, the report per file) and **5 Rückersetzen** (paste the model's text, pick the person, copy or save the result). A dropped hit is remembered: the re-check tolerates exactly that rule and text, everything else still blocks the release. Long steps run in a worker thread; the window never talks to the network except for the address-book search.

### Address book

`kontakte einrichten` stores server address, user name and address book in the configuration file (`anonymed konfig` shows where) and the app password in the operating system's keyring (install the extra: `uv sync --extra carddav`). Without a keyring, set `ANONYMED_CARDDAV_PASSWORT`; `ANONYMED_CARDDAV_URL`, `_BENUTZER` and `_ADRESSBUCH` override the file. Only https is accepted (loopback addresses excepted for tests). The client speaks plain CardDAV (PROPFIND, REPORT addressbook-query) and needs no third-party library.

Cards written by DoctoNext and hand-made cards are both understood: `N` (salutation prefix gives Herr/Frau), `GENDER`, `BDAY`, `TEL`, `EMAIL`, `ADR`, `X-MAIDENNAME`, `X-DOCTOLIB-PATIENT-ID`, and an insurance number found in `NOTE` or `X-KVNR`. Relatives or referrers mentioned in a note are **not** derived automatically: export the card and add them to the patient file.

Options for the pseudonym store: `--schema hash` (default) or `--schema buchstabe` (running letters, `Patient A`); when the patient resembles a stored person without a clear match, the command stops and asks for `--gleich KENNUNG` or `--neu`.

Exit codes: 0 released, 1 input error, 2 not released (check failed or page without text layer), 3 pseudonyms left unresolved after re-identification, 4 uncertain match in the pseudonym store (decide with `--gleich` or `--neu`).

### Scans and photos

Before OCR, image-only pages go through a clean-up (the lower half of the desktop's documents step; on the command line `--aufbereiten auto`, the default, applies the suggestion): the tilt is estimated from the text lines and straightened, a sheet photographed against a background is found and unwarped, shadows and grey paper are evened out, dark scanner borders are cleaned, coloured ink (stamps, highlighter, blue signatures) is dropped, grain and dust are removed, faint copies get more contrast, ruled and table lines can be taken out, pages can be rotated in 90° steps (an *Ausrichtung erkennen* button finds portrait/landscape from the lines and upside-down pages by a short OCR comparison) and turned black and white, and rectangles can be drawn on the result to erase regions white before the OCR ever sees them. Upscaling by 2×, 3× or 4× is not baked into the page but raises the resolution at which the OCR reads it; the *Buchstabenmodus* enlarges with Lanczos and sharpens the strokes in grey scale. Measured on synthetic scans with RapidOCR, a hard local threshold (Sauvola) after the enlargement looked crisp but wrecked the recognition, so the image stays grey; the automatic suggestion never switches the letter mode on, and it straightens only tilts of 1.2° and more, because the engines tolerate a slight tilt and rotating a noisy scan costs a little. A test guards that the suggested clean-up does not lower the OCR accuracy on a noisy, tilted scan. A learnt super-resolution model for text would need extra libraries and models and is not included. Denoising, contrast and sharpening have strength sliders; erased rectangles can be taken back one by one (Cmd/Ctrl+Z). Every step is a suggestion the user checks in a linked before/after view with a movable divider (pan or zoom one side, the other follows; Shift or Option + drag pans; an upscaled result is shown at the original size, zooming in reveals the gain); the processed image replaces the page, so that OCR, redaction and output all work on the corrected page. Needs OpenCV and NumPy, which come with the `ocr` extra.

Install the extras once: `uv sync --extra ocr --extra codes` (RapidOCR with bundled models, pillow-heif, zxing-cpp). A locally installed Tesseract (Homebrew, apt, the Windows installer) is found in the usual places even when the app starts from the Finder and is used when RapidOCR is missing or preferred (`--ocr tesseract`, `[ocr] backend = "tesseract"` in the configuration file, or `ANONYMED_OCR=tesseract`; `--ocr aus` disables OCR). German and English language packs are picked up when installed.

Tesseract and tables: its page segmentation takes a boxed grid for graphics and drops the cells (a bordered medication table came back as a single word). Every page is therefore scanned for grids (long horizontal and vertical rules that touch); each grid is cut out, its rules are removed and the crop is read again as one block; the reading with the higher confidence mass wins the box. Measured on synthetic bordered tables at 200 dpi: 0 of 45 cell words with the plain pass, 42 with the table pass, with thin rules 12 → 42, on a noisy scan 11 → 29; pages without a grid are untouched. The checkbox in the text-recognition step and `--keine-tabellen` switch it off. RapidOCR, a detector-based engine, does not have the problem.

Structured text: OCR delivers lines, a language model wants the document's own structure. By default (`--gliedern`, the checkbox in the text-recognition step; `--roh` for the plain lines) the recognised text goes out as Markdown: lines that run to the right margin are joined into paragraphs (hyphenation undone), a taller gap ends a paragraph, lines in a larger face become headings, short bold lines on their own (ink coverage well above the page's median) become small headings, bullets become list items, and words separated by far more than the usual gap are table cells (a tab in the page text), consecutive such lines a Markdown table. Italics are not marked: the engines report no slant. The structure is computed from the OCR geometry when the page is read and applied after the pseudonyms are in place, so the text file, the Markdown and the correction editor all show it; the text-recognition step shows the structured text, which you correct there.

Text layer on the image: whatever text goes out for a scan, the invisible layer sits on the printed words. For the plain reading every OCR word is written at its own box, sized to the box width. For a corrected or restructured text the words are aligned to the OCR words (longest common subsequence on normalised words); a corrected word takes the box of the word it replaces, several words on one OCR word share its box, words without counterpart are placed between or after their neighbours, and Markdown markup gets no box. Selecting a word in a PDF reader therefore covers the printed word, and a search hit lands where the word is.

Image files are placed on a sheet of paper, A4 by default (`--papier a4|a5|a3|letter|legal|bild`, the combo box in the documents step): portrait or landscape follows the image, which is scaled to fit and centred; `bild` keeps the old behaviour of a page exactly as large as the image. EXIF orientation of phone photos is honoured.

Pages without a text layer, image files (JPEG, PNG, TIFF, HEIC) and pages whose area is largely covered by images are read by OCR after a median filter against scanner noise. Hits are blanked **in the image pixels**: the OCR word box is snapped outward to the next word gap in the image and padded, so that an imprecise box never leaves a character standing; the pseudonym is written on top. The text file gets the OCR text as a separate block marked as unchecked; the output pages are OCR-read again and any leftover blocks the release. Barcodes and QR codes are blanked as `[Code]`.

OCR misreads are met in three ways: codes such as insurance numbers are matched tolerant to the usual confusions (0/O, 1/I, 5/S, 8/B); values behind labels like "Versicherten-Nr.", "Pat.-ID" or "AZ" are blanked even when misread; and every identifier is additionally searched with a small edit-distance tolerance that grows with its length, also across tokens OCR split ("gotthard. flantz@example. org"). Lines OCR could not read at all (fragments, low confidence) are blanked as `[unlesbar]` because what cannot be read cannot be checked (`--unlesbar lassen` keeps them). This also removes handwriting the detector finds but cannot read; handwriting it does not find stays. Preview images of every OCR page are written to `<name>_anonym_vorschau/`: look at them before handing a scan to a model, over-covered neighbours and `[unlesbar]` bars show what was lost.

### Texts and transcripts

Plain text goes through the same pipeline: `.txt`, `.md`, `.srt` and `.vtt` files, for instance the transcript a local speech recogniser wrote from a recorded consultation. The output is a text file again, `name_anonym.txt` (same extension as the input), with every identifier replaced by its pseudonym and nothing else touched: line structure, SRT and WebVTT timestamps, cue numbers and Whisper-style `[00:00.000 --> 00:04.000]` stamps stay as they are. The report is written as for a PDF, and the re-check runs over the output before it is released.

Two more text-like inputs are rendered first and come out as `.txt`: **Whisper JSON** (openai-whisper, whisper.cpp, WhisperX and plain segment lists) becomes one line per segment, `[00:00:01.000 --> 00:00:03.500] SPEAKER_00: text`; word-level entries and token ids are not carried over, because the token ids encode the clear text and the word copies could not be checked consistently. **E-mails** (`.eml`, as mail programs export them) become From, To, Cc, Date and Subject, the plain-text body (or the HTML body as text) and a list of attachment names; routing headers such as Message-ID and Received are dropped, and attachments are not included: anonymise a PDF or photo a patient sent as a file of its own.

Speech recognisers misspell names the way OCR does, so for text files the fuzzy search of the OCR path is on by default (`--genau` switches it off): the patient's identifiers are also searched with a small edit distance, names and places only when capitalised, so that *lang* stays safe for a patient called Lang while *Lanq* is caught. Fuzzy hits are guesses and are shown in orange in the review tab. The review shows the text with coloured spans instead of a page image; click a span to select the hit, select any text and press *Redact selection* to mark what was missed. NER and the medical vocabulary work as for PDFs. Numbers spoken as words are read as digits before matching: "einundzwanzigster Juli neunzehnhundertneunundfünfzig" is the date of birth, "Sieringgasse zweiundvierzig" the street, "acht zwei vier sechs zwei" the postcode; the whole phrase is then replaced by the pseudonym.

### Unknown names (NER, optional)

`uv sync --extra ner` (spaCy and the German model) adds named-entity recognition: `--ner` on the command line or the checkbox in the documents tab tags people and places the rules do not know and gives them reproducible codes (`Person K7Q4MX`, `[Ort 2F7QV4]`). It guesses, so it keeps its hands off everything the medical vocabulary marks (see below), drops ordinary words the model only capitalised because they start a sentence, drops single words the tagger calls common nouns and single words it has never seen (German compounds such as *Nierenlager*, *Strichgang*) unless a salutation, title or kinship word precedes a person or a preposition a place, shows its hits in their own colour in the review tab and never runs in the re-check. The order is fixed: the vocabulary marks the page first, then NER runs and skips every entity inside a marked span; what still slipped through in findings prose ("Konzentration: gut") were terms neither list had, hence the third list below and the noun filter. The graphical interface switches it on by default once the extra is installed, after the vocabulary has marked the page; on the command line it stays opt-in.

### Medical vocabulary (what guesswork must never redact)

NER and the fuzzy OCR search are heuristics, so they run behind a guard: before they look at a page, every medical term on it is marked and those spans are off limits to them. The deterministic rules for the patient's own identifiers are not affected (a patient really called Herz is still redacted). The bundled base vocabulary (`anonymed/daten/fachbegriffe_de.txt.gz`, about 40 000 German terms) is built by `werkzeuge/fachbegriffe_wikidata.py` from Wikidata (CC0): German labels and aliases of every item with an ICD-10 code, an ATC code, a WHO INN or a MeSH descriptor in the medical MeSH trees A–G (anatomy, organisms, diseases, drugs, techniques, psychiatry, phenomena; geography and occupations are left out so that towns and surnames stay recognisable). Matching tolerates German inflection, hyphenated compounds (*Parkinson-Syndrom*) and umlaut transliteration, and multi-word terms are matched whole. A second bundled list (`anonymed/daten/koerperteile_de.txt`, about 580 entries) covers body parts, organs, bones, muscles, vessels and nerves in everyday German plus the side and position words (links, rechts, proximal …), because the Wikidata list lacks many colloquial forms (Kopf, Nacken, Oberbauch, Wade) and NER liked to take them for names. A third list (`anonymed/daten/befundbegriffe_de.txt`, about 370 entries) holds the terms of the clinical examination and the findings report as they head the lines of a status (Konzentration, Haltung, Gangbild, Strichgang, Nierenlager, Reflexe, Sensibilität, Procedere …), which are in neither MeSH nor the body-part list.

Your own vocabulary grows with every review: the export writes the discarded guesses to `<name>_anonym.vokabular.txt` (one term per line; discarded hits of the fixed rules are left out, their text is the patient's own identifier), and *Vokabular importieren* in the analysis step, or `anonymed fachbegriffe ergaenzen FILE`, merges such a file into `nutzervokabular.txt` in the vocabulary folder. The vocabulary shields terms from NER and the fuzzy search for every patient; the per-patient *remember discarded hits* box in the review step is the place for rule hits.

Add your own lists with `anonymed fachbegriffe importieren` from MeSH XML (`--format mesh-xml`, the NLM file or a translation), from ICD-10-GM, Alpha-ID-SE or ATC tables (`--format csv --spalte N`) or from any plain list; `anonymed fachbegriffe pruefen WORT` tells whether a word is protected, `anonymed fachbegriffe status` lists the sources. SNOMED CT cannot be shipped (licence), but a locally licensed description file imports like any CSV.

### Languages

The interface is German with an en-GB translation (`anonymed gui --language en`, the *Language* menu, or `ANONYMED_SPRACHE=en`); manuals: `docs/anleitung.md` (German), `docs/manual.md` (English). Reports and command-line messages are German.

## What it does not do yet

* Without NER, unknown names (a relative not listed in the patient file) are not found; add them to the patient file (`angehoerige`, `zuweiser`, `begriffe`) or switch NER on and review its hits.
* Handwriting and text inside photos of screens or odd angles are beyond OCR; the review tab shows every page so that you can draw a box over what was missed.
* Office documents (DOCX, ODT) are not read; convert them to PDF or plain text first.

## Safety notes

* All processing is local; the only network connection is the CardDAV request to your own Nextcloud.
* Reports and logs contain no clear data; the mapping lives in an SQLite file in the user data folder next to the secret key for the identifiers (`anonymed konfig` shows where). Back up both; the key alone reproduces the identifiers, the database alone does not.
* Names are matched in their written, capitalised and transliterated forms (Müller, MÜLLER, Mueller, Muller, Müllers) but not in lower case, so that surnames which are also words do not fire in running text. Longest match wins, so "Max Müller" becomes one `Patient A`.
* PyMuPDF is pinned below 2.0: that release shifts the glyphs after a redaction (see `docs/pymupdf-redaction-shift.md`). A layout check after every redaction rasterises any page where text moved.

## Development

```sh
uv run pytest                  # synthetic documents and an in-process fake CardDAV server, nothing real
uv run ruff check anonymed tests
tests/e2e/nextcloud.sh         # optional: the same against a throwaway Nextcloud 35 in Docker
uv run --group build python packaging/build.py   # folder distribution plus smoke test
```

`.github/workflows/release.yml` builds the three platforms on a tag `v*` and uploads the archives as artefacts.

Layout: `anonymed/kern` (data model), `dokument` (loading incl. HEIC, text layer, OCR backends), `erkennung` (rules, generic and label patterns, fuzzy OCR search, barcodes), `schwaerzen` (redaction of text and pixels, clean-up, rasterising), `pruefung` (re-check incl. OCR, report, previews), `gui` (PySide6 pages over a Qt-free session model), `pseudonyme` (assignment, SQLite store, re-identification), `quellen` (patient file, vCard reader, CardDAV client), `konfig.py` (configuration and keyring), `ablauf.py` (pipeline), `cli.py`.

## Licence

AGPL-3.0-or-later (PyMuPDF is AGPL). See `LICENSE`.
