"""Index arithmetic. This is where the old pipeline bled."""

import pytest
from fixtures import SCHEDULE, make_doc

from draaiboek.ops import AddRow, RemoveRow, Source, UpdateRow
from draaiboek.reader import parse_document
from draaiboek.service import Draaiboek
from draaiboek.writer import (
    WriteError, anchor_for, cell_text_requests, plan_structural, plan_text,
)

SRC = Source(kind="email", ref="msg-1", quote="inloop 19:30")
BLANK = ["", "", ""]


def add(values, **kw):
    return AddRow(section=kw.pop("section", "Tijdschema"), values=values, source=SRC, **kw)


def simulate(rows, structurals):
    """Apply the planned batch the way Google does: in order, each request
    against the document as it stands at that point."""
    out = [list(r) for r in rows]
    for s in structurals:
        if s.kind == "insert":
            out.insert(s.row_index + 1, list(BLANK))
        else:
            out.pop(s.row_index)
    return out


def after_view(rows, spans):
    return parse_document(make_doc([rows], spans=[spans]))


# --- anchoring --------------------------------------------------------------
def test_default_anchor_is_the_last_data_row_not_the_band(view):
    """Appending to a section must land under its last row, never under the
    section header."""
    assert anchor_for(view, add(["23:00", "Schoonmaak", ""])).row_id == "t0r4"


def test_empty_section_anchors_on_its_column_header_not_the_band(view):
    """Anchoring on the band pushes the column header below the rows being
    added, and a row inserted below a band inherits its merged cell."""
    v = after_view([["Leveringen", "", ""], ["Tijd", "Activiteit", "Opmerkingen"]], {0: 3})
    a = anchor_for(v, add(["08:00", "Bloemen", ""], section="Leveringen"))
    assert a.kind == "header"


def test_section_with_no_header_falls_back_to_the_band(view):
    v = after_view([["Leveringen", "", ""]], {0: 3})
    assert anchor_for(v, add(["08:00", "Bloemen", ""], section="Leveringen")).kind == "band"


def test_unknown_section_names_the_real_sections(view):
    with pytest.raises(WriteError, match="Tijdschema"):
        anchor_for(view, add(["x", "y", "z"], section="Tijdsschema"))


# --- ordering ---------------------------------------------------------------
def test_structural_ops_run_in_descending_document_order(view):
    edits = [add(["23:00", "Schoonmaak", ""]),
             RemoveRow(row_id="t0r2", expect_contains="Inloop", reason="test")]
    plan = plan_structural(view, edits)
    positions = [s.doc_order for s in plan]
    assert positions == sorted(positions, reverse=True)


def test_same_anchor_inserts_are_reversed_so_they_land_in_op_order(view):
    """Three rows appended to one section all anchor on the same row. Applied
    naively they come out backwards."""
    edits = [add(["21:00", "Pauze", ""]), add(["21:30", "Hervatting", ""]),
             add(["22:30", "Einde show", ""])]
    plan = plan_structural(view, edits)
    assert [s.op_index for s in plan] == [2, 1, 0]


# --- the new-row location problem ------------------------------------------
def _locate(view, edits):
    plan = plan_structural(view, edits)
    rows_after = simulate(SCHEDULE, plan)
    # bands shift down by the number of rows inserted above them
    spans = {i: 3 for i, r in enumerate(rows_after) if r[0] in ("Tijdschema", "Bijzonderheden")}
    v2 = after_view(rows_after, spans)
    return Draaiboek._locate_new_rows(view, v2, plan, edits), v2, rows_after


def test_single_insert_lands_in_the_right_place(view):
    edits = [add(["23:00", "Schoonmaak", ""])]
    new, v2, rows = _locate(view, edits)
    assert new[0] == "t0r5"
    assert rows[5] == BLANK


