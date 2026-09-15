"""What `read` returns. A flat, addressable view of a document.

Row addressing is structural (`t0r14`), never fuzzy text matching. The old
pipeline matched rows by activity text, which silently failed whenever the
text being matched lived in a different column (`NoMatch` on a row that was
plainly there). Structural ids cannot do that.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from typing import Any, Literal

RowKind = Literal["band", "header", "data"]


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower().strip())
    return s.strip("-") or "sectie"


@dataclass
class Cell:
    text: str
    start_index: int
    end_index: int
    column_span: int = 1
    background: str | None = None  # "#rrggbb"


@dataclass
class Row:
    row_id: str            # "t0r14" -- table ordinal, row ordinal
    table: int
    index: int             # row index within its table
    kind: RowKind
    section: str | None    # section band this row lives under
    cells: list[Cell]
    start_index: int
    end_index: int

    @property
    def values(self) -> list[str]:
        return [c.text for c in self.cells]

    def joined(self) -> str:
        return " | ".join(c.text for c in self.cells if c.text)

    def fingerprint(self) -> str:
        """Content hash, used for tombstones and drift detection."""
        norm = "␟".join(re.sub(r"\s+", " ", c.text).strip().lower() for c in self.cells)
        return hashlib.sha1(norm.encode()).hexdigest()[:12]


@dataclass
class Table:
    index: int
    start_index: int
    end_index: int
    columns: int
    rows: list[Row]
    headers: list[str] = field(default_factory=list)
    role: str = "unknown"  # "schedule" | "call_sheet" | "unknown"


@dataclass
class Section:
    name: str
    slug: str
    table: int
    band_row_id: str
    row_ids: list[str] = field(default_factory=list)


@dataclass
class DocView:
    doc_id: str
    revision_id: str
    title: str
    tables: list[Table]
    sections: list[Section]
    warnings: list[str] = field(default_factory=list)

    # -- lookup ------------------------------------------------------------
    def row(self, row_id: str) -> Row | None:
        for t in self.tables:
            for r in t.rows:
                if r.row_id == row_id:
                    return r
        return None

    def section_by_name(self, name: str) -> Section | None:
        want = slugify(name)
        exact = [s for s in self.sections if s.slug == want]
        if exact:
            return exact[0]
        partial = [s for s in self.sections if want in s.slug or s.slug in want]
        return partial[0] if len(partial) == 1 else None

    def section_names(self) -> list[str]:
        return [s.name for s in self.sections]

    def data_rows(self) -> list[Row]:
        return [r for t in self.tables for r in t.rows if r.kind == "data"]

    # -- serialisation -----------------------------------------------------
    def to_dict(self, *, compact: bool = True) -> dict[str, Any]:
        """Compact form is what the agent sees: ids + text, no indices.

        Document indices are deliberately withheld from the agent. They are an
        implementation detail of the writer, and exposing them invites exactly
        the raw-batchUpdate improvisation that produced the current mess.
        """
        if not compact:
            return asdict(self)
        return {
            "doc_id": self.doc_id,
            "revision_id": self.revision_id,
            "title": self.title,
            "sections": [
                {"name": s.name, "rows": s.row_ids} for s in self.sections
            ],
            "rows": [
                {
                    "row_id": r.row_id,
                    "section": r.section,
                    "kind": r.kind,
                    "values": r.values,
                }
                for t in self.tables
                for r in t.rows
            ],
            "tables": [
                {"table": t.index, "role": t.role, "columns": t.columns,
                 "headers": t.headers, "rows": len(t.rows)}
                for t in self.tables
            ],
            "warnings": self.warnings,
        }
