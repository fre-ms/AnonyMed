"""Where a patient's identifiers come from: manual files, local vCards, CardDAV."""

from pseudonymed.quellen.manuell import lade_patient, vorlage
from pseudonymed.quellen.vcf import lade_vcf, parse_vcards, patient_aus_vcard, patienten_aus_vcf

__all__ = [
    "lade_patient",
    "lade_vcf",
    "parse_vcards",
    "patient_aus_vcard",
    "patienten_aus_vcf",
    "vorlage",
]
