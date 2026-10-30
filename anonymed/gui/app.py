"""Entry point of the desktop interface: ``anonymed gui`` or ``anonymed-gui``."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("Die Oberfläche braucht das Extra 'gui': uv sync --extra gui", file=sys.stderr)
        return 1
    from anonymed.gui.hauptfenster import Hauptfenster

    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("AnonyMed")
    app.setOrganizationName("AnonyMed")
    fenster = Hauptfenster()
    fenster.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
