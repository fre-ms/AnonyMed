"""The audit report: counts per page and rule, pseudonyms, result of the check."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from pseudonymed import WERKZEUG
from pseudonymed.kern.modell import Fundstelle, Gegenprobe, Pruefbericht


def erstelle_bericht(
    quelle: Path,
    ziel: Path | None,
    seiten: int,
    textebene: bool,
    fundstellen: list[Fundstelle],
    gegenprobe: Gegenprobe | None,
    optionen: dict[str, Any] | None = None,
    warnungen: list[str] | None = None,
    hinweise: list[str] | None = None,
    restrisiko: dict[str, Any] | None = None,
) -> Pruefbericht:
    pseudonyme = sorted({f.pseudonym for f in fundstellen})
    return Pruefbericht(
        quelle=quelle.name,
        ziel=ziel.name if ziel else "",
        seiten=seiten,
        zeitpunkt=Pruefbericht.jetzt(),
        werkzeug=WERKZEUG,
        textebene=textebene,
        fundstellen=[f.ohne_klartext() for f in fundstellen],
        pseudonyme=pseudonyme,
        gegenprobe=gegenprobe,
        optionen=optionen or {},
        warnungen=list(warnungen or []),
        hinweise=list(hinweise or []),
        restrisiko=restrisiko,
    )


def als_text(b: Pruefbericht) -> str:
    zeilen = [
        f"{b.werkzeug} – Prüfbericht",
        f"Quelle: {b.quelle}",
        f"Ziel: {b.ziel or '(nicht freigegeben)'}",
        f"Seiten: {b.seiten}    Textebene: {'ja' if b.textebene else 'nein'}",
        f"Zeitpunkt: {b.zeitpunkt}",
        f"Treffer: {len(b.fundstellen)}",
    ]
    je_seite: dict[int, Counter[str]] = {}
    for f in b.fundstellen:
        je_seite.setdefault(f["seite"], Counter())[f["regel"]] += 1
    for seite in sorted(je_seite):
        regeln = ", ".join(f"{r} {n}" for r, n in sorted(je_seite[seite].items()))
        zeilen.append(f"  Seite {seite}: {sum(je_seite[seite].values())}  ({regeln})")
    zeilen.append("Pseudonyme: " + (", ".join(b.pseudonyme) if b.pseudonyme else "–"))
    g = b.gegenprobe
    if g is None:
        zeilen.append("Gegenprobe: nicht durchgeführt")
    elif g.bestanden:
        zeilen.append("Gegenprobe: bestanden")
        if g.geprueft:
            zeilen.append("  Erneut geprüft nach dem Prüfschritt: " + ", ".join(g.geprueft)
                          + " – nichts gefunden")
        if g.belassen:
            zaehlung = Counter(g.belassen)
            zeilen.append("  Bewusst belassen (im Prüfschritt verworfen): "
                          + ", ".join(f"{r} ×{n}" for r, n in sorted(zaehlung.items())))
    else:
        zeilen.append(f"Gegenprobe: NICHT bestanden ({len(g.resttreffer)} Resttreffer)"
                      " – nichts exportiert")
        for r in g.resttreffer[:20]:
            zeilen.append(f"  Seite {r['seite']}: {r['regel']} ({r['quelle']})")
    if g and g.anmerkungen:
        zeilen.extend(f"  Anmerkung: {a}" for a in g.anmerkungen)
    if b.restrisiko:
        from pseudonymed.pruefung.restrisiko import Restrisiko

        zeilen.extend(Restrisiko.aus_dict(b.restrisiko).als_zeilen())
    if b.hinweise:
        zeilen.append("Hinweise (durchgeführte Schritte):")
        zeilen.extend(f"  - {h}" for h in b.hinweise)
    if b.warnungen:
        zeilen.append("Warnungen:")
        zeilen.extend(f"  - {w}" for w in b.warnungen)
    return "\n".join(zeilen) + "\n"


def speichere_bericht(b: Pruefbericht, pfad: Path) -> None:
    pfad.write_text(json.dumps(b.als_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
