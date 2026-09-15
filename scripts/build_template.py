# -*- coding: utf-8 -*-
"""Draw the draaiboek template once, in code.

Everywhere else this system refuses to draw formatting -- a document's look is
inherited from a document a human made. That works only if such a document
exists. This layout is new, so it is built here once; from then on every
draaiboek is a copy of the result and nothing draws formatting again.

Run:  .venv/bin/python scripts/build_template.py
"""
from __future__ import annotations

import sys
import time

from draaiboek.service import Draaiboek

NAVY = {"red": 45 / 255, "green": 60 / 255, "blue": 79 / 255}
GREY = {"red": 239 / 255, "green": 240 / 255, "blue": 241 / 255}
INK = {"red": 0.098, "green": 0.114, "blue": 0.133}
WHITE = {"red": 1, "green": 1, "blue": 1}
LINE = {"red": .86, "green": .84, "blue": .81}

# chapter: (heading, [(column label, width in pt)], blank data rows)
CHAPTERS = [
    ("Tijdschema", [("TIJD", 58), ("TOT", 50), ("WAT", 300), ("WIE", 120), ("OPMERKINGEN", 220)], 0),
    ("Techniek",   [("TIJD", 58), ("WAT", 420), ("WIE", 270)], 0),
    ("Inrichting", [("RUIMTE", 120), ("WAT", 560), ("AANTAL", 68)], 0),
    ("Catering",   [("SESSIE", 78), ("KAARTEN", 70), ("WAT", 530), ("AANTAL", 70)], 0),
    ("Leveringen", [("WANNEER", 100), ("LEVERANCIER", 160), ("GOEDEREN", 488)], 0),
    ("Call sheet", [("NAAM", 180), ("ROL", 220), ("TELEFOON", 140), ("AANWEZIG", 208)], 0),
    ("Open punten", [("ONDERWERP", 150), ("VRAAG", 400), ("OPMERKINGEN", 198)], 0),
]
TOP = [("{{TITEL}}\n{{ONDERTITEL}}\nversie 1 · {{BIJGEWERKT}}", 300),
       ("EERSTE CREW\n{{EERSTE}}", 150),
       ("{{CONTACTEN}}", 298)]
LEGEND = "Techniek & Media      Hospitality & Catering      Inrichting & Logistiek      Algemeen"


