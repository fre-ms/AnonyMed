"""OpenPGP for key capsules: encrypt to a recipient's public key, decrypt with the
recipient's private key.

Two engines.  pysequoia (extra ``pgp``, Sequoia-PGP with wheels for every platform,
bundled in the desktop app) lets a patient or a colleague encrypt with nothing but the
recipient's public key.  The system's GnuPG serves the recipient, whose private key
lives in its keyring and whose agent asks for the passphrase.  Reading a public key
and encrypting try pysequoia first and fall back to ``gpg``; decrypting with a
private-key *file* goes through pysequoia, decrypting from the keyring through
``gpg``.  (GnuPG 2.5 exports Curve25519 secret keys in a form Sequoia's Python binding
does not read yet; such keys are used through the keyring route.)
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

_GPG_ORTE = (
    "/opt/homebrew/bin/gpg", "/usr/local/bin/gpg", "/usr/local/MacGPG2/bin/gpg2", "/usr/bin/gpg",
    r"C:\Program Files (x86)\GnuPG\bin\gpg.exe", r"C:\Program Files\GnuPG\bin\gpg.exe",
)
_KOPF_OEFFENTLICH = "-----BEGIN PGP PUBLIC KEY BLOCK-----"
_KOPF_NACHRICHT = "-----BEGIN PGP MESSAGE-----"
MAX_SCHLUESSEL_BYTES = 262_144


class PGPFehler(Exception):
    pass


@dataclass
class Zertifikat:
    """A recipient's public key: fingerprint, user IDs and the armoured key itself."""

    fingerabdruck: str  # 40 hex digits, upper case
    nutzer: list[str] = field(default_factory=list)
    armor: str = ""
    ablauf: str = ""

    @property
    def gruppen(self) -> str:
        """The fingerprint in groups of four, as key servers and key signing parties show it."""
        return " ".join(self.fingerabdruck[i:i + 4] for i in range(0, len(self.fingerabdruck), 4))

    @property
    def kurz(self) -> str:
        return self.gruppen[-19:]  # the last four groups

    def beschreibung(self) -> str:
        wer = "; ".join(self.nutzer) if self.nutzer else "ohne Namen"
        return f"{wer} · Fingerabdruck {self.gruppen}"


# -- engines ---------------------------------------------------------------------------------------
def pysequoia_verfuegbar() -> bool:
    try:
        import pysequoia  # noqa: F401
    except ImportError:
        return False
    return True


def gnupg_programm() -> str | None:
    for name in ("gpg", "gpg2"):
        pfad = shutil.which(name)
        if pfad:
            return pfad
    for ort in _GPG_ORTE:
        if os.path.isfile(ort) and os.access(ort, os.X_OK):
            return ort
    return None


def verfuegbar() -> bool:
    return pysequoia_verfuegbar() or gnupg_programm() is not None


def engines() -> list[str]:
    aus = []
    if pysequoia_verfuegbar():
        import importlib.metadata as m

        aus.append(f"pysequoia {m.version('pysequoia')}")
    gpg = gnupg_programm()
    if gpg:
        aus.append(f"GnuPG {gpg}")
    return aus


def _gpg(argumente: list[str], eingabe: bytes | None = None, home: Path | None = None,
         timeout: int = 120) -> bytes:
    programm = gnupg_programm()
    if not programm:
        raise PGPFehler("GnuPG (gpg) ist nicht installiert.")
    umgebung = dict(os.environ)
    if home is not None:
        umgebung["GNUPGHOME"] = str(home)
    try:
        lauf = subprocess.run([programm, "--batch", "--yes", *argumente], input=eingabe,
                              capture_output=True, check=False, timeout=timeout, env=umgebung)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise PGPFehler(f"GnuPG konnte nicht ausgeführt werden: {e}") from e
    if lauf.returncode != 0:
        meldung = lauf.stderr.decode("utf-8", "replace").strip()
        raise PGPFehler("GnuPG meldet: " + (meldung.splitlines()[-1] if meldung else
                                           f"Rückgabewert {lauf.returncode}"))
    return lauf.stdout


