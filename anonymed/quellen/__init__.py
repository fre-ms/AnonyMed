"""Where a patient's identifiers come from: manual files now, vCard and CardDAV in M2."""

from anonymed.quellen.manuell import lade_patient, vorlage

__all__ = ["lade_patient", "vorlage"]
