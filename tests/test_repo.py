"""Repository hygiene: the local handover notes never become part of the tree."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

WURZEL = Path(__file__).resolve().parent.parent


def test_uebergabe_bleibt_lokal() -> None:
    if shutil.which("git") is None or not (WURZEL / ".git").exists():
        pytest.skip("kein Git-Arbeitsverzeichnis")
    r = subprocess.run(["git", "ls-files", "--", "CLAUDE.md", "**/CLAUDE.md"], cwd=WURZEL,
                       capture_output=True, text=True, check=True)
    assert r.stdout.strip() == "", "CLAUDE.md ist versioniert"
