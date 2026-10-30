"""Build the folder distribution with PyInstaller and smoke-test it.

Usage: uv run --group build python packaging/build.py [--ohne-test]

Produces dist/AnonyMed/ (all platforms) and dist/AnonyMed.app (macOS).  The
smoke test runs the packaged command line (konfig, vorlage, anonymisieren on a
synthetic report) and starts the packaged interface with --selbsttest.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
DIST = WURZEL / "dist" / "AnonyMed"


def lauf(befehl: list[str], **kw) -> subprocess.CompletedProcess:
    print("+", " ".join(str(b) for b in befehl), flush=True)
    return subprocess.run(befehl, check=True, text=True, **kw)


def exe(name: str) -> Path:
    return DIST / (name + (".exe" if sys.platform.startswith("win") else ""))


def bauen() -> None:
    for alt in (WURZEL / "build", WURZEL / "dist"):
        if alt.exists():
            shutil.rmtree(alt)
    lauf([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
          str(WURZEL / "packaging" / "anonymed.spec")], cwd=WURZEL)


def app_plist_richten() -> None:
    """The bundle must start the window; PyInstaller may point it at the console program."""
    if sys.platform != "darwin":
        return
    import plistlib

    plist = WURZEL / "dist" / "AnonyMed.app" / "Contents" / "Info.plist"
    if not plist.exists():
        return
    daten = plistlib.loads(plist.read_bytes())
    if daten.get("CFBundleExecutable") != "anonymed-gui":
        daten["CFBundleExecutable"] = "anonymed-gui"
        plist.write_bytes(plistlib.dumps(daten))
        print("Info.plist: CFBundleExecutable auf anonymed-gui gesetzt")


def rauchtest() -> None:
    umgebung = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    if sys.platform == "darwin":
        import plistlib

        plist = WURZEL / "dist" / "AnonyMed.app" / "Contents" / "Info.plist"
        assert plistlib.loads(plist.read_bytes())["CFBundleExecutable"] == "anonymed-gui"
    with tempfile.TemporaryDirectory() as ordner:
        daten = Path(ordner)
        umgebung["ANONYMED_DATENORDNER"] = str(daten)
        aus = lauf([str(exe("anonymed")), "konfig"], capture_output=True, env=umgebung)
        print(aus.stdout)
        assert "AnonyMed" in aus.stdout and "OCR-Backends" in aus.stdout
        aus = lauf([str(exe("anonymed")), "vorlage"], capture_output=True, env=umgebung)
        assert '"nachname"' in aus.stdout
        # a synthetic report through the packaged pipeline
        sys.path.insert(0, str(WURZEL))
        from tests.synthetisch import erzeuge_befund, synthetischer_patient

        p = synthetischer_patient(7)
        befund = erzeuge_befund(daten / "befund.pdf", p)
        (daten / "patient.json").write_text(
            __import__("json").dumps(p.als_dict(), ensure_ascii=False), encoding="utf-8")
        aus = lauf([str(exe("anonymed")), "anonymisieren", str(befund), "--patient",
                    str(daten / "patient.json"), "--speicher", str(daten / "s.sqlite")],
                   capture_output=True, env=umgebung)
        assert "Gegenprobe: bestanden" in aus.stdout, aus.stdout + aus.stderr
        assert (daten / "befund_anonym.pdf").exists()
        gui = exe("anonymed-gui")
        aus = lauf([str(gui), "--selbsttest"], capture_output=True, env=umgebung)
        assert "Selbsttest bestanden" in aus.stdout, aus.stdout + aus.stderr
    print("Rauchtest bestanden.")


if __name__ == "__main__":
    bauen()
    app_plist_richten()
    if "--ohne-test" not in sys.argv:
        rauchtest()
    groesse = sum(f.stat().st_size for f in DIST.rglob("*") if f.is_file()) / 1e6
    print(f"dist/AnonyMed: {groesse:.0f} MB")
