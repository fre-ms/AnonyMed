"""User configuration (TOML in the config folder) and the CardDAV app password.

The password is never written to the configuration file: it comes from the
environment variable ``ANONYMED_CARDDAV_PASSWORT`` or from the operating
system's keyring (optional extra ``carddav``).
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from platformdirs import user_config_path

DIENST = "AnonyMed CardDAV"
UMGEBUNG_PASSWORT = "ANONYMED_CARDDAV_PASSWORT"


@dataclass
class CardDAVKonfig:
    url: str = ""  # https://cloud.example.org
    benutzer: str = ""
    adressbuch: str = ""  # URI segment or display name, empty = the only/first one

    @property
    def vollstaendig(self) -> bool:
        return bool(self.url and self.benutzer)

    @property
    def kennung(self) -> str:
        """Keyring account name: user@host."""
        host = urlsplit(self.url).hostname or self.url
        return f"{self.benutzer}@{host}"


def konfigpfad() -> Path:
    """Configuration file in the user config folder; ANONYMED_DATENORDNER overrides the folder."""
    ordner = os.environ.get("ANONYMED_DATENORDNER")
    basis = Path(ordner) if ordner else user_config_path("AnonyMed", appauthor=False)
    return basis / "konfig.toml"


@dataclass
class Einstellungen:
    """The non-CardDAV part of the configuration file."""

    ocr_backend: str = "auto"  # auto, rapidocr, tesseract, aus
    tesseract_pfad: str = ""  # explicit path to the tesseract program
    tesseract_sprachen: str = ""  # e.g. "deu+eng"; empty = whatever of deu/eng is installed
    gui_sprache: str = ""  # "de", "en" or empty = system locale
    empfaenger_schluessel: str = ""  # public key of the recipient: file or https address
    empfaenger_fingerabdruck: str = ""  # pinned once the user confirmed it
    pgp_privater_schluessel: str = ""  # private-key file for capsules; empty = GnuPG keyring


def _lese_toml(pfad: Path) -> dict:
    if not pfad.exists():
        return {}
    with pfad.open("rb") as f:
        return tomllib.load(f)


def lade_einstellungen(pfad: str | Path | None = None) -> Einstellungen:
    """[ocr] and [gui] sections; ANONYMED_OCR and ANONYMED_SPRACHE override."""
    daten = _lese_toml(Path(pfad) if pfad else konfigpfad())
    ocr = daten.get("ocr", {})
    gui = daten.get("gui", {})
    empfaenger = daten.get("empfaenger", {})
    pgp = daten.get("pgp", {})
    e = Einstellungen(
        ocr_backend=str(ocr.get("backend", "auto")),
        tesseract_pfad=str(ocr.get("tesseract", "")),
        tesseract_sprachen=str(ocr.get("sprachen", "")),
        gui_sprache=str(gui.get("sprache", "")),
        empfaenger_schluessel=str(empfaenger.get("schluessel", "")),
        empfaenger_fingerabdruck=str(empfaenger.get("fingerabdruck", "")),
        pgp_privater_schluessel=str(pgp.get("privater_schluessel", "")),
    )
    e.ocr_backend = os.environ.get("ANONYMED_OCR", e.ocr_backend).strip() or "auto"
    e.tesseract_pfad = os.environ.get("ANONYMED_TESSERACT", e.tesseract_pfad).strip()
    e.gui_sprache = os.environ.get("ANONYMED_SPRACHE", e.gui_sprache).strip()
    return e


def _schreibe(pfad: Path, carddav: dict, e: Einstellungen) -> Path:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    zeilen = ["[carddav]"]
    for k in ("url", "benutzer", "adressbuch"):
        zeilen.append(f"{k} = {_toml_wert(str(carddav.get(k, '')))}")
    zeilen += ["", "[ocr]", f"backend = {_toml_wert(e.ocr_backend)}",
               f"tesseract = {_toml_wert(e.tesseract_pfad)}",
               f"sprachen = {_toml_wert(e.tesseract_sprachen)}",
               "", "[gui]", f"sprache = {_toml_wert(e.gui_sprache)}",
               "", "[empfaenger]", f"schluessel = {_toml_wert(e.empfaenger_schluessel)}",
               f"fingerabdruck = {_toml_wert(e.empfaenger_fingerabdruck)}",
               "", "[pgp]", f"privater_schluessel = {_toml_wert(e.pgp_privater_schluessel)}"]
    pfad.write_text("\n".join(zeilen) + "\n", encoding="utf-8")
    try:
        os.chmod(pfad, 0o600)
    except OSError:
        pass
    return pfad


def speichere_einstellungen(e: Einstellungen, pfad: str | Path | None = None) -> Path:
    """Write [ocr], [gui], [empfaenger] and [pgp], keeping [carddav] as it is."""
    pfad = Path(pfad) if pfad else konfigpfad()
    return _schreibe(pfad, _lese_toml(pfad).get("carddav", {}), e)


def lade_konfig(pfad: str | Path | None = None) -> CardDAVKonfig:
    """File values, overridden by ANONYMED_CARDDAV_URL/BENUTZER/ADRESSBUCH."""
    pfad = Path(pfad) if pfad else konfigpfad()
    k = CardDAVKonfig()
    if pfad.exists():
        with pfad.open("rb") as f:
            daten = tomllib.load(f).get("carddav", {})
        k = CardDAVKonfig(
            url=str(daten.get("url", "")),
            benutzer=str(daten.get("benutzer", "")),
            adressbuch=str(daten.get("adressbuch", "")),
        )
    k.url = os.environ.get("ANONYMED_CARDDAV_URL", k.url).strip()
    k.benutzer = os.environ.get("ANONYMED_CARDDAV_BENUTZER", k.benutzer).strip()
    k.adressbuch = os.environ.get("ANONYMED_CARDDAV_ADRESSBUCH", k.adressbuch).strip()
    return k


def _toml_wert(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def speichere_konfig(k: CardDAVKonfig, pfad: str | Path | None = None) -> Path:
    """Write the [carddav] section, keeping the other sections as they are."""
    pfad = Path(pfad) if pfad else konfigpfad()
    e = lade_einstellungen(pfad) if pfad.exists() else Einstellungen()
    return _schreibe(pfad, {"url": k.url, "benutzer": k.benutzer, "adressbuch": k.adressbuch}, e)


def passwort_laden(k: CardDAVKonfig) -> str | None:
    aus_umgebung = os.environ.get(UMGEBUNG_PASSWORT)
    if aus_umgebung:
        return aus_umgebung
    try:
        import keyring
    except ImportError:
        return None
    try:
        return keyring.get_password(DIENST, k.kennung)
    except Exception:  # keyring backends raise their own error types
        return None


def passwort_speichern(k: CardDAVKonfig, passwort: str) -> bool:
    """Store in the keyring; False when no keyring is available."""
    try:
        import keyring
    except ImportError:
        return False
    try:
        keyring.set_password(DIENST, k.kennung, passwort)
    except Exception:
        return False
    return True
