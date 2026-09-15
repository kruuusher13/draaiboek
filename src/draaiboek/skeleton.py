"""The draaiboek structure, taken from Larissa's own document.

Each chapter is its own table: a full-width navy band, a grey column-header
row, then data rows. That is how she builds them, so that is what this builds.

Her changes of 13 September 2026 are applied:
  * Programma and Tijdschema are one chapter, not two.
  * "Na afloop" is no longer a chapter -- those rows go in Programma.
  * Inrichting always starts with five lines.
  * LEVERINGEN is its own chapter, at the bottom.
  * The call sheet's "Aanwezig" column is "Overige info".
"""

from __future__ import annotations

from typing import Any

# name, columns, header labels, default blank data rows
CHAPTERS: list[tuple[str, list[str], int]] = [
    ("Opbouw",        ["RUIMTE", "DETAILS", "OPMERKINGEN"],          0),
    ("Programma",     ["TIJD", "ACTIVITEIT", "OPMERKINGEN"],         0),
    ("Techniek",      ["TIJD", "ACTIVITEIT", "OPMERKINGEN"],         0),
    ("Inrichting",    ["RUIMTE", "DETAILS", "OPMERKINGEN"],          5),
    ("Catering",      ["TIJD", "DETAILS", "OPMERKINGEN"],            0),
    ("Vaste afspraken", ["ONDERWERP", "DETAILS", "OPMERKINGEN"],     0),
    ("Open Punten",   ["ONDERWERP", "VRAAG", "OPMERKINGEN"],         0),
    ("Call Sheet",    ["NAAM", "ROL", "TELEFOON", "OVERIGE INFO"],   0),
    ("Leveringen",    ["TIJD", "LEVERANCIER", "GOEDEREN"],           0),
]

# Column widths in points, proportional to her document, for a landscape page.
WIDTHS: dict[int, list[int]] = {
    3: [110, 380, 190],
    4: [170, 200, 150, 160],
}

INRICHTING_DEFAULT_ROWS = 5


def blank(title: str = "") -> list[dict[str, Any]]:
    """One table per chapter, matching her layout."""
    tables: list[dict[str, Any]] = []
    for name, headers, defaults in CHAPTERS:
        n = len(headers)
        rows: list[list[str]] = [[name] + [""] * (n - 1), list(headers)]
        rows += [[""] * n for _ in range(defaults)]
        tables.append({"columns": n, "rows": rows, "spans": {"0": n},
                       "widths": WIDTHS.get(n, [])})
    return tables


def chapter_names() -> list[str]:
    return [c[0] for c in CHAPTERS]
