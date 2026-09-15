"""Google Docs document -> DocView.

Pure function of the API response; no network, no credentials, fully testable.

Unlike the old DocMap it indexes *every* table in the document, not just the
main schedule table. Docs with a separate "Catering Overzicht" or "Inrichting
Overzicht" table used to raise UnknownSection because those tables were
invisible to the map; here they are ordinary tables with ordinary row ids.
"""

from __future__ import annotations

from typing import Any

from .model import Cell, DocView, Row, Section, Table, slugify

HEADER_WORDS = {
    "tijd", "tijdstip", "activiteit", "omschrijving", "notities", "opmerkingen",
    "catering", "naam", "telefoon", "telefoonnummer", "aanwezig", "overige info",
    "rol", "functie", "wie", "wat", "leverancier", "levering", "aantal",
    "antwoord", "status",
}


def _skeleton_headers() -> set[str]:
    # Every column label the blank draaiboek uses must be recognisable, or a
    # chapter's header row is read as content (and becomes editable, deletable
    # and addressable by name-less column indices only).
    from .skeleton import CHAPTERS
    return {h.strip().lower() for _, headers, _ in CHAPTERS for h in headers}


HEADER_WORDS |= _skeleton_headers()


def _rgb(style: dict | None) -> str | None:
    try:
        c = style["backgroundColor"]["color"]["rgbColor"]  # type: ignore[index]
    except (KeyError, TypeError):
        return None
    r, g, b = (int(round(c.get(k, 0.0) * 255)) for k in ("red", "green", "blue"))
    return f"#{r:02x}{g:02x}{b:02x}"


def _text_of(content: list[dict] | None) -> str:
    """Concatenate the text of a sequence of structural elements."""
    if not content:
        return ""
    parts: list[str] = []
    for el in content:
        if "paragraph" in el:
            for pe in el["paragraph"].get("elements", []):
                tr = pe.get("textRun")
                if tr:
                    parts.append(tr.get("content", ""))
                elif "inlineObjectElement" in pe:
                    parts.append("⬜")  # an image lives here
        elif "table" in el:
            parts.append("[nested table]")
    return "".join(parts).replace("\v", "\n").strip()


def _is_band(cells: list[Cell], columns: int) -> bool:
    if any(c.column_span >= columns > 1 for c in cells):
        return True
    filled = [i for i, c in enumerate(cells) if c.text.strip()]
    return filled == [0] and len(cells) > 1


def _is_header(cells: list[Cell]) -> bool:
    vals = [c.text.strip().lower() for c in cells if c.text.strip()]
    if not vals:
        return False
    hits = sum(1 for v in vals if v in HEADER_WORDS)
    return hits >= max(2, len(vals) - 1)


def parse_document(doc: dict[str, Any]) -> DocView:
    tables: list[Table] = []
    sections: list[Section] = []
    warnings: list[str] = []

    elements = doc.get("body", {}).get("content", [])
    t_idx = 0
    for el in elements:
        tbl = el.get("table")
        if not tbl:
            continue
        columns = tbl.get("columns", 0) or max(
            (len(r.get("tableCells", [])) for r in tbl.get("tableRows", [])), default=0
        )
        rows: list[Row] = []
        current_section: str | None = None
        seen_data_in_section = False

        for r_idx, tr in enumerate(tbl.get("tableRows", [])):
            cells = [
                Cell(
                    text=_text_of(tc.get("content")),
                    start_index=tc.get("startIndex", 0),
                    end_index=tc.get("endIndex", 0),
                    column_span=(tc.get("tableCellStyle") or {}).get("columnSpan", 1),
                    background=_rgb(tc.get("tableCellStyle")),
                )
                for tc in tr.get("tableCells", [])
            ]
            if _is_band(cells, columns):
                kind = "band"
                current_section = cells[0].text.strip() if cells else None
                seen_data_in_section = False
            elif not seen_data_in_section and _is_header(cells):
                kind = "header"
            else:
                kind = "data"
                seen_data_in_section = True

            row = Row(
                row_id=f"t{t_idx}r{r_idx}",
                table=t_idx,
                index=r_idx,
                kind=kind,  # type: ignore[arg-type]
                section=current_section,
                cells=cells,
                start_index=tr.get("startIndex", 0),
                end_index=tr.get("endIndex", 0),
            )
            rows.append(row)

            if kind == "band" and current_section:
                sections.append(Section(
                    name=current_section, slug=slugify(current_section),
                    table=t_idx, band_row_id=row.row_id,
                ))

        headers = next((r.values for r in rows if r.kind == "header"), [])
        role = "unknown"
        low = {h.strip().lower() for h in headers}
        if {"naam"} & low and {"telefoon", "telefoonnummer"} & low:
            role = "call_sheet"
        elif {"tijd", "tijdstip"} & low or {"activiteit"} & low:
            role = "schedule"

        tables.append(Table(
            index=t_idx, start_index=el.get("startIndex", 0), end_index=el.get("endIndex", 0),
            columns=columns, rows=rows, headers=headers, role=role,
        ))
        t_idx += 1

    # attach data rows to their sections
    by_key = {(s.table, s.band_row_id): s for s in sections}
    for t in tables:
        cur: Section | None = None
        for r in t.rows:
            if r.kind == "band":
                cur = by_key.get((t.index, r.row_id))
            elif r.kind == "data" and cur is not None:
                cur.row_ids.append(r.row_id)

    if not tables:
        warnings.append("No tables found. This does not look like a draaiboek.")
    dupes = {s.slug for s in sections if [x.slug for x in sections].count(s.slug) > 1}
    if dupes:
        warnings.append(
            f"Duplicate section names: {sorted(dupes)}. Target these by row id, "
            f"not by section name."
        )

    return DocView(
        doc_id=doc.get("documentId", ""),
        revision_id=doc.get("revisionId", ""),
        title=doc.get("title", ""),
        tables=tables,
        sections=sections,
        warnings=warnings,
    )
