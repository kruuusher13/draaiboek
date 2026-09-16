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
# One schedule, not several.
#
# Every row is already colour-coded by department, so splitting the day into a
# Techniek table and a Catering table encodes the same thing twice and hides
# the one thing the crew needs: what happens next. The colour does the
# grouping; the clock does the ordering. A banquet event order -- the venue
# trade's own document -- works the same way: one minute-by-minute flow, with
# setup, menu and plans as reference blocks beside it.
SCHEDULE = [("TIJD", 52), ("TOT", 46), ("WAT", 300), ("WIE", 128), ("OPMERKINGEN", 247)]
SCHEDULE_EN = [("TIME", 52), ("TO", 46), ("WHAT", 300), ("WHO", 128), ("NOTES", 247)]

ROOM = [("RUIMTE", 120), ("OPSTELLING", 585), ("AANTAL", 68)]
ROOM_EN = [("AREA", 120), ("SETUP", 585), ("QTY", 68)]

# Who is working, with the hours they are booked for -- "ook altijd de crew
# benoemen, van wanneer tot wanneer zij geboekt zijn" (13 sep 2026) -- and the
# number to reach them on. One chapter, because a name without a number is no
# use on the day and a number without an arrival time is no use either.
CREW = [("NAAM", 150), ("ROL", 170), ("TELEFOON", 120), ("VAN", 55),
        ("TOT", 55), ("OPMERKINGEN", 223)]
CREW_EN = [("NAME", 150), ("ROLE", 170), ("PHONE", 120), ("FROM", 55),
           ("TO", 55), ("NOTES", 223)]

PAIRS_NL = [
    (("Catering",    [("WANNEER", 74), ("WAT", 240), ("AANTAL", 62)]),
     ("Leveringen",  [("WANNEER", 70), ("LEVERANCIER", 110), ("GOEDEREN", 188)])),
]
PAIRS_EN = [
    (("Catering & drinks", [("WHEN", 74), ("WHAT", 240), ("QTY", 62)]),
     ("Deliveries",        [("WHEN", 70), ("SUPPLIER", 110), ("GOODS", 188)])),
]
OPEN_NL = ("Open punten", [("ONDERWERP", 140), ("VRAAG", 400), ("OPMERKINGEN", 233)])
OPEN_EN = ("Open points", [("SUBJECT", 140), ("QUESTION", 400), ("NOTES", 233)])
HALF = 380
TOP = [("{{TITEL}}\n{{ONDERTITEL}}\nversie 1 · {{BIJGEWERKT}}", 300),
       ("EERSTE CREW\n{{EERSTE}}", 150),
       ("{{CONTACTEN}}", 323)]
LEGEND = "Techniek & Media      Hospitality & Catering      Inrichting & Logistiek      Algemeen"


