"""Loading documents and reading their text layer."""

from anonymed.dokument.laden import oeffne
from anonymed.dokument.textebene import Seitentext, lese_dokument, lese_seite

__all__ = ["Seitentext", "lese_dokument", "lese_seite", "oeffne"]
