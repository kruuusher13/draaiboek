"""An in-memory Google Docs that honours the real index semantics.

Requests are applied one at a time against freshly computed indices, exactly
as the API does, so a batch that is ordered wrongly breaks here the same way
it would break on a live document.
"""

from __future__ import annotations

from fixtures import make_doc


class FakeGoogle:
    def __init__(self, tables: list[list[list[str]]], spans: list[dict] | None = None):
        self.tables = [[list(r) for r in t] for t in tables]
        self.spans = spans or [{} for _ in tables]
        self.styles: dict[tuple[int, int], dict] = {}
        self.rev = 0
        self.batches: list[list[dict]] = []

    # -- rendering ---------------------------------------------------------
    def _render(self) -> dict:
        return make_doc(self.tables, revision=f"rev-{self.rev}", spans=self.spans)

    def get_document(self, doc_id: str) -> dict:
        d = self._render()
        d["documentId"] = doc_id
        return d

    def get_revision(self, doc_id: str) -> str:
        return f"rev-{self.rev}"

    def _maps(self):
        """cell start index -> (table, row, col); table start index -> table."""
        doc = self._render()
        cells, tstarts = {}, {}
        ti = 0
        for el in doc["body"]["content"]:
            if "table" not in el:
                continue
            tstarts[el["startIndex"]] = ti
            for ri, tr in enumerate(el["table"]["tableRows"]):
                for ci, tc in enumerate(tr["tableCells"]):
                    cells[tc["startIndex"]] = (ti, ri, ci)
            ti += 1
        return cells, tstarts

    # -- writing -----------------------------------------------------------
    def batch_update(self, doc_id: str, requests: list[dict]) -> dict:
        if not requests:
            return {}
        self.batches.append(requests)
        for req in requests:
            cells, tstarts = self._maps()
            (kind, body), = req.items()

            if kind == "insertTableRow":
                loc = body["tableCellLocation"]
                t = tstarts[loc["tableStartLocation"]["index"]]
                width = len(self.tables[t][0])
                at = loc["rowIndex"] + (1 if body.get("insertBelow") else 0)
                self.tables[t].insert(at, [""] * width)
                self._shift_spans(t, at)

            elif kind == "deleteTableRow":
                loc = body["tableCellLocation"]
                t = tstarts[loc["tableStartLocation"]["index"]]
                self.tables[t].pop(loc["rowIndex"])

            elif kind == "deleteContentRange":
                s = body["range"]["startIndex"]
                t, r, c = cells[s - 1]
                self.tables[t][r][c] = ""

            elif kind == "insertText":
                i = body["location"]["index"]
                t, r, c = cells[i - 1]
                self.tables[t][r][c] = body["text"] + self.tables[t][r][c]

            elif kind == "updateTableCellStyle":
                tr = body["tableRange"]["tableCellLocation"]
                t = tstarts[tr["tableStartLocation"]["index"]]
                self.styles[(t, tr["rowIndex"])] = body["tableCellStyle"]

            elif kind == "updateTextStyle":
                pass  # character styling is not modelled here
            elif kind == "replaceAllText":
                find = body["containsText"]["text"]
                rep = body["replaceText"]
                for tbl in self.tables:
                    for row in tbl:
                        for ci, v in enumerate(row):
                            row[ci] = v.replace(find, rep)
            else:
                raise AssertionError(f"unsupported request: {kind}")
            self.rev += 1
        return {}

    def _shift_spans(self, t: int, at: int) -> None:
        self.spans[t] = {(k + 1 if k >= at else k): v for k, v in self.spans[t].items()}

    def hand_edit(self, t: int, *, delete: int | None = None,
                  set_cell: tuple[int, int, str] | None = None) -> None:
        """Larissa, editing in the browser. Keeps the band map consistent."""
        if delete is not None:
            self.tables[t].pop(delete)
            self.spans[t] = {(k - 1 if k > delete else k): v
                             for k, v in self.spans[t].items() if k != delete}
        if set_cell is not None:
            r, c, v = set_cell
            self.tables[t][r][c] = v
        self.rev += 1

    # -- used by service.apply --------------------------------------------
    def wait_for_rows(self, doc_id: str, table: int, expected: int, **_) -> dict:
        got = len(self.tables[table])
        assert got == expected, f"table {table}: {got} rows, expected {expected}"
        return self.get_document(doc_id)

    def copy_document(self, template_id: str, name: str, folder_id) -> str:
        return "COPY1"