# -- public keys -----------------------------------------------------------------------------------
def _hole(quelle: str | Path) -> bytes:
    """Key material from armoured text, an https address or a file."""
    if isinstance(quelle, str) and quelle.lstrip().startswith("-----BEGIN PGP"):
        return quelle.encode("utf-8")
    text = str(quelle)
    if text.startswith("http://"):
        raise PGPFehler("Öffentliche Schlüssel nur über https laden (oder als Datei).")
    if text.startswith("https://"):
        anfrage = urllib.request.Request(text, headers={"User-Agent": "AnonyMed"})
        try:
            with urllib.request.urlopen(anfrage, timeout=15) as antwort:  # noqa: S310
                daten = antwort.read(MAX_SCHLUESSEL_BYTES + 1)
        except OSError as e:
            raise PGPFehler(f"Schlüssel konnte nicht geladen werden: {e}") from e
        if len(daten) > MAX_SCHLUESSEL_BYTES:
            raise PGPFehler("Die Antwort ist zu groß für einen Schlüssel.")
        return daten
    pfad = Path(text).expanduser()
    if not pfad.is_file():
        raise PGPFehler(f"Schlüsseldatei nicht gefunden: {pfad}")
    if pfad.stat().st_size > MAX_SCHLUESSEL_BYTES:
        raise PGPFehler("Die Datei ist zu groß für einen Schlüssel.")
    return pfad.read_bytes()


def lade_zertifikat(quelle: str | Path) -> Zertifikat:
    """The recipient's public key from armoured text, an https address or a file,
    checked for revocation and expiry."""
    daten = _hole(quelle)
    if pysequoia_verfuegbar():
        return _zertifikat_sequoia(daten)
    if gnupg_programm():
        return _zertifikat_gnupg(daten)
    raise PGPFehler("Weder pysequoia (Extra 'pgp') noch GnuPG vorhanden.")


def _zertifikat_sequoia(daten: bytes) -> Zertifikat:
    import pysequoia as sq

    try:
        cert = sq.Cert.from_bytes(daten)
    except Exception as e:  # noqa: BLE001
        raise PGPFehler(f"Kein lesbarer OpenPGP-Schlüssel: {e}") from e
    if getattr(cert, "is_revoked", False):
        raise PGPFehler("Der Schlüssel ist widerrufen.")
    ablauf = cert.expiration
    if ablauf is not None:
        import datetime as dt

        if ablauf < dt.datetime.now(tz=ablauf.tzinfo):
            raise PGPFehler(f"Der Schlüssel ist abgelaufen ({ablauf:%d.%m.%Y}).")
    return Zertifikat(fingerabdruck=str(cert.fingerprint).upper(),
                      nutzer=[str(u) for u in cert.user_ids],
                      armor=sq.armor(bytes(cert), sq.ArmorKind.PublicKey),
                      ablauf=f"{ablauf:%Y-%m-%d}" if ablauf is not None else "")


def _zertifikat_gnupg(daten: bytes) -> Zertifikat:
    text = daten.decode("utf-8", "replace")
    if _KOPF_OEFFENTLICH not in text:
        raise PGPFehler("Ohne pysequoia bitte einen ASCII-gepanzerten Schlüssel"
                        " (gpg --armor --export) angeben.")
    with tempfile.TemporaryDirectory() as ordner:
        datei = Path(ordner) / "schluessel.asc"
        datei.write_bytes(daten)
        aus = _gpg(["--with-colons", "--import-options", "show-only", "--import", str(datei)])
    fpr, nutzer, ablauf = "", [], ""
    for zeile in aus.decode("utf-8", "replace").splitlines():
        felder = zeile.split(":")
        if felder[0] == "fpr" and not fpr:
            fpr = felder[9]
        elif felder[0] == "uid":
            nutzer.append(felder[9])
        elif felder[0] == "pub":
            if "r" in felder[1]:
                raise PGPFehler("Der Schlüssel ist widerrufen.")
            if "e" in felder[1]:
                raise PGPFehler("Der Schlüssel ist abgelaufen.")
            ablauf = felder[6]
    if not fpr:
        raise PGPFehler("GnuPG fand keinen Schlüssel in der Datei.")
    return Zertifikat(fingerabdruck=fpr.upper(), nutzer=nutzer, armor=text, ablauf=ablauf)


