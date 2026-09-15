"""Ops -> Google Docs batchUpdate requests.

Two problems killed the old writer, both solved structurally here:

  Index shifting. Inserting a table row invalidates every index after it.
  Solved by phasing: all structural changes first (each against a freshly
  read document), then one text pass computed from a final re-read. No
  sleeps, no guessing -- we poll for the expected row count instead.

  Anchoring on a band. Inserting after a section header collapsed the text
  into cell 0. Anchors are validated as data rows in guard.py, and the
  default anchor is the last data row of the section.
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import DocView, Row, Table
from .ops import AddRow, Category, Op, RemoveRow, ReplaceText, UpdateRow

def _hex(h: str) -> tuple[float, float, float]:
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


# Taken from Larissa's own document, not invented. Changing these changes the
# look of every draaiboek, so they are written as the hex she actually uses.
BAND_HEX = "2d3c4f"      # navy chapter band, white text
HEADER_HEX = "eff0f1"    # column-header row
CATEGORY_HEX = {
    Category.TECHNIEK:    "f4cccc",   # Techniek & Media
    Category.HOSPITALITY: "d9ead3",   # Hospitality & Catering
    Category.INRICHTING:  "cfe2f3",   # Inrichting & Logistiek
    Category.ALGEMEEN:    "ffffff",   # Algemeen
}
BG: dict[Category, tuple[float, float, float]] = {
    c: _hex(h) for c, h in CATEGORY_HEX.items()
}


class WriteError(RuntimeError):
    pass


@dataclass
class Structural:
    """One row insert or delete. Applied one batch at a time, in document
    order descending, so earlier requests never invalidate later ones."""

    kind: str          # "insert" | "delete"
    table_start: int
    row_index: int
    doc_order: int     # sort key: position in the document
    op_index: int


def _resolve_column(table: Table, key: str) -> int:
    """Accept a column index ('1') or a header name ('Activiteit')."""
    if key.isdigit():
        return int(key)
    want = key.strip().lower()
    for i, h in enumerate(table.headers):
        if h.strip().lower() == want:
            return i
    raise WriteError(
        f"Unknown column {key!r}. Columns in this table: "
        f"{table.headers or list(range(table.columns))}"
    )


def anchor_for(view: DocView, op: AddRow) -> Row:
    """The data row the new row is inserted below."""
    if op.after_row_id:
        row = view.row(op.after_row_id)
        if row is None:
            raise WriteError(f"Unknown row {op.after_row_id!r}")
        return row
    section = view.section_by_name(op.section)
    if section is None:
        raise WriteError(
            f"Unknown section {op.section!r}. Sections in this doc: {view.section_names()}"
        )
    if not section.row_ids:
        # The section has no data yet. Anchor on its column-header row if it has
        # one -- anchoring on the band instead pushes the header down below the
        # rows we are adding, and a row inserted below a band inherits the
        # band's merged cell, collapsing all text into column 0.
        band = view.row(section.band_row_id)
        if band is None:
            raise WriteError(f"Section {op.section!r} has no band row")
        table = view.tables[band.table]
        anchor = band
        for row in table.rows[band.index + 1:]:
            if row.kind == "header":
                anchor = row
            else:
                break
        return anchor
    last = view.row(section.row_ids[-1])
    assert last is not None
    return last


def plan_structural(view: DocView, edits: list[Op]) -> list[Structural]:
    # "Replace the last row of this section" is an ordinary request: the anchor
    # an append lands on may be one this same batch removes. Walk back to the
    # previous surviving data row instead of refusing the whole batch.
    doomed = {op.row_id for op in edits if isinstance(op, RemoveRow)}

    def survivor(row: Row) -> Row:
        if row.row_id not in doomed:
            return row
        table = view.tables[row.table]
        for prev in reversed(table.rows[:row.index]):
            if prev.row_id not in doomed and prev.kind in ("data", "header"):
                return prev
        return view.row(next(s.band_row_id for s in view.sections
                             if s.table == row.table
                             and row.row_id in s.row_ids)) or row

    out: list[Structural] = []
    for i, op in enumerate(edits):
        if isinstance(op, AddRow):
            anchor = survivor(anchor_for(view, op))
            table = view.tables[anchor.table]
            out.append(Structural("insert", table.start_index, anchor.index,
                                  anchor.start_index, i))
        elif isinstance(op, RemoveRow):
            row = view.row(op.row_id)
            if row is None:
                raise WriteError(f"Unknown row {op.row_id!r}")
            table = view.tables[row.table]
            out.append(Structural("delete", table.start_index, row.index,
                                  row.start_index, i))
    # Descending document order, so a later change never shifts an earlier one.
    # Ties matter: several rows appended to the same section share an anchor,
    # and each insertBelow lands directly under it -- so the last one applied
    # ends up first. Emitting ties in reverse op order cancels that out.
    out.sort(key=lambda s: (s.doc_order, s.op_index), reverse=True)
    return out


def structural_request(s: Structural) -> dict:
    loc = {
        "tableCellLocation": {
            "tableStartLocation": {"index": s.table_start},
            "rowIndex": s.row_index,
            "columnIndex": 0,
        }
    }
    if s.kind == "insert":
        return {"insertTableRow": {**loc, "insertBelow": True}}
    return {"deleteTableRow": loc}


def cell_text_requests(row: Row, table: Table, values: dict[int, str],
                       category: Category | None) -> list[dict]:
    """Replace the text of specific cells of one row, plus optional shading.

    Emitted in descending index order so the requests inside this batch do not
    invalidate each other.
    """
    reqs: list[dict] = []
    for col in sorted(values.keys(), reverse=True):
        if col >= len(row.cells):
            raise WriteError(
                f"Column {col} out of range for row {row.row_id} "
                f"({len(row.cells)} columns)"
            )
        cell = row.cells[col]
        start, end = cell.start_index + 1, cell.end_index - 1
        if end > start:
            reqs.append({"deleteContentRange": {"range": {"startIndex": start, "endIndex": end}}})
        text = values[col].rstrip("\n")
        if text:
            reqs.append({"insertText": {"location": {"index": start}, "text": text}})
            # A row inserted below a section band inherits the band's white bold
            # text, which is invisible on a pale row. Set it back to document ink.
            reqs.append({"updateTextStyle": {
                "range": {"startIndex": start, "endIndex": start + len(text)},
                "textStyle": {"bold": False, "foregroundColor": {"color": {
                    "rgbColor": {"red": 0.098, "green": 0.114, "blue": 0.133}}}},
                "fields": "bold,foregroundColor"}})

    if category is not None:
        r, g, b = BG[category]
        reqs.append({
            "updateTableCellStyle": {
                "tableRange": {
                    "tableCellLocation": {
                        "tableStartLocation": {"index": table.start_index},
                        "rowIndex": row.index,
                        "columnIndex": 0,
                    },
                    "rowSpan": 1,
                    "columnSpan": len(row.cells),
                },
                "tableCellStyle": {
                    "backgroundColor": {"color": {"rgbColor": {"red": r, "green": g, "blue": b}}}
                },
                "fields": "backgroundColor",
            }
        })
    return reqs


def replace_text_request(op: ReplaceText) -> dict:
    return {
        "replaceAllText": {
            "containsText": {"text": op.find, "matchCase": op.match_case},
            "replaceText": op.replace,
        }
    }


def plan_text(view: DocView, edits: list[Op], new_rows: dict[int, str]) -> list[dict]:
    """Second phase: all text and shading, computed from a post-structural read.

    `new_rows` maps the index of an AddRow op to the row id it created.
    """
    per_row: list[tuple[int, list[dict]]] = []
    tail: list[dict] = []

    for i, op in enumerate(edits):
        if isinstance(op, AddRow):
            row_id = new_rows.get(i)
            if row_id is None:
                raise WriteError(f"No row was created for edit {i}")
            row = view.row(row_id)
            if row is None:
                raise WriteError(f"Created row {row_id} vanished before the text pass")
            table = view.tables[row.table]
            # Some chapters are two-column tables. Dropping the values that do
            # not fit loses the content silently -- the entire wine order for
            # 18 September vanished this way. Fold the remainder into the last
            # cell instead.
            n = len(row.cells)
            vals = {c: v for c, v in enumerate(op.values[:n])}
            if len(op.values) > n and n:
                rest = [v for v in op.values[n:] if v.strip()]
                if rest:
                    vals[n - 1] = " · ".join(x for x in [vals.get(n - 1, "")] + rest if x)
            per_row.append((row.start_index, cell_text_requests(row, table, vals, op.category)))

        elif isinstance(op, UpdateRow):
            row = view.row(op.row_id)
            if row is None:
                raise WriteError(f"Unknown row {op.row_id!r} in the text pass")
            table = view.tables[row.table]
            vals = {_resolve_column(table, k): v for k, v in op.set_values.items()}
            per_row.append((row.start_index, cell_text_requests(row, table, vals, op.category)))

        elif isinstance(op, ReplaceText):
            # index-independent, so order does not matter; run them last
            tail.append(replace_text_request(op))

    # rows in descending document order
    per_row.sort(key=lambda x: x[0], reverse=True)
    return [r for _, reqs in per_row for r in reqs] + tail
