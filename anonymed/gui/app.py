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

    argumente = list(argv if argv is not None else sys.argv[1:])
    selbsttest = "--selbsttest" in argumente
    if selbsttest:
        argumente.remove("--selbsttest")
    app = QApplication.instance() or QApplication([sys.argv[0], *argumente])
    app.setApplicationName("AnonyMed")
    app.setOrganizationName("AnonyMed")
    fenster = Hauptfenster()
    fenster.show()
    if selbsttest:
        # packaged builds: construct everything, render once, report, leave
        app.processEvents()
        tabs = [fenster.tabs.tabText(i) for i in range(fenster.tabs.count())]
        print("Selbsttest bestanden:", ", ".join(tabs))
        fenster.close()
        return 0
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
