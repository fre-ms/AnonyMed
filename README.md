# AnonyMed

Local de-identification of patient documents (PDF, later scans and photos) so that they can be handed to cloud language models for drafting letters. Identifiers are replaced by **stable pseudonyms** rather than black bars, so the text stays readable and consistent for the model; afterwards the clear data is put back locally. The pseudonym key never leaves the machine.

Status (30 October 2026): milestones M0 (core), M1 (command line) and M2 (CardDAV address book) are done for documents **with a text layer**. Scans, photos and OCR follow in M3, the desktop GUI in M4. The design, open decisions and milestones are kept in the project's internal hand-over notes.

## The round trip

1. The patient's identifiers (name, date of birth, address, phone, e-mail, insurance number, relatives, referring physicians, file numbers) come from the CardDAV address book of your Nextcloud, a local vCard file or a patient file.
2. Every occurrence in the document is removed from the PDF content stream (real redaction with PyMuPDF) and the pseudonym is written into the gap: `Patient K7Q4MX`, `[geb. 1959]`, `[Straße 7HQ2NB]`, `[PLZ Ort 2F7QV4]`, `Angehörige K7Q4MX-1`, `Dr. Z1` … Streets and towns get their own codes from the same keyed hash of their phonetic form, so *Musterstr. 12* and *Musterstraße 12*, *München* and *Muenchen* always map to the same code. The identifier is a keyed hash of the phonetically normalised name and the date of birth, so the same person gets the same code on every machine that holds the key, and spelling variants do not matter; an error-tolerant record linkage in the manner of the Mainzelliste recognises people already in the local store (see `docs/pseudonyme.md`).
3. Metadata, XMP, bookmarks, annotations, form fields and attachments are stripped.
4. The result is re-read and checked against the same rules; nothing is released if a single identifier survives.
5. The language model writes the letter using the pseudonyms.
6. `anonymed rueckersetzen` puts the clear data back, including German case forms ("Patient K7Q4MX" → "Herr X", "Patienten K7Q4MX" → "Herrn X"), and flags codes the model mangled (check character).

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
#    -> befund_anonym.pdf          redacted PDF, pseudonyms in place, metadata removed
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

Options of `anonymisieren`: `--datum alter` writes `[Alter 36]` instead of `[geb. 1990]`; `--rastern` produces an image-only PDF; `--kein-text`, `--kein-bericht`, `--ohne-vermerk`; `--ziel DIR`; `--speicher FILE` for a different pseudonym database; `--konfig FILE` for a different configuration file.

### Address book

`kontakte einrichten` stores server address, user name and address book in the configuration file (`anonymed konfig` shows where) and the app password in the operating system's keyring (install the extra: `uv sync --extra carddav`). Without a keyring, set `ANONYMED_CARDDAV_PASSWORT`; `ANONYMED_CARDDAV_URL`, `_BENUTZER` and `_ADRESSBUCH` override the file. Only https is accepted (loopback addresses excepted for tests). The client speaks plain CardDAV (PROPFIND, REPORT addressbook-query) and needs no third-party library.

Cards written by DoctoNext and hand-made cards are both understood: `N` (salutation prefix gives Herr/Frau), `GENDER`, `BDAY`, `TEL`, `EMAIL`, `ADR`, `X-MAIDENNAME`, `X-DOCTOLIB-PATIENT-ID`, and an insurance number found in `NOTE` or `X-KVNR`. Relatives or referrers mentioned in a note are **not** derived automatically: export the card and add them to the patient file.

Options for the pseudonym store: `--schema hash` (default) or `--schema buchstabe` (running letters, `Patient A`); when the patient resembles a stored person without a clear match, the command stops and asks for `--gleich KENNUNG` or `--neu`.

Exit codes: 0 released, 1 input error, 2 not released (check failed or page without text layer), 3 pseudonyms left unresolved after re-identification, 4 uncertain match in the pseudonym store (decide with `--gleich` or `--neu`).

## What it does not do yet

* Pages without a text layer (scans, photos) are **not released**: OCR, pixel redaction and barcode blanking come with M3. Even then, handwriting is not recognised; the GUI (M4) will show every page for visual inspection.
* No NER: unknown names (a relative not listed in the patient file) are not found. Add them to the patient file (`angehoerige`, `zuweiser`, `begriffe`).

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
```

Layout: `anonymed/kern` (data model), `dokument` (loading, text layer), `erkennung` (rules), `schwaerzen` (redaction, clean-up, rasterising), `pruefung` (re-check, report), `pseudonyme` (assignment, SQLite store, re-identification), `quellen` (patient file, vCard reader, CardDAV client), `konfig.py` (configuration and keyring), `ablauf.py` (pipeline), `cli.py`.

## Licence

AGPL-3.0-or-later (PyMuPDF is AGPL). See `LICENSE`.
