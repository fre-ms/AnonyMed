"""Pseudonym codes: derivation rules that the detection must never trip over."""

from __future__ import annotations


def test_kennung_beginnt_nie_mit_az() -> None:
    """A code that starts with "AZ" would be taken for a file number by the label rule
    (and by the re-check on a scan); the derivation skips such codes deterministically."""
    from pseudonymed.pseudonyme.kennung import hash_kennung, kennung_gueltig

    schluessel = bytes(range(32))
    codes = [hash_kennung(schluessel, f"ort:test{i}") for i in range(3000)]
    assert not any(c.startswith("AZ") for c in codes)
    assert all(kennung_gueltig(c) for c in codes) and len(set(codes)) > 2990
    assert hash_kennung(schluessel, "ort:test7") == codes[7]  # still deterministic
