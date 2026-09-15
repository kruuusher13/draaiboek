"""Hermes proposes, Larissa decides -- in the workspace.

Larissa talks to Hermes on Telegram. Hermes gathers the sources and works out
the edits, each with a reason, but it does not write them. It proposes them:
the batch is checked exactly as a write would be (revision, house rules,
quotes) and parked here. Hermes posts the workspace link; Larissa opens the
event, reads the sources, answers the open questions, adjusts the draaiboek
and deploys. Only her deploy writes.

"Zolang jij niet zegt dat het goed is, raakt het systeem geen enkel draaiboek
van je aan." -- the promise of 15 Sep 2026, as a mechanism.

Proposals are files so the MCP server (which creates them) and the workspace
(which decides them) can be separate processes.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .model import DocView
from .ops import AddRow, EditRequest, RemoveRow, ReplaceText, UpdateRow
from .writer import WriteError, _resolve_column, anchor_for

EXPIRY_DAYS = 14

OPEN, APPLIED, REJECTED, SUPERSEDED, EXPIRED = (
    "open", "applied", "rejected", "superseded", "expired")


def now() -> datetime:
    return datetime.now(timezone.utc)


class ProposalStore:
    def __init__(self, home: Path):
        self.dir = home / "proposals"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()

    def _path(self, pid: str) -> Path:
        return self.dir / f"{pid}.json"

    @staticmethod
    def _valid(pid: str) -> bool:
        return bool(pid) and len(pid) <= 32 and all(c in "0123456789abcdef" for c in pid)

    def create(self, **fields: Any) -> dict:
        t = now()
        rec = {"id": secrets.token_hex(6), "status": OPEN, "created_at": t.isoformat(),
               "expires_at": (t + timedelta(days=EXPIRY_DAYS)).isoformat(), **fields}
        self.save(rec)
        return rec

    def save(self, rec: dict) -> None:
        p = self._path(rec["id"])
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=1, default=str))
        os.replace(tmp, p)

    def _load(self, p: Path) -> dict | None:
        try:
            rec = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        if rec.get("status") == OPEN and rec.get("expires_at", "") < now().isoformat():
            rec["status"] = EXPIRED
        return rec

    def get(self, pid: str) -> dict | None:
        pid = (pid or "").strip().lower()
        return self._load(self._path(pid)) if self._valid(pid) else None

    def all(self, limit: int = 50) -> list[dict]:
        recs = [r for p in self.dir.glob("*.json") if (r := self._load(p))]
        recs.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return recs[:limit]

    def open_for(self, doc_id: str) -> dict | None:
        return next((r for r in self.all(500)
                     if r.get("doc_id") == doc_id and r.get("status") == OPEN), None)


def describe_edits(view: DocView, req: EditRequest) -> list[dict]:
    """For each edit, the context a person needs to judge it: where it lands,
    what it replaces, and why. Captured against the revision it was planned on."""
    out: list[dict] = []
    for op in req.edits:
        d: dict[str, Any] = {"op": op.op, "section": None, "headers": [],
                             "before": None, "after": None, "context": None,
                             "reason": getattr(op, "reason", "")}
        if isinstance(op, AddRow):
            d["section"] = op.section
            try:
                anchor = anchor_for(view, op)
                d["headers"] = view.tables[anchor.table].headers
                d["anchor_row_id"] = anchor.row_id
                if anchor.kind == "data":
                    d["context"] = anchor.values
            except WriteError:
                pass
            d["after"] = list(op.values)
            d["category"] = op.category.value
        elif isinstance(op, UpdateRow):
            d["row_id"] = op.row_id
            row = view.row(op.row_id)
            if row is not None:
                table = view.tables[row.table]
                d["section"], d["headers"], d["before"] = row.section, table.headers, row.values
                after = list(row.values)
                for k, v in op.set_values.items():
                    try:
                        after[_resolve_column(table, k)] = v
                    except (WriteError, IndexError):
                        pass
                d["after"] = after
            if op.category is not None:
                d["category"] = op.category.value
        elif isinstance(op, RemoveRow):
            d["row_id"] = op.row_id
            d["reason"] = op.reason
            row = view.row(op.row_id)
            if row is not None:
                d["section"], d["headers"] = row.section, view.tables[row.table].headers
                d["before"] = row.values
        elif isinstance(op, ReplaceText):
            d["find"], d["replace"] = op.find, op.replace
            d["occurrences"] = sum(
                (c.text if op.match_case else c.text.lower()).count(
                    op.find if op.match_case else op.find.lower())
                for t in view.tables for r in t.rows for c in r.cells)
        src = getattr(op, "source", None)
        if src is not None:
            d["source"] = src.model_dump(mode="json")
        out.append(d)
    return out


def cited_refs(req: EditRequest) -> list[str]:
    refs: list[str] = []
    for op in req.edits:
        src = getattr(op, "source", None)
        if src is not None and src.kind.value not in ("larissa", "house_rule", "doc"):
            if src.ref not in refs:
                refs.append(src.ref)
    return refs
