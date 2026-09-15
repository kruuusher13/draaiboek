"""Synthetic Google Docs documents with realistic index arithmetic.

The index model mirrors the real API: a cell occupies
    1 (cell marker) + len(text) + 1 (paragraph newline)
so an empty cell satisfies endIndex == startIndex + 2, which is the invariant
writer.cell_text_requests relies on.
"""

from __future__ import annotations



def _cell(text: str, idx: int, span: int = 1) -> tuple[dict, int]:
    start = idx
    end = idx + 1 + len(text) + 1
    cell = {
        "startIndex": start,
        "endIndex": end,
        "tableCellStyle": {"columnSpan": span},
        "content": [{"paragraph": {"elements": [{"textRun": {"content": text + "\n"}}]}}]
        if text else [{"paragraph": {"elements": []}}],
    }
    return cell, end


def make_table(rows: list[list[str]], start: int, columns: int | None = None,
               spans: dict[int, int] | None = None) -> tuple[dict, int]:
    columns = columns or max(len(r) for r in rows)
    spans = spans or {}
    idx = start + 1
    trs = []
    for r_i, row in enumerate(rows):
        r_start = idx
        idx += 1
        cells = []
        span = spans.get(r_i, 1)
        for c_i, text in enumerate(row):
            cell, idx = _cell(text, idx, span if c_i == 0 else 1)
            cells.append(cell)
        trs.append({"startIndex": r_start, "endIndex": idx, "tableCells": cells})
    return {"rows": len(rows), "columns": columns, "tableRows": trs}, idx


def make_doc(tables: list[list[list[str]]], *, revision="rev-1", doc_id="DOC1",
             spans: list[dict[int, int]] | None = None) -> dict:
    content = [{"startIndex": 0, "endIndex": 1, "sectionBreak": {}}]
    idx = 1
    for t_i, rows in enumerate(tables):
        tbl, end = make_table(rows, idx, spans=(spans or [{}] * len(tables))[t_i])
        content.append({"startIndex": idx, "endIndex": end, "table": tbl})
        idx = end + 1
    return {"documentId": doc_id, "title": "Draaiboek test",
            "revisionId": revision, "body": {"content": content}}


SCHEDULE = [
    ["Tijdschema", "", ""],                       # 0 band
    ["Tijd", "Activiteit", "Opmerkingen"],        # 1 header
    ["19:30", "Inloop gasten", "foyer"],          # 2 data
    ["20:30", "Aanvang show", ""],                # 3 data
    ["22:30", "Einde", ""],                       # 4 data
    ["Bijzonderheden", "", ""],                   # 5 band
    ["", "Larissa's notities", ""],               # 6 data
]