class Builder:
    def __init__(self, svc: Draaiboek):
        self.svc = svc
        self.g = svc.google

    def end(self) -> int:
        body = self.g.get_document(self.doc)["body"]["content"]
        return body[-1].get("endIndex", 1) - 1

    def run(self, name: str, logo_from: str | None = None) -> str:
        folder = self.svc.cfg.drive_folder_id
        if logo_from:
            # Copy a real draaiboek and keep only its first paragraph -- the
            # Leeuwenbergh wordmark. Inserting the image instead would mean
            # publishing it to a public URL, which the Docs API requires and
            # which nobody should have to do to put a logo on a document.
            body = {"name": name}
            if folder:
                body["parents"] = [folder]
            self.doc = self.g.drive.files().copy(
                fileId=logo_from, body=body, supportsAllDrives=True,
                fields="id").execute()["id"]
            self.strip_to_logo()
        else:
            body = {"name": name, "mimeType": "application/vnd.google-apps.document"}
            if folder:
                body["parents"] = [folder]
            self.doc = self.g.drive.files().create(
                body=body, supportsAllDrives=True, fields="id").execute()["id"]
        # A4 landscape, as she asked on 26 Aug: more fits side by side.
        self.g.batch_update(self.doc, [{"updateDocumentStyle": {
            "documentStyle": {
                "pageSize": {"width": {"magnitude": 841.89, "unit": "PT"},
                             "height": {"magnitude": 595.28, "unit": "PT"}},
                "marginTop": {"magnitude": 34, "unit": "PT"},
                "marginBottom": {"magnitude": 34, "unit": "PT"},
                "marginLeft": {"magnitude": 34, "unit": "PT"},
                "marginRight": {"magnitude": 34, "unit": "PT"}},
            "fields": "pageSize,marginTop,marginBottom,marginLeft,marginRight"}}])

        self.top_block()
        self.legend()
        for heading, cols, blanks in CHAPTERS:
            self.chapter(heading, cols, blanks)
        return self.doc

    # -- building blocks --------------------------------------------------
    def paragraph(self, text: str, *, size=10, bold=False, colour=None,
                  space_before=0, heading=False) -> None:
        at = self.end()
        self.g.batch_update(self.doc, [{"insertText": {"location": {"index": at},
                                                       "text": text + "\n"}}])
        style = {"fontSize": {"magnitude": size, "unit": "PT"}, "bold": bold,
                 "weightedFontFamily": {"fontFamily": "Calibri"}}
        if colour:
            style["foregroundColor"] = {"color": {"rgbColor": colour}}
        reqs = [{"updateTextStyle": {
            "range": {"startIndex": at, "endIndex": at + len(text)},
            "textStyle": style,
            "fields": "fontSize,bold,weightedFontFamily" + (",foregroundColor" if colour else "")}}]
        para = {"spaceAbove": {"magnitude": space_before, "unit": "PT"},
                "spaceBelow": {"magnitude": 3, "unit": "PT"}}
        fields = "spaceAbove,spaceBelow"
        if heading:
            para["namedStyleType"] = "HEADING_2"
            para["borderBottom"] = {"color": {"color": {"rgbColor": NAVY}},
                                    "width": {"magnitude": 1.5, "unit": "PT"},
                                    "padding": {"magnitude": 2, "unit": "PT"},
                                    "dashStyle": "SOLID"}
            fields += ",namedStyleType,borderBottom"
        reqs.append({"updateParagraphStyle": {
            "range": {"startIndex": at, "endIndex": at + len(text)},
            "paragraphStyle": para, "fields": fields}})
        self.g.batch_update(self.doc, reqs)

    def _table(self, rows: int, cols: int):
        at = self.end()
        self.g.batch_update(self.doc, [{"insertTable": {
            "rows": rows, "columns": cols, "location": {"index": at}}}])
        time.sleep(1.2)
        from draaiboek.reader import parse_document
        return parse_document(self.g.get_document(self.doc)).tables[-1]

    def strip_to_logo(self) -> None:
        """Keep the wordmark paragraph, delete everything after it."""
        body = self.g.get_document(self.doc)["body"]["content"]
        logo_end = None
        for el in body:
            para = el.get("paragraph")
            if para and any("inlineObjectElement" in e for e in para.get("elements", [])):
                logo_end = el.get("endIndex")
                break
        start = logo_end if logo_end else 1
        end = body[-1].get("endIndex", 1) - 1
        if end > start:
            self.g.batch_update(self.doc, [{"deleteContentRange": {
                "range": {"startIndex": start, "endIndex": end}}}])
            time.sleep(1.0)

    def legend(self) -> None:
        """The four category colours, in their own colours."""
        parts = [("Techniek & Media", {"red": .70, "green": .16, "blue": .16}),
                 ("Hospitality & Catering", {"red": .21, "green": .46, "blue": .25}),
                 ("Inrichting & Logistiek", {"red": .16, "green": .35, "blue": .60}),
                 ("Algemeen", INK)]
        text = "      ".join(p[0] for p in parts)
        at = self.end()
        self.g.batch_update(self.doc, [{"insertText": {
            "location": {"index": at}, "text": text + "\n"}}])
        reqs, cursor = [], at
        for label, colour in parts:
            reqs.append({"updateTextStyle": {
                "range": {"startIndex": cursor, "endIndex": cursor + len(label)},
                "textStyle": {"foregroundColor": {"color": {"rgbColor": colour}},
                              "bold": True, "fontSize": {"magnitude": 8.5, "unit": "PT"},
                              "weightedFontFamily": {"fontFamily": "Calibri"}},
                "fields": "foregroundColor,bold,fontSize,weightedFontFamily"}})
            cursor += len(label) + 6
        reqs.append({"updateParagraphStyle": {
            "range": {"startIndex": at, "endIndex": at + len(text)},
            "paragraphStyle": {"spaceAbove": {"magnitude": 8, "unit": "PT"},
                               "spaceBelow": {"magnitude": 2, "unit": "PT"}},
            "fields": "spaceAbove,spaceBelow"}})
        self.g.batch_update(self.doc, reqs)

    def top_block(self) -> None:
        t = self._table(1, 3)
        reqs = []
        for i, (text, width) in reversed(list(enumerate(TOP))):
            cell = t.rows[0].cells[i]
            reqs.append({"insertText": {"location": {"index": cell.start_index + 1},
                                        "text": text}})
        self.g.batch_update(self.doc, reqs)
        time.sleep(1.0)
        from draaiboek.reader import parse_document
        t = parse_document(self.g.get_document(self.doc)).tables[-1]

        style = []
        for i, (_, width) in enumerate(TOP):
            style.append({"updateTableColumnProperties": {
                "tableStartLocation": {"index": t.start_index}, "columnIndices": [i],
                "tableColumnProperties": {"widthType": "FIXED_WIDTH",
                                          "width": {"magnitude": width, "unit": "PT"}},
                "fields": "widthType,width"}})
        # the middle panel is the one number anyone reads first
        mid = t.rows[0].cells[1]
        style.append({"updateTableCellStyle": {
            "tableRange": {"tableCellLocation": {
                "tableStartLocation": {"index": t.start_index},
                "rowIndex": 0, "columnIndex": 1}, "rowSpan": 1, "columnSpan": 1},
            "tableCellStyle": {"backgroundColor": {"color": {"rgbColor": NAVY}}},
            "fields": "backgroundColor"}})
        style.append({"updateTextStyle": {
            "range": {"startIndex": mid.start_index + 1, "endIndex": mid.end_index - 1},
            "textStyle": {"foregroundColor": {"color": {"rgbColor": WHITE}},
                          "bold": True, "fontSize": {"magnitude": 11, "unit": "PT"}},
            "fields": "foregroundColor,bold,fontSize"}})
        first = t.rows[0].cells[0]
        style.append({"updateTextStyle": {
            "range": {"startIndex": first.start_index + 1,
                      "endIndex": first.start_index + 1 + len("{{TITEL}}")},
            "textStyle": {"bold": True, "fontSize": {"magnitude": 16, "unit": "PT"}},
            "fields": "bold,fontSize"}})
        self.g.batch_update(self.doc, style)

    def chapter(self, heading: str, cols: list, blanks: int) -> None:
        self.paragraph(heading, size=11, bold=True, colour=NAVY,
                       space_before=14, heading=True)
        t = self._table(1, len(cols))
        head = t.rows[0]
        reqs = [{"insertText": {"location": {"index": head.cells[i].start_index + 1},
                                "text": label}}
                for i, (label, _) in reversed(list(enumerate(cols)))]
        self.g.batch_update(self.doc, reqs)
        time.sleep(1.0)
        from draaiboek.reader import parse_document
        t = parse_document(self.g.get_document(self.doc)).tables[-1]
        head = t.rows[0]

        style = [{"updateTableCellStyle": {
            "tableRange": {"tableCellLocation": {
                "tableStartLocation": {"index": t.start_index},
                "rowIndex": 0, "columnIndex": 0}, "rowSpan": 1, "columnSpan": len(cols)},
            "tableCellStyle": {"backgroundColor": {"color": {"rgbColor": NAVY}},
                               "paddingTop": {"magnitude": 3, "unit": "PT"},
                               "paddingBottom": {"magnitude": 3, "unit": "PT"}},
            "fields": "backgroundColor,paddingTop,paddingBottom"}}]
        for i, (label, width) in enumerate(cols):
            style.append({"updateTableColumnProperties": {
                "tableStartLocation": {"index": t.start_index}, "columnIndices": [i],
                "tableColumnProperties": {"widthType": "FIXED_WIDTH",
                                          "width": {"magnitude": width, "unit": "PT"}},
                "fields": "widthType,width"}})
            c = head.cells[i]
            style.append({"updateTextStyle": {
                "range": {"startIndex": c.start_index + 1, "endIndex": c.end_index - 1},
                "textStyle": {"foregroundColor": {"color": {"rgbColor": WHITE}},
                              "bold": True, "fontSize": {"magnitude": 8.5, "unit": "PT"},
                              "weightedFontFamily": {"fontFamily": "Calibri"}},
                "fields": "foregroundColor,bold,fontSize,weightedFontFamily"}})
        border = {"color": {"color": {"rgbColor": LINE}},
                  "width": {"magnitude": 0.75, "unit": "PT"}, "dashStyle": "SOLID"}
        style.append({"updateTableCellStyle": {
            "tableStartLocation": {"index": t.start_index},
            "tableCellStyle": {"borderTop": border, "borderBottom": border,
                               "borderLeft": border, "borderRight": border,
                               "paddingLeft": {"magnitude": 5, "unit": "PT"},
                               "paddingRight": {"magnitude": 5, "unit": "PT"}},
            "fields": ("borderTop,borderBottom,borderLeft,borderRight,"
                       "paddingLeft,paddingRight")}})
        self.g.batch_update(self.doc, style)


if __name__ == "__main__":
    svc = Draaiboek()
    name = sys.argv[1] if len(sys.argv) > 1 else "Draaiboek — TEMPLATE"
    logo_from = sys.argv[2] if len(sys.argv) > 2 else None
    doc_id = Builder(svc).run(name, logo_from)
    view, _ = svc.read(doc_id, record=False)
    print("template:", doc_id)
    print("url:", f"https://docs.google.com/document/d/{doc_id}/edit")
    print("chapters:", ", ".join(view.section_names()))
    print("tables:", len(view.tables), "| blank rows:", len(view.data_rows()))
