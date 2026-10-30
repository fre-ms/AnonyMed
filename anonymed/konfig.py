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
    return user_config_path("AnonyMed", appauthor=False) / "konfig.toml"


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
    pfad = Path(pfad) if pfad else konfigpfad()
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text(
        "[carddav]\n"
        f"url = {_toml_wert(k.url)}\n"
        f"benutzer = {_toml_wert(k.benutzer)}\n"
        f"adressbuch = {_toml_wert(k.adressbuch)}\n",
        encoding="utf-8",
    )
    try:
        os.chmod(pfad, 0o600)
    except OSError:
        pass
    return pfad


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