def test_three_appends_keep_their_order(view):
    edits = [add(["21:00", "Pauze", ""]), add(["21:30", "Hervatting", ""]),
             add(["22:30", "Einde show", ""])]
    new, v2, rows = _locate(view, edits)
    assert [new[0], new[1], new[2]] == ["t0r5", "t0r6", "t0r7"]


def test_insert_and_delete_in_one_batch(view):
    edits = [add(["23:00", "Schoonmaak", ""]),
             RemoveRow(row_id="t0r2", expect_contains="Inloop", reason="Larissa vroeg dit")]
    new, v2, rows = _locate(view, edits)
    assert rows[v2.row(new[0]).index] == BLANK
    assert not any("Inloop" in c for r in rows for c in r)


def test_refuses_to_write_into_a_row_that_is_not_blank(view):
    """If the computed position holds content, something is wrong with our
    model of the document. Stop, do not overwrite."""
    plan = plan_structural(view, [add(["23:00", "Schoonmaak", ""])])
    unchanged = parse_document(make_doc([SCHEDULE], spans=[{0: 3, 5: 3}]))
    with pytest.raises(WriteError, match="Refusing to overwrite"):
        Draaiboek._locate_new_rows(view, unchanged, plan, [add(["23:00", "Schoonmaak", ""])])


# --- text requests ----------------------------------------------------------
def test_cell_writes_are_emitted_high_index_first(view):
    row, table = view.row("t0r2"), view.tables[0]
    reqs = cell_text_requests(row, table, {0: "19:45", 2: "foyer open"}, None)
    idx = [r["insertText"]["location"]["index"] for r in reqs if "insertText" in r]
    assert idx == sorted(idx, reverse=True)


def test_empty_cells_get_no_delete_request(view):
    """An empty cell has endIndex == startIndex + 2. Deleting that range is a
    400 from the API."""
    row, table = view.row("t0r3"), view.tables[0]
    reqs = cell_text_requests(row, table, {2: "nieuw"}, None)
    assert not [r for r in reqs if "deleteContentRange" in r]


def test_columns_may_be_named(view):
    row, table = view.row("t0r2"), view.tables[0]
    reqs = plan_text(view, [UpdateRow(row_id="t0r2", expect_contains="Inloop",
                                      set_values={"Activiteit": "Inloop gasten in foyer"},
                                      source=SRC)], {})
    assert any(r.get("insertText", {}).get("text") == "Inloop gasten in foyer" for r in reqs)


def test_unknown_column_name_lists_the_real_columns(view):
    with pytest.raises(WriteError, match="Activiteit"):
        plan_text(view, [UpdateRow(row_id="t0r2", expect_contains="Inloop",
                                   set_values={"Tijdstip": "x"}, source=SRC)], {})


def test_category_shading_is_applied_to_the_whole_row(view):
    row, table = view.row("t0r2"), view.tables[0]
    reqs = cell_text_requests(row, table, {}, __import__("draaiboek.ops", fromlist=["Category"]).Category.HOSPITALITY)
    style = [r for r in reqs if "updateTableCellStyle" in r][0]["updateTableCellStyle"]
    assert style["tableRange"]["columnSpan"] == 3
    assert style["tableCellStyle"]["backgroundColor"]["color"]["rgbColor"]["green"] > 0.9


def test_a_chapter_written_as_a_heading_can_still_be_appended_to():
    """The new template puts the chapter name in a heading above its table,
    so there is no band row inside it to anchor on."""
    from draaiboek.reader import parse_document
    doc = make_doc([[["TIJD", "WAT", "WIE"]]])
    doc["body"]["content"].insert(1, {
        "startIndex": 1, "endIndex": 12,
        "paragraph": {"paragraphStyle": {"namedStyleType": "HEADING_2"},
                      "elements": [{"textRun": {"content": "Tijdschema\n"}}]}})
    v = parse_document(doc)
    assert v.section_by_name("Tijdschema") is not None
    a = anchor_for(v, add(["19:00", "Inloop", "Host"], section="Tijdschema"))
    assert a.kind in ("header", "data")
