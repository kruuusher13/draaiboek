"""A local document backend.

Implements the same interface as `Google`, and produces the same document JSON
shape, so reader, guard, writer, ledger and revision locking all run unchanged.
Working locally is not a mock or a simplified path -- it is the identical code
with the network swapped out, which is exactly what makes it safe to trust
what you see here.

Documents live in ~/.draaiboek/localdocs/<id>.json
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

CELL_OVERHEAD = 2  # cell marker + trailing paragraph newline


class LocalDocs:
    def __init__(self, home: Path):
        self.dir = home / "localdocs"
        self.dir.mkdir(parents=True, exist_ok=True)

    # -- storage -----------------------------------------------------------
    def _path(self, doc_id: str) -> Path:
        return self.dir / f"{doc_id.removeprefix('local:')}.json"

    def _load(self, doc_id: str) -> dict:
        p = self._path(doc_id)
        if not p.exists():
            raise FileNotFoundError(
                f"No local document {doc_id!r}. Create one with:  draaiboek new-local <name>"
            )
        return json.loads(p.read_text())

    def _save(self, doc: dict) -> None:
        doc["revision"] = doc.get("revision", 0) + 1
        self._path(doc["id"]).write_text(json.dumps(doc, ensure_ascii=False, indent=1))

    def list_docs(self) -> list[dict]:
        out = []
        for p in sorted(self.dir.glob("*.json")):
            try:
                d = json.loads(p.read_text())
                out.append({"id": d["id"], "title": d.get("title", ""),
                            "revision": d.get("revision", 0),
                            "rows": sum(len(t["rows"]) for t in d["tables"])})
            except (json.JSONDecodeError, KeyError):
                continue
        return out

    def create(self, doc_id: str, title: str, tables: list[dict]) -> dict:
        doc = {"id": doc_id, "title": title, "revision": 0, "tables": tables}
        self._path(doc_id).write_text(json.dumps(doc, ensure_ascii=False, indent=1))
        return doc

    # -- Google-shaped rendering ------------------------------------------
    @staticmethod
    def _render(doc: dict) -> dict[str, Any]:
        content: list[dict] = [{"startIndex": 0, "endIndex": 1, "sectionBreak": {}}]
        idx = 1
        for tbl in doc["tables"]:
            rows = tbl["rows"]
            columns = tbl.get("columns") or max((len(r) for r in rows), default=0)
            spans = {int(k): v for k, v in (tbl.get("spans") or {}).items()}
            bgs = {int(k): v for k, v in (tbl.get("bg") or {}).items()}
            t_start = idx
            i = idx + 1
            trs = []
            for r_i, row in enumerate(rows):
                r_start = i
                i += 1
                cells = []
                for c_i, text in enumerate(row):
                    c_start = i
                    i += CELL_OVERHEAD + len(text)
                    style: dict[str, Any] = {
                        "columnSpan": spans.get(r_i, 1) if c_i == 0 else 1
                    }
                    if r_i in bgs:
                        r, g, b = bgs[r_i]
                        style["backgroundColor"] = {
                            "color": {"rgbColor": {"red": r, "green": g, "blue": b}}
                        }
                    cells.append({
                        "startIndex": c_start, "endIndex": i,
                        "tableCellStyle": style,
                        "content": [{"paragraph": {"elements": (
                            [{"textRun": {"content": text + "\n"}}] if text else []
                        )}}],
                    })
                trs.append({"startIndex": r_start, "endIndex": i, "tableCells": cells})
            content.append({"startIndex": t_start, "endIndex": i,
                            "table": {"rows": len(rows), "columns": columns,
                                      "tableRows": trs}})
            idx = i + 1
        return {"documentId": doc["id"], "title": doc.get("title", ""),
                "revisionId": f"local-{doc.get('revision', 0)}",
                "body": {"content": content}}

    # -- Google interface --------------------------------------------------
    def get_document(self, doc_id: str) -> dict[str, Any]:
        return self._render(self._load(doc_id))

    def get_revision(self, doc_id: str) -> str:
        return f"local-{self._load(doc_id).get('revision', 0)}"

    def _maps(self, doc: dict):
        rendered = self._render(doc)
        cells, tstarts = {}, {}
        ti = 0
        for el in rendered["body"]["content"]:
            if "table" not in el:
                continue
            tstarts[el["startIndex"]] = ti
            for ri, tr in enumerate(el["table"]["tableRows"]):
                for ci, tc in enumerate(tr["tableCells"]):
                    cells[tc["startIndex"]] = (ti, ri, ci)
            ti += 1
        return cells, tstarts

    def batch_update(self, doc_id: str, requests: list[dict]) -> dict:
        if not requests:
            return {}
        doc = self._load(doc_id)
        for req in requests:
            cells, tstarts = self._maps(doc)
            (kind, body), = req.items()
            if kind == "insertTableRow":
                loc = body["tableCellLocation"]
                t = tstarts[loc["tableStartLocation"]["index"]]
                tbl = doc["tables"][t]
                width = tbl.get("columns") or len(tbl["rows"][0])
                at = loc["rowIndex"] + (1 if body.get("insertBelow") else 0)
                tbl["rows"].insert(at, [""] * width)
                self._shift(tbl, at, +1)
            elif kind == "deleteTableRow":
                loc = body["tableCellLocation"]
                t = tstarts[loc["tableStartLocation"]["index"]]
                tbl = doc["tables"][t]
                tbl["rows"].pop(loc["rowIndex"])
                self._shift(tbl, loc["rowIndex"], -1)
            elif kind == "deleteContentRange":
                t, r, c = cells[body["range"]["startIndex"] - 1]
                doc["tables"][t]["rows"][r][c] = ""
            elif kind == "insertText":
                t, r, c = cells[body["location"]["index"] - 1]
                doc["tables"][t]["rows"][r][c] = (
                    body["text"] + doc["tables"][t]["rows"][r][c]
                )
            elif kind == "updateTableCellStyle":
                tr = body["tableRange"]["tableCellLocation"]
                t = tstarts[tr["tableStartLocation"]["index"]]
                rgb = ((body["tableCellStyle"].get("backgroundColor") or {})
                       .get("color", {}).get("rgbColor", {}))
                doc["tables"][t].setdefault("bg", {})[str(tr["rowIndex"])] = [
                    rgb.get("red", 1.0), rgb.get("green", 1.0), rgb.get("blue", 1.0)
                ]
            elif kind == "replaceAllText":
                find = body["containsText"]["text"]
                rep = body["replaceText"]
                for tbl in doc["tables"]:
                    for row in tbl["rows"]:
                        for ci, v in enumerate(row):
                            row[ci] = v.replace(find, rep)
            else:
                raise ValueError(f"Unsupported request for local documents: {kind}")
        self._save(doc)
        return {}

    @staticmethod
    def _shift(tbl: dict, at: int, delta: int) -> None:
        for key in ("spans", "bg"):
            if key in tbl:
                tbl[key] = {
                    str(int(k) + delta if int(k) >= at else int(k)): v
                    for k, v in tbl[key].items()
                    if not (delta < 0 and int(k) == at)
                }

    def wait_for_rows(self, doc_id: str, table: int, expected: int, **_) -> dict[str, Any]:
        doc = self._load(doc_id)
        got = len(doc["tables"][table]["rows"])
        if got != expected:
            raise RuntimeError(f"table {table}: {got} rows, expected {expected}")
        return self._render(doc)

    def copy_document(self, template_id: str, name: str, folder_id=None) -> str:
        src = self._load(template_id)
        new_id = name.lower().replace(" ", "-").replace("/", "-")[:60]
        self.create(new_id, name, json.loads(json.dumps(src["tables"])))
        return new_id

    def share(self, *_a, **_k) -> None:
        pass


def backend_for(doc_id: str, home: Path, google):
    """Local documents are addressed as `local:<name>`; everything else is Google."""
    return LocalDocs(home) if str(doc_id).startswith("local:") else google


def is_local(doc_id: str) -> bool:
    return str(doc_id).startswith("local:")
