# AnonyMed

Local de-identification of patient documents (PDF, later scans and photos) so that they can be handed to cloud language models for drafting letters. Identifiers are replaced by **stable pseudonyms** rather than black bars, so the text stays readable and consistent for the model; afterwards the clear data is put back locally. The pseudonym key never leaves the machine.

Status (30 October 2026): milestones M0 (core) and M1 (command line) are done for documents **with a text layer**. Scans, photos and OCR follow in M3, the CardDAV address book in M2, the desktop GUI in M4. The design, open decisions and milestones are kept in the project's internal hand-over notes.

## The round trip

1. The patient's identifiers (name, date of birth, address, phone, e-mail, insurance number, relatives, referring physicians, file numbers) are read from a patient file (later: the CardDAV address book).
2. Every occurrence in the document is removed from the PDF content stream (real redaction with PyMuPDF) and the pseudonym is written into the gap: `Patient A`, `[geb. 1959]`, `[Wohnort]`, `Angehörige A1`, `Dr. Z1` …
3. Metadata, XMP, bookmarks, annotations, form fields and attachments are stripped.
4. The result is re-read and checked against the same rules; nothing is released if a single identifier survives.
5. The language model writes the letter using the pseudonyms.
6. `anonymed rueckersetzen` puts the clear data back, including German case forms ("Patient A" → "Herr X", "Patienten A" → "Herrn X").

## Install

Requires Python 3.12 or newer and [uv](https://docs.astral.sh/uv/).

```sh
git clone <repository> AnonyMed
cd AnonyMed
uv sync
uv run anonymed --help
```

## Usage

```sh
# 1. describe the patient (JSON or TOML)
uv run anonymed vorlage > patient.json      # edit the fields

# 2. anonymise one or more files
uv run anonymed anonymisieren befund.pdf --patient patient.json
#    -> befund_anonym.pdf          redacted PDF, pseudonyms in place, metadata removed
#    -> befund_anonym.txt          text in reading order (paste this into the model)
#    -> befund_anonym.bericht.json audit report without any clear data

# 3. check any file again
uv run anonymed pruefen befund_anonym.pdf --patient patient.json

# 4. put the clear data back into the model's answer
uv run anonymed rueckersetzen brief.txt --buchstabe A --form anrede

# housekeeping
uv run anonymed pseudonyme        # which letter belongs to whom (local only)
uv run anonymed konfig            # version and path of the pseudonym database
```

Options of `anonymisieren`: `--datum alter` writes `[Alter 36]` instead of `[geb. 1990]`; `--rastern` produces an image-only PDF; `--kein-text`, `--kein-bericht`, `--ohne-vermerk`; `--ziel DIR`; `--speicher FILE` for a different pseudonym database.

Exit codes: 0 released, 1 input error, 2 not released (check failed or page without text layer), 3 pseudonyms left unresolved after re-identification.

## What it does not do yet

* Pages without a text layer (scans, photos) are **not released**: OCR, pixel redaction and barcode blanking come with M3. Even then, handwriting is not recognised; the GUI (M4) will show every page for visual inspection.
* No NER: unknown names (a relative not listed in the patient file) are not found. Add them to the patient file (`angehoerige`, `zuweiser`, `begriffe`).
* No CardDAV yet (M2); the patient file is written by hand or exported.

## Safety notes

* All processing is local; the tool never opens a network connection (CardDAV in M2 will talk to your own Nextcloud only).
* Reports and logs contain no clear data; the key lives in an SQLite file in the user data folder (`anonymed konfig` shows where).
* Names are matched in their written, capitalised and transliterated forms (Müller, MÜLLER, Mueller, Muller, Müllers) but not in lower case, so that surnames which are also words do not fire in running text. Longest match wins, so "Max Müller" becomes one `Patient A`.
* PyMuPDF is pinned below 2.0: that release shifts the glyphs after a redaction (see `docs/pymupdf-redaction-shift.md`). A layout check after every redaction rasterises any page where text moved.

## Development

```sh
uv run pytest          # synthetic documents only (Faker de_DE), nothing real
uv run ruff check anonymed tests
```

Layout: `anonymed/kern` (data model), `dokument` (loading, text layer), `erkennung` (rules), `schwaerzen` (redaction, clean-up, rasterising), `pruefung` (re-check, report), `pseudonyme` (assignment, SQLite store, re-identification), `quellen` (patient sources), `ablauf.py` (pipeline), `cli.py`.

## Licence

AGPL-3.0-or-later (PyMuPDF is AGPL). See `LICENSE`.
