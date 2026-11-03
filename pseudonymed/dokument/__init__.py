"""Loading documents and reading their text layer."""

from pseudonymed.dokument.laden import oeffne
from pseudonymed.dokument.textebene import Seitentext, lese_dokument, lese_seite

__all__ = ["Seitentext", "lese_dokument", "lese_seite", "oeffne"]