class Builder:
    def __init__(self, svc: Draaiboek):
        self.svc = svc
        self.g = svc.google

    plan_anchor = False
    before_plan = False

    def end(self) -> int:
        if self.before_plan:
            imgs = self.image_paragraphs()
            if len(imgs) > 1:
                return imgs[1]["startIndex"]
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
            self.strip_to_assets()
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

        # Everything up to and including the room chapter is built ABOVE the
        # floor plan, so the drawing ends up inside "Zaal & opstelling" where a
        # banquet event order puts it. Set this before the first insert or the
        # header lands underneath the picture.
        self.before_plan = self.plan_anchor
        self.top_block()
        self.legend()
        self.footer()
        # Dutch for the floor, English after it for Fever and international
        # clients -- one document, so there is only ever one to update.
        self.chapter("Tijdschema", SCHEDULE)
        self.chapter("Zaal & opstelling", ROOM)
        self.before_plan = False
        if not self.plan_anchor:
            self.plan_slot("Plattegrond hier plakken")
        else:
            self.paragraph("Standaard opstelling — vervang bij een andere indeling",
                           size=8, colour={"red": .55, "green": .53, "blue": .50})
        for left, right in PAIRS_NL:
            self.pair(left, right)
        self.chapter("Bezetting & contacten", CREW)
        self.chapter(*OPEN_NL)

        self.rule()
        self.chapter("Schedule", SCHEDULE_EN)
        self.chapter("Room & setup", ROOM_EN)
        for left, right in PAIRS_EN:
            self.pair(left, right)
        self.chapter("Crew & contacts", CREW_EN)
        self.chapter(*OPEN_EN)
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

    def footer(self) -> None:
        """Printed call sheets get separated. Every page says which event it
        belongs to."""
        try:
            res = self.g.docs.documents().batchUpdate(
                documentId=self.doc,
                body={"requests": [{"createFooter": {"type": "DEFAULT"}}]}).execute()
            fid = res["replies"][0]["createFooter"]["footerId"]
        except Exception:  # noqa: BLE001 -- a template without a footer still works
            return
        text = "{{TITEL}}  ·  {{ONDERTITEL}}"
        self.g.batch_update(self.doc, [
            {"insertText": {"location": {"segmentId": fid, "index": 0}, "text": text}},
            {"updateTextStyle": {
                "range": {"segmentId": fid, "startIndex": 0, "endIndex": len(text)},
                "textStyle": {"fontSize": {"magnitude": 7.5, "unit": "PT"},
                              "weightedFontFamily": {"fontFamily": "Calibri"},
                              "foregroundColor": {"color": {"rgbColor": {
                                  "red": .55, "green": .53, "blue": .50}}}},
                "fields": "fontSize,weightedFontFamily,foregroundColor"}}])

    def image_paragraphs(self) -> list[dict]:
        return [el for el in self.g.get_document(self.doc)["body"]["content"]
                if el.get("paragraph") and any("inlineObjectElement" in e
                    for e in el["paragraph"].get("elements", []))]

    def strip_to_assets(self) -> None:
        """Keep the wordmark and the floor plan; delete everything else.

        Google can only insert an image from a publicly reachable URL, so
        embedding the venue's plan would mean publishing the drawing. Copying a
        draaiboek that already contains it avoids that entirely -- the picture
        never leaves the Drive it was already in.
        """
        imgs = self.image_paragraphs()
        if not imgs:
            return
        logo, plan = imgs[0], (imgs[1] if len(imgs) > 1 else None)
        body = self.g.get_document(self.doc)["body"]["content"]
        end = body[-1].get("endIndex", 1) - 1
        cuts = []
        if plan:
            # everything after the plan, then everything between logo and plan
            if end > plan["endIndex"]:
                cuts.append((plan["endIndex"], end))
            if plan["startIndex"] > logo["endIndex"]:
                cuts.append((logo["endIndex"], plan["startIndex"]))
        elif end > logo["endIndex"]:
            cuts.append((logo["endIndex"], end))
        for a, b in sorted(cuts, reverse=True):
            self.g.batch_update(self.doc, [{"deleteContentRange": {
                "range": {"startIndex": a, "endIndex": b}}}])
            time.sleep(0.8)
        self.plan_anchor = bool(plan)

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
            "paragraphStyle": {"spaceAbove": {"magnitude": 6, "unit": "PT"},
                               "spaceBelow": {"magnitude": 0, "unit": "PT"}},
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

        t_start = t.start_index
        style = []
        for i, (_, width) in enumerate(TOP):
            style.append({"updateTableColumnProperties": {
                "tableStartLocation": {"index": t_start}, "columnIndices": [i],
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
        label_len = len("EERSTE CREW")
        # The label is small and quiet; the time is the size of a headline.
        style.append({"updateTextStyle": {
            "range": {"startIndex": mid.start_index + 1,
                      "endIndex": mid.start_index + 1 + label_len},
            "textStyle": {"foregroundColor": {"color": {"rgbColor": {
                              "red": .78, "green": .82, "blue": .87}}},
                          "bold": False, "fontSize": {"magnitude": 8, "unit": "PT"},
                          "weightedFontFamily": {"fontFamily": "Calibri"}},
            "fields": "foregroundColor,bold,fontSize,weightedFontFamily"}})
        style.append({"updateTextStyle": {
            "range": {"startIndex": mid.start_index + 1 + label_len + 1,
                      "endIndex": mid.end_index - 1},
            "textStyle": {"foregroundColor": {"color": {"rgbColor": WHITE}},
                          "bold": True, "fontSize": {"magnitude": 30, "unit": "PT"},
                          "weightedFontFamily": {"fontFamily": "Calibri"}},
            "fields": "foregroundColor,bold,fontSize,weightedFontFamily"}})
        style.append({"updateTableCellStyle": {
            "tableRange": {"tableCellLocation": {
                "tableStartLocation": {"index": t.start_index},
                "rowIndex": 0, "columnIndex": 1}, "rowSpan": 1, "columnSpan": 1},
            "tableCellStyle": {"paddingTop": {"magnitude": 8, "unit": "PT"},
                               "paddingBottom": {"magnitude": 8, "unit": "PT"},
                               "paddingLeft": {"magnitude": 12, "unit": "PT"},
                               "contentAlignment": "MIDDLE"},
            "fields": "paddingTop,paddingBottom,paddingLeft,contentAlignment"}})
        first = t.rows[0].cells[0]
        title_start = first.start_index + 1
        style.append({"updateTextStyle": {
            "range": {"startIndex": title_start,
                      "endIndex": title_start + len("{{TITEL}}")},
            "textStyle": {"bold": True, "fontSize": {"magnitude": 19, "unit": "PT"},
                          "weightedFontFamily": {"fontFamily": "Calibri"},
                          "foregroundColor": {"color": {"rgbColor": NAVY}}},
            "fields": "bold,fontSize,weightedFontFamily,foregroundColor"}})
        style.append({"updateTextStyle": {
            "range": {"startIndex": title_start + len("{{TITEL}}") + 1,
                      "endIndex": first.end_index - 1},
            "textStyle": {"fontSize": {"magnitude": 9, "unit": "PT"},
                          "weightedFontFamily": {"fontFamily": "Calibri"},
                          "foregroundColor": {"color": {"rgbColor": {
                              "red": .42, "green": .40, "blue": .38}}}},
            "fields": "fontSize,weightedFontFamily,foregroundColor"}})
        for idx in (0, 2):
            style.append({"updateTableCellStyle": {
                "tableRange": {"tableCellLocation": {
                    "tableStartLocation": {"index": t.start_index},
                    "rowIndex": 0, "columnIndex": idx}, "rowSpan": 1, "columnSpan": 1},
                "tableCellStyle": {"paddingTop": {"magnitude": 9, "unit": "PT"},
                                   "paddingBottom": {"magnitude": 9, "unit": "PT"},
                                   "paddingLeft": {"magnitude": 11, "unit": "PT"},
                                   "contentAlignment": "MIDDLE"},
                "fields": "paddingTop,paddingBottom,paddingLeft,contentAlignment"}})
        contacts = t.rows[0].cells[2]
        style.append({"updateTextStyle": {
            "range": {"startIndex": contacts.start_index + 1, "endIndex": contacts.end_index - 1},
            "textStyle": {"fontSize": {"magnitude": 9.5, "unit": "PT"},
                          "weightedFontFamily": {"fontFamily": "Calibri"}},
            "fields": "fontSize,weightedFontFamily"}})
        self.g.batch_update(self.doc, style)

    def rule(self) -> None:
        """Where the Dutch half ends and the English half begins."""
        at = self.end()
        self.g.batch_update(self.doc, [{"insertPageBreak": {"location": {"index": at}}}])
        time.sleep(0.6)

    def plan_slot(self, caption: str) -> None:
        """A place for the floor plan. The venue trade includes a diagram as a
        matter of course, and Larissa has asked twice that the zaalopstelling
        image stays in."""
        self.paragraph("{{PLATTEGROND}}", size=9, colour={"red": .42, "green": .40,
                                                          "blue": .38}, space_before=6)
        self.paragraph(caption, size=8, colour={"red": .55, "green": .53, "blue": .50})

    def raw_tables(self) -> list[dict]:
        return [el for el in self.g.get_document(self.doc)["body"]["content"]
                if "table" in el]

    def pair(self, left, right) -> None:
        """Two chapters side by side, in a borderless two-column container."""
        at = self.end()
        self.g.batch_update(self.doc, [{"insertTable": {
            "rows": 1, "columns": 2, "location": {"index": at}}}])
        time.sleep(1.2)
        container = self.raw_tables()[-1]
        c_start = container["startIndex"]

        invisible = {"color": {"color": {"rgbColor": {"red": 1, "green": 1, "blue": 1}}},
                     "width": {"magnitude": 0, "unit": "PT"}, "dashStyle": "SOLID"}
        self.g.batch_update(self.doc, [
            {"updateTableCellStyle": {
                "tableStartLocation": {"index": c_start},
                "tableCellStyle": {"borderTop": invisible, "borderBottom": invisible,
                                   "borderLeft": invisible, "borderRight": invisible,
                                   "paddingLeft": {"magnitude": 0, "unit": "PT"},
                                   "paddingRight": {"magnitude": 12, "unit": "PT"},
                                   "paddingTop": {"magnitude": 0, "unit": "PT"},
                                   "paddingBottom": {"magnitude": 0, "unit": "PT"}},
                "fields": ("borderTop,borderBottom,borderLeft,borderRight,"
                           "paddingLeft,paddingRight,paddingTop,paddingBottom")}}]
            + [{"updateTableColumnProperties": {
                "tableStartLocation": {"index": c_start}, "columnIndices": [i],
                "tableColumnProperties": {"widthType": "FIXED_WIDTH",
                                          "width": {"magnitude": HALF, "unit": "PT"}},
                "fields": "widthType,width"}} for i in (0, 1)])

        # right first: filling the left cell shifts everything after it
        for col, (heading, cols) in ((1, right), (0, left)):
            self.in_cell(col, heading, cols)

    def cell_start(self, col: int) -> int:
        container = self.raw_tables()[-1]
        return container["table"]["tableRows"][0]["tableCells"][col]["startIndex"]

    def in_cell(self, col: int, heading: str, cols: list) -> None:
        at = self.cell_start(col) + 1
        self.g.batch_update(self.doc, [{"insertText": {
            "location": {"index": at}, "text": heading + "\n"}}])
        self.g.batch_update(self.doc, [
            {"updateTextStyle": {
                "range": {"startIndex": at, "endIndex": at + len(heading)},
                "textStyle": {"bold": True, "fontSize": {"magnitude": 12, "unit": "PT"},
                              "weightedFontFamily": {"fontFamily": "Calibri"},
                              "foregroundColor": {"color": {"rgbColor": NAVY}}},
                "fields": "bold,fontSize,weightedFontFamily,foregroundColor"}},
            {"updateParagraphStyle": {
                "range": {"startIndex": at, "endIndex": at + len(heading)},
                "paragraphStyle": {
                    "namedStyleType": "HEADING_2",
                    "spaceAbove": {"magnitude": 9, "unit": "PT"},
                    "spaceBelow": {"magnitude": 3, "unit": "PT"},
                    "borderBottom": {"color": {"color": {"rgbColor": NAVY}},
                                     "width": {"magnitude": 1.25, "unit": "PT"},
                                     "padding": {"magnitude": 2, "unit": "PT"},
                                     "dashStyle": "SOLID"}},
                "fields": "namedStyleType,spaceAbove,spaceBelow,borderBottom"}}])
        time.sleep(0.8)
        at = self.cell_start(col) + 1 + len(heading) + 1
        self.g.batch_update(self.doc, [{"insertTable": {
            "rows": 1, "columns": len(cols), "location": {"index": at}}}])
        time.sleep(1.2)
        self.style_table(lambda: self.nested_table(col), cols)

    def nested_table(self, col: int):
        from draaiboek.reader import parse_document
        raw = self.g.get_document(self.doc)
        container = [el for el in raw["body"]["content"] if "table" in el][-1]
        cell = container["table"]["tableRows"][0]["tableCells"][col]
        inner = [c for c in cell.get("content", []) if "table" in c][-1]
        return parse_document({"documentId": "", "title": "", "revisionId": "",
                               "body": {"content": [inner]}}).tables[0], inner["startIndex"]

    def chapter(self, heading: str, cols: list) -> None:
        self.paragraph(heading, size=13, bold=True, colour=NAVY,
                       space_before=12, heading=True)
        self._table(1, len(cols))
        self.style_table(self.last_top_table, cols)

    def last_top_table(self):
        """Re-locate the table just built, after text has shifted every index.

        While the first half is being built above the floor plan, the newest
        table is not the last one in the document -- the plan sits below it.
        """
        from draaiboek.reader import parse_document
        v = parse_document(self.g.get_document(self.doc))
        if self.before_plan:
            imgs = self.image_paragraphs()
            if len(imgs) > 1:
                cut = imgs[1]["startIndex"]
                above = [t for t in v.tables if t.start_index < cut]
                if above:
                    t = above[-1]
                    return t, t.start_index
        t = v.tables[-1]
        return t, t.start_index

    def style_table(self, locate, cols: list) -> None:
        t, t_start = locate()
        head = t.rows[0]
        self.g.batch_update(self.doc, [
            {"insertText": {"location": {"index": head.cells[i].start_index + 1},
                            "text": label}}
            for i, (label, _) in reversed(list(enumerate(cols)))])
        time.sleep(1.0)
        t, t_start = locate()
        head = t.rows[0]
        style = [{"updateTableCellStyle": {
            "tableRange": {"tableCellLocation": {
                "tableStartLocation": {"index": t_start},
                "rowIndex": 0, "columnIndex": 0}, "rowSpan": 1, "columnSpan": len(cols)},
            "tableCellStyle": {"backgroundColor": {"color": {"rgbColor": NAVY}},
                               "paddingTop": {"magnitude": 3, "unit": "PT"},
                               "paddingBottom": {"magnitude": 3, "unit": "PT"}},
            "fields": "backgroundColor,paddingTop,paddingBottom"}}]
        for i, (label, width) in enumerate(cols):
            style.append({"updateTableColumnProperties": {
                "tableStartLocation": {"index": t_start}, "columnIndices": [i],
                "tableColumnProperties": {"widthType": "FIXED_WIDTH",
                                          "width": {"magnitude": width, "unit": "PT"}},
                "fields": "widthType,width"}})
            c = head.cells[i]
            style.append({"updateTextStyle": {
                "range": {"startIndex": c.start_index + 1, "endIndex": c.end_index - 1},
                "textStyle": {"foregroundColor": {"color": {"rgbColor": WHITE}},
                              "bold": True, "fontSize": {"magnitude": 7.5, "unit": "PT"},
                              "weightedFontFamily": {"fontFamily": "Calibri"}},
                "fields": "foregroundColor,bold,fontSize,weightedFontFamily"}})
        border = {"color": {"color": {"rgbColor": LINE}},
                  "width": {"magnitude": 0.75, "unit": "PT"}, "dashStyle": "SOLID"}
        style.append({"updateTableCellStyle": {
            "tableStartLocation": {"index": t_start},
            "tableCellStyle": {"borderTop": border, "borderBottom": border,
                               "borderLeft": border, "borderRight": border,
                               "paddingLeft": {"magnitude": 6, "unit": "PT"},
                               "paddingRight": {"magnitude": 6, "unit": "PT"},
                               "paddingTop": {"magnitude": 2.5, "unit": "PT"},
                               "paddingBottom": {"magnitude": 2.5, "unit": "PT"}},
            "fields": ("borderTop,borderBottom,borderLeft,borderRight,"
                       "paddingLeft,paddingRight,paddingTop,paddingBottom")}})
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