# -- encrypt / decrypt -----------------------------------------------------------------------------
def verschluessele(daten: bytes, zertifikat: Zertifikat) -> bytes:
    """``daten`` as an ASCII-armoured OpenPGP message for the certificate's owner."""
    if pysequoia_verfuegbar():
        import pysequoia as sq

        cert = sq.Cert.from_bytes(zertifikat.armor.encode("utf-8"))
        return sq.encrypt(recipients=[cert], bytes=daten)
    with tempfile.TemporaryDirectory() as ordner:
        datei = Path(ordner) / "empfaenger.asc"
        datei.write_text(zertifikat.armor, encoding="utf-8")
        return _gpg(["--armor", "--trust-model", "always", "--recipient-file", str(datei),
                     "--encrypt"], eingabe=daten)


def entschluessele(daten: bytes, privater_schluessel: str | Path | None = None,
                   passphrase: str | None = None, gnupg_home: Path | None = None) -> bytes:
    """Decrypt an OpenPGP message: with a private-key file through pysequoia, else with
    the keyring of the system's GnuPG (its agent asks for the passphrase unless one is
    given)."""
    if privater_schluessel:
        if not pysequoia_verfuegbar():
            raise PGPFehler("Für eine Schlüsseldatei wird pysequoia gebraucht (Extra 'pgp');"
                            " sonst den Schlüssel in GnuPG importieren.")
        import pysequoia as sq

        try:
            tsk = sq.Tsk.from_file(str(Path(privater_schluessel).expanduser()))
        except Exception as e:  # noqa: BLE001
            raise PGPFehler(f"Privater Schlüssel nicht lesbar: {e}. GnuPG-2.5-Exporte von"
                            " Curve25519-Schlüsseln bitte über den Schlüsselbund nutzen.") from e
        try:
            dec = tsk.decryptor(password=passphrase) if passphrase else tsk.decryptor()
        except Exception as e:  # noqa: BLE001
            if passphrase is None:
                raise PGPFehler("Passphrase nötig") from e
            raise PGPFehler(f"Schlüssel ließ sich nicht entsperren: {e}") from e
        try:
            return bytes(sq.decrypt(decryptor=dec, bytes=daten).bytes)
        except Exception as e:  # noqa: BLE001
            raise PGPFehler(f"Entschlüsselung fehlgeschlagen: {e}") from e
    if not gnupg_programm():
        raise PGPFehler("Kein privater Schlüssel angegeben und GnuPG nicht installiert.")
    with tempfile.TemporaryDirectory() as ordner:
        datei = Path(ordner) / "kapsel.asc"
        datei.write_bytes(daten)
        argumente = ["--decrypt"]
        eingabe = None
        if passphrase is not None:
            argumente = ["--pinentry-mode", "loopback", "--passphrase-fd", "0", "--decrypt"]
            eingabe = passphrase.encode("utf-8") + b"\n"
        return _gpg([*argumente, str(datei)], eingabe=eingabe, home=gnupg_home)


# -- key pairs for people without GnuPG ------------------------------------------------------------
def erzeuge_schluesselpaar(name: str, email: str) -> tuple[str, str]:
    """A fresh OpenPGP key pair (Curve25519) as armoured public and secret key."""
    if not pysequoia_verfuegbar():
        raise PGPFehler("Schlüssel erzeugen braucht pysequoia (Extra 'pgp').")
    import pysequoia as sq

    if not re.fullmatch(r"[^<>@\s]+@[^<>@\s]+\.[^<>@\s]+", email or ""):
        raise PGPFehler("Bitte eine gültige E-Mail-Adresse angeben.")
    tsk = sq.Tsk.generate(f"{name.strip()} <{email.strip()}>")
    oeffentlich = sq.armor(bytes(tsk.extract_certificate()), sq.ArmorKind.PublicKey)
    geheim = sq.armor(bytes(tsk), sq.ArmorKind.SecretKey)
    return oeffentlich, geheim


def ist_nachricht(daten: bytes) -> bool:
    return daten.lstrip().startswith(_KOPF_NACHRICHT.encode()) or daten[:1] in (b"\x84", b"\x85",
                                                                                  b"\xc1", b"\xc3")
