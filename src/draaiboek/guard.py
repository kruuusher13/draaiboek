"""Rule enforcement at the write boundary.

The premise: you do not fix an agent that ignores instructions by writing
better instructions. You fix it by refusing the write. Everything in
rules/guards.yaml is checked here, before a single request reaches Google.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable

import yaml

from .model import DocView, slugify
from .ops import AddRow, Op, RemoveRow, ReplaceText, UpdateRow


@dataclass
class Violation:
    severity: str   # "block" | "warn"
    rule: str
    edit_index: int
    message: str
    offending: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def bare(text: str) -> str:
    """Normalised and stripped of punctuation, so a row cannot come back just
    by gaining an exclamation mark or losing a hyphen."""
    return re.sub(r"[^\w ]+", "", normalise(text)).strip()


# A monetary-looking amount: 1.250,00 / 12,50 / 340 -- but not a time (18.30),
# a date, or a bare count that a goods list needs ("40 barkrukken").
_AMOUNT = re.compile(r"(?<![:\d])\d{1,3}(?:[.\s]\d{3})*[,.]\d{2}\b|[€$]\s*\d")


class Guard:
    """Hot-reloads guards.yaml so rule edits take effect without a restart."""

    def __init__(self, guards_path: Path):
        self.path = guards_path
        self._mtime: float = -1.0
        self._spec: dict[str, Any] = {}

    @property
    def spec(self) -> dict[str, Any]:
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            return {}
        if mtime != self._mtime:
            self._spec = yaml.safe_load(self.path.read_text()) or {}
            self._mtime = mtime
        return self._spec

    # -- per-op text extraction ------------------------------------------
    @staticmethod
    def _texts(op: Op) -> list[str]:
        if isinstance(op, AddRow):
            return list(op.values)
        if isinstance(op, UpdateRow):
            return list(op.set_values.values())
        if isinstance(op, ReplaceText):
            return [op.replace]
        return []

    # -- checks -----------------------------------------------------------
    def check(
        self,
        edits: Iterable[Op],
        view: DocView | None = None,
        tombstones: dict[str, dict] | None = None,
        by_larissa: frozenset[int] | set[int] = frozenset(),
    ) -> list[Violation]:
        """`by_larissa`: indices of edits she made herself in the workspace. Her
        own section and her own deletions are hers to change; every content
        rule (no prices, no citations, lexicon) still applies to everyone."""
        spec = self.spec
        out: list[Violation] = []
        locked = {slugify(s) for s in spec.get("locked_sections", [])}
        tombstones = tombstones or {}

        for i, op in enumerate(edits):
            texts = self._texts(op)

            # 1. forbidden content patterns
            for rule in spec.get("forbidden", []):
                rx = re.compile(rule["pattern"])
                for t in texts:
                    m = rx.search(t)
                    if m and rule.get("requires_number") and not _AMOUNT.search(t):
                        continue
                    if m:
                        out.append(Violation(
                            severity=rule.get("severity", "block"),
                            rule=rule["name"], edit_index=i,
                            message=rule["message"].strip(), offending=m.group(0),
                        ))
                        break

            # 2. required lexicon
            for lex in spec.get("lexicon", []):
                rx = re.compile(lex["wrong"])
                for t in texts:
                    m = rx.search(t)
                    if m:
                        out.append(Violation(
                            severity=lex.get("severity", "block"),
                            rule=f"lexicon:{lex['right'].lower()}", edit_index=i,
                            message=lex["message"].strip(), offending=m.group(0),
                        ))
                        break

            # 3. locked sections -- resolved against the live doc, so a row id
            #    that happens to sit under Bijzonderheden is caught too.
            target_section = None
            if isinstance(op, AddRow):
                target_section = op.section
            elif isinstance(op, (UpdateRow, RemoveRow)) and view is not None:
                row = view.row(op.row_id)
                target_section = row.section if row else None
            if target_section and slugify(target_section) in locked and i not in by_larissa:
                out.append(Violation(
                    severity="block", rule="locked_section", edit_index=i,
                    message=f"'{target_section}' is Larissa's section. Florentine never writes there.",
                    offending=target_section,
                ))

            # 4. tombstones -- she deleted it by hand; it does not come back
            tspec = spec.get("tombstones", {})
            if (tspec.get("enabled") and isinstance(op, AddRow) and tombstones
                    and i not in by_larissa):
                key = bare(" ".join(op.values))
                longest = bare(max(op.values, key=len)) if op.values else ""
                for fp, rec in tombstones.items():
                    if key == bare(rec.get("key", "")) or (
                            longest and longest == bare(rec.get("longest", ""))):
                        out.append(Violation(
                            severity=tspec.get("severity", "block"),
                            rule="tombstone", edit_index=i,
                            message=tspec.get("message", "").strip()
                                    + f" (removed by hand on {rec.get('removed_at', '?')[:10]})",
                            offending=rec.get("key", "")[:120],
                        ))
                        break

            # 4b. ...nor may replace_text be used as a side door to the same end
            if tspec.get("enabled") and isinstance(op, ReplaceText) and tombstones:
                new = bare(op.replace)
                for rec in tombstones.values():
                    if new and new in (bare(rec.get("key", "")), bare(rec.get("longest", ""))):
                        out.append(Violation(
                            severity=tspec.get("severity", "block"),
                            rule="tombstone", edit_index=i,
                            message=tspec.get("message", "").strip()
                                    + " A replacement may not put it back either.",
                            offending=op.replace[:120],
                        ))
                        break

            # 5. structural safety -- never anchor an insert on a band/header.
            #    This is the "coffee break rendered on top of the header" bug:
            #    anchoring on a band collapses the inserted text into cell 0.
            if isinstance(op, AddRow) and op.after_row_id and view is not None:
                anchor = view.row(op.after_row_id)
                if anchor is None:
                    out.append(Violation(
                        severity="block", rule="unknown_row", edit_index=i,
                        message=f"No row '{op.after_row_id}' in this revision. Re-read the doc.",
                        offending=op.after_row_id,
                    ))
                elif anchor.kind == "band":
                    # A row inserted below a band inherits its merged cell, so
                    # every value lands in column 0. Header rows are ordinary
                    # rows and are a legitimate anchor for a section's first row.
                    out.append(Violation(
                        severity="block", rule="bad_anchor", edit_index=i,
                        message=f"'{op.after_row_id}' is a section header band. Anchor on a "
                                f"data row, or on the column-header row to insert first in "
                                f"the section -- a row inserted below a band inherits its "
                                f"merged cell and collapses into column 0.",
                        offending=anchor.joined()[:80],
                    ))

            # 6. row existence + content expectation
            if isinstance(op, (UpdateRow, RemoveRow)) and view is not None:
                row = view.row(op.row_id)
                if row is None:
                    out.append(Violation(
                        severity="block", rule="unknown_row", edit_index=i,
                        message=f"No row '{op.row_id}' in this revision. Re-read the doc.",
                        offending=op.row_id,
                    ))
                elif not op.expect_contains.strip() and row.joined().strip():
                    out.append(Violation(
                        severity="block", rule="expectation_failed", edit_index=i,
                        message=f"Row '{op.row_id}' is not empty. It currently reads: "
                                f"{row.joined()!r}. Re-read before writing.",
                        offending=row.joined()[:120],
                    ))
                elif normalise(op.expect_contains) not in normalise(row.joined()):
                    out.append(Violation(
                        severity="block", rule="expectation_failed", edit_index=i,
                        message=f"Row '{op.row_id}' does not contain {op.expect_contains!r}. "
                                f"It currently reads: {row.joined()!r}. Re-read before writing.",
                        offending=row.joined()[:120],
                    ))
                elif row.kind != "data":
                    out.append(Violation(
                        severity="block", rule="bad_target", edit_index=i,
                        message=f"'{op.row_id}' is a {row.kind} row, not content.",
                        offending=row.joined()[:80],
                    ))

        return out


def blocking(violations: list[Violation]) -> list[Violation]:
    return [v for v in violations if v.severity == "block"]
