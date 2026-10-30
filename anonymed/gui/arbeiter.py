"""Run a callable in a Qt thread and hand back the result or the exception."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal


class Arbeiter(QThread):
    fertig = Signal(object)
    fehler = Signal(object)

    def __init__(self, aufgabe: Callable[[], Any], eltern: QObject | None = None) -> None:
        super().__init__(eltern)
        self._aufgabe = aufgabe

    def run(self) -> None:  # noqa: D102
        try:
            self.fertig.emit(self._aufgabe())
        except Exception as e:  # shown to the user by the page
            self.fehler.emit(e)
