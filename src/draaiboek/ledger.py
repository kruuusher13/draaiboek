"""Append-only audit trail, document snapshots, and tombstones.

This is the "track everything" half of the system. Three things are persisted:

  ledger.jsonl        every tool call: args, provenance, verdict, revisions
  snapshots/<doc>/    full text of the doc at each revision we have seen
  tombstones/<doc>    rows Larissa deleted by hand, which may never return

Tombstones are derived, not declared: when a read finds a row missing that was
present in the last snapshot, and no op of ours removed it, she removed it.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .model import DocView


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm(s: str) -> str:
    return " ".join(s.split()).strip().lower()


class Ledger:
    def __init__(self, home: Path):
        self.home = home
        self.path = home / "ledger.jsonl"
        self.snapshots = home / "snapshots"
        self.tombstones = home / "tombstones"
        for d in (self.snapshots, self.tombstones):
            d.mkdir(parents=True, exist_ok=True)

    # -- audit trail -------------------------------------------------------
    def append(self, **entry: Any) -> dict[str, Any]:
        entry.setdefault("ts", _now())
        line = json.dumps(entry, ensure_ascii=False, default=str)
        # O_APPEND write is atomic for lines below PIPE_BUF; safe for the
        # concurrent-agent case without a lock file.
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return entry

    def entries(self, doc_id: str | None = None, limit: int = 50) -> list[dict]:
        if not self.path.exists():
            return []
        out: list[dict] = []
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if doc_id and e.get("doc_id") != doc_id:
                    continue
                out.append(e)
        return out[-limit:]

    # -- snapshots ---------------------------------------------------------
    def _snap_dir(self, doc_id: str) -> Path:
        d = self.snapshots / doc_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save_snapshot(self, view: DocView) -> Path:
        d = self._snap_dir(view.doc_id)
        payload = {
            "doc_id": view.doc_id,
            "revision_id": view.revision_id,
            "title": view.title,
            "captured_at": _now(),
            "rows": [
                {"row_id": r.row_id, "section": r.section, "kind": r.kind,
                 "values": r.values, "fp": r.fingerprint()}
                for t in view.tables for r in t.rows
            ],
        }
        # revision ids are opaque and may contain path-hostile characters
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in view.revision_id)[:80]
        p = d / f"{int(time.time())}.{safe}.json"
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=1))
        (d / "latest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1))
        self._prune(d, keep=40)
        return p

    @staticmethod
    def _prune(d: Path, keep: int) -> None:
        snaps = sorted(p for p in d.glob("*.json") if p.name != "latest.json")
        for p in snaps[:-keep]:
            try:
                p.unlink()
            except OSError:
                pass

    def latest_snapshot(self, doc_id: str) -> dict | None:
        p = self.snapshots / doc_id / "latest.json"
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            return None

    # -- drift detection ---------------------------------------------------
    @staticmethod
    def diff(old: dict | None, new: DocView) -> dict[str, list]:
        """What changed between a stored snapshot and a fresh read.

        Reported per-row by content, not by position, so an insert higher up
        does not show every row below it as 'changed'.
        """
        if not old:
            return {"added": [], "removed": [], "unchanged": 0, "baseline": False}
        old_rows = {r["fp"]: r for r in old.get("rows", []) if r["kind"] == "data"}
        new_rows = {r.fingerprint(): r for r in new.data_rows()}
        removed = [old_rows[fp] for fp in old_rows.keys() - new_rows.keys()]
        added = [
            {"row_id": r.row_id, "section": r.section, "values": r.values, "fp": fp}
            for fp, r in new_rows.items() if fp not in old_rows
        ]
        return {
            "added": added,
            "removed": [{"section": r["section"], "values": r["values"], "fp": r["fp"]} for r in removed],
            "unchanged": len(old_rows.keys() & new_rows.keys()),
            "baseline": True,
            "from_revision": old.get("revision_id"),
        }

    # -- tombstones --------------------------------------------------------
    def _tomb_path(self, doc_id: str) -> Path:
        return self.tombstones / f"{doc_id}.json"

    def load_tombstones(self, doc_id: str) -> dict[str, dict]:
        p = self._tomb_path(doc_id)
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            return {}

    def _save_tombstones(self, doc_id: str, data: dict) -> None:
        p = self._tomb_path(doc_id)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
        os.replace(tmp, p)

    def add_tombstones(self, doc_id: str, rows, by: str) -> None:
        """Rows Larissa deleted herself in the workspace. Same effect as deleting
        them by hand in Google Docs: they may never be re-added by the agent."""
        tombs = self.load_tombstones(doc_id)
        for r in rows:
            tombs[r.fingerprint()] = {
                "key": _norm(" ".join(r.values)),
                "longest": _norm(max(r.values, key=len)) if r.values else "",
                "section": r.section, "values": r.values,
                "removed_at": _now(), "removed_by": by,
            }
        self._save_tombstones(doc_id, tombs)

    def record_agent_removal(self, doc_id: str, fingerprints: list[str]) -> None:
        """Rows *we* deleted on instruction. Excluded from tombstoning."""
        p = self.home / "agent_removed.json"
        data = json.loads(p.read_text()) if p.exists() else {}
        data.setdefault(doc_id, [])
        data[doc_id] = (data[doc_id] + fingerprints)[-500:]
        p.write_text(json.dumps(data, ensure_ascii=False))

    def _agent_removed(self, doc_id: str) -> set[str]:
        p = self.home / "agent_removed.json"
        if not p.exists():
            return set()
        try:
            return set(json.loads(p.read_text()).get(doc_id, []))
        except json.JSONDecodeError:
            return set()

    def reconcile_tombstones(self, view: DocView) -> list[dict]:
        """Called after every read. Rows that vanished without one of our own
        remove_row ops were deleted by Larissa -- record them permanently."""
        old = self.latest_snapshot(view.doc_id)
        d = self.diff(old, view)
        if not d.get("baseline"):
            return []
        ours = self._agent_removed(view.doc_id)
        tombs = self.load_tombstones(view.doc_id)
        fresh = []
        for r in d["removed"]:
            if r["fp"] in ours or r["fp"] in tombs:
                continue
            rec = {
                "key": _norm(" ".join(r["values"])),
                "longest": _norm(max(r["values"], key=len)) if r["values"] else "",
                "section": r["section"],
                "values": r["values"],
                "removed_at": _now(),
                "detected_from_revision": old.get("revision_id"),
            }
            tombs[r["fp"]] = rec
            fresh.append(rec)
        if fresh:
            self._save_tombstones(view.doc_id, tombs)
        return fresh
