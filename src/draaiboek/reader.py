"""Google Docs document -> DocView.

Pure function of the API response; no network, no credentials, fully testable.

Unlike the old DocMap it indexes *every* table in the document, not just the
main schedule table. Docs with a separate "Catering Overzicht" or "Inrichting
Overzicht" table used to raise UnknownSection because those tables were
invisible to the map; here they are ordinary tables with ordinary row ids.
"""

from __future__ import annotations

import re
from typing import Any

from .model import Cell, DocView, Row, Section, Table, slugify

HEADER_WORDS = {
    "tijd", "tijdstip", "activiteit", "omschrijving", "notities", "opmerkingen",
    "catering", "naam", "telefoon", "telefoonnummer", "aanwezig", "overige info",
    "rol", "functie", "wie", "wat", "leverancier", "levering", "aantal",
    "antwoord", "status", "van", "tot", "wanneer", "goederen", "ruimte",
    "opstelling", "sessie", "kaarten", "onderwerp", "vraag", "details", "gang",
    # the English half of a bilingual draaiboek
    "time", "to", "from", "what", "who", "notes", "name", "role", "phone",
    "on site", "qty", "when", "supplier", "goods", "area", "setup", "session",
    "tickets", "subject", "question",
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


def _is_header(cells: list[Cell], below: list[Cell] | None = None) -> bool:
    vals = [c.text.strip().lower() for c in cells if c.text.strip()]
    if not vals:
        return False
    hits = sum(1 for v in vals if v in HEADER_WORDS)
    if hits >= max(2, len(vals) - 1):
        return True
    # Not every draaiboek labels its columns "Tijd / Activiteit". Some say
    # "Gang / Omschrijving" or "Naam / Dieetwens". A shaded first row above
    # unshaded rows, with no times or numbers in it, is a column header.
    if not cells:
        return False
    # An empty chapter is a single shaded row with nothing beneath it. That is
    # still a header, not content -- otherwise a blank template reports itself
    # as full of somebody else's event.
    if below is None or not below:
        shade = cells[0].background
        return bool(shade and shade.lower() not in ("#ffffff", "#000000")) and \
            not any(re.search(r"\d", v) for v in vals)
    shaded = cells[0].background
    if not shaded or shaded.lower() in ("#ffffff", "#000000"):
        return False
    if shaded == below[0].background:
        return False
    return not any(re.search(r"\d", v) for v in vals) and all(len(v) <= 40 for v in vals)


# "2. TIJDSCHEMA", "INRICHTING & ZAALOPSTELLING" -- several draaiboeken put
# their chapters in paragraphs and follow each with a table, instead of using
# a full-width band row inside one big table.
HEADING = re.compile(r"^(?:\d+[.)]\s*)?([A-Z][A-Z0-9 &/\u00c0-\u00de'’-]{3,48})\s*$")


def _heading_text(el: dict) -> str | None:
    para = el.get("paragraph")
    if not para:
        return None
    text = "".join(pe.get("textRun", {}).get("content", "")
                   for pe in para.get("elements", [])).strip()
    if not text or len(text) > 60:
        return None
    style = (para.get("paragraphStyle") or {}).get("namedStyleType", "")
    if style.startswith("HEADING"):
        return re.sub(r"^\d+[.)]\s*", "", text)
    m = HEADING.match(text)
    return m.group(1).strip() if m else None


def _has_nested_table(el: dict) -> bool:
    return any("table" in c
               for row in el["table"].get("tableRows", [])
               for cell in row.get("tableCells", [])
               for c in cell.get("content", []))


def _collect_tables(content: list[dict], out: list[dict],
                    heading: str | None = None) -> None:
    """Tables, including those nested inside a cell.

    Landscape pages are wide enough to stand two short chapters side by side,
    which in a Google Doc means a borderless two-column table with a real
    table in each cell. The container itself is not a chapter -- it is
    scaffolding -- so it is walked through rather than indexed.
    """
    for el in content or []:
        if "table" not in el:
            # A chapter standing in a column carries its own heading inside the
            # cell, above its table.
            found = _heading_text(el)
            if found:
                heading = found
            continue
        if not _has_nested_table(el):
            out.append({"el": el, "heading": heading})
            heading = None
        for row in el["table"].get("tableRows", []):
            for cell in row.get("tableCells", []):
                _collect_tables(cell.get("content"), out, None)


def parse_document(doc: dict[str, Any]) -> DocView:
    tables: list[Table] = []
    sections: list[Section] = []
    warnings: list[str] = []

    elements: list[dict] = []
    pending_heading: str | None = None
    for el in doc.get("body", {}).get("content", []):
        if "table" in el:
            found: list[dict] = []
            _collect_tables([el], found)
            elements.extend(found)
        else:
            elements.append({"el": el, "heading": None})

    t_idx = 0
    for entry in elements:
        el = entry["el"]
        if entry.get("heading"):
            pending_heading = entry["heading"]
        tbl = el.get("table")
        if not tbl:
            found_heading = _heading_text(el)
            if found_heading:
                pending_heading = found_heading
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
            elif not seen_data_in_section and _is_header(
                    cells, next((
                        [Cell(text=_text_of(tc.get("content")),
                              start_index=tc.get("startIndex", 0),
                              end_index=tc.get("endIndex", 0),
                              column_span=(tc.get("tableCellStyle") or {}).get("columnSpan", 1),
                              background=_rgb(tc.get("tableCellStyle")))
                         for tc in nxt.get("tableCells", [])]
                        for nxt in tbl.get("tableRows", [])[r_idx + 1:r_idx + 2]), None)):
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

        if not any(r.kind == "band" for r in rows) and pending_heading:
            sections.append(Section(name=pending_heading, slug=slugify(pending_heading),
                                    table=t_idx, band_row_id=""))
            for r in rows:
                r.section = pending_heading
            pending_heading = None

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
        cur: Section | None = by_key.get((t.index, ""))
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
