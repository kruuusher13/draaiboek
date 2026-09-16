"""Orchestration: the only place reads, guards, writes and the ledger meet."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import Config, load_config
from .evidence import EvidenceStore
from .gdocs import Google
from .secrets import load_env
from .sources.base import Evidence
from .sources.base import normalise as normalise_text
from .sources.registry import Sources
from .guard import Guard, Violation, blocking
from .ledger import Ledger
from .local import LocalDocs, is_local
from .model import DocView
from .ops import AddRow, EditRequest, RemoveRow, ReplaceText, UpdateRow
from . import proposals as P
from .reader import parse_document
from .writer import (
    WriteError, anchor_for, plan_structural, plan_text, structural_request,
)


class RevisionConflict(RuntimeError):
    def __init__(self, expected: str, actual: str, drift: dict):
        self.expected, self.actual, self.drift = expected, actual, drift
        super().__init__(
            f"Document changed since you read it (you had {expected}, it is now {actual}). "
            f"Larissa's edits win: re-read the document and rebuild your edits on top of "
            f"what is there now."
        )


class UnsupportedClaim(RuntimeError):
    """A write cited a source whose text does not contain the claimed quote."""

    def __init__(self, problems: list[dict]):
        self.problems = problems
        super().__init__(
            "Edit refused -- unsupported by its sources:\n"
            + "\n".join(f"  edit #{p['edit_index']}: {p['message']}" for p in problems)
        )


class GuardRefusal(RuntimeError):
    def __init__(self, violations: list[Violation]):
        self.violations = violations
        lines = [f"  [{v.rule}] edit #{v.edit_index}: {v.message}" +
                 (f"  -> {v.offending!r}" if v.offending else "")
                 for v in violations]
        super().__init__("Edit refused by house rules:\n" + "\n".join(lines))


@dataclass
class ApplyResult:
    doc_id: str
    revision_before: str
    revision_after: str
    applied: int
    warnings: list[Violation] = field(default_factory=list)
    times_collapsed: int = 0
    reminders: list[str] = field(default_factory=list)
    larissa_changes: dict[str, Any] = field(default_factory=dict)
    new_rows: dict[int, str] = field(default_factory=dict)


class Draaiboek:
    def __init__(self, cfg: Config | None = None, google: Google | None = None):
        self.cfg = cfg or load_config()
        self.google = google or Google(self.cfg)
        load_env()  # no-op if load_config already did it; needed when cfg is injected
        self.ledger = Ledger(self.cfg.home)
        self.guard = Guard(self.cfg.guards_path)
        self.evidence = EvidenceStore(self.cfg.home)
        self.sources = Sources(self.google)
        self.localdocs = LocalDocs(self.cfg.home)
        self.proposals = P.ProposalStore(self.cfg.home)

    def backend(self, doc_id: str):
        """`local:<name>` documents run on disk; everything else on Google.

        Identical code either way -- only the transport differs -- so a dry run
        against a local copy proves the same thing it would on the real doc."""
        return self.localdocs if is_local(doc_id) else self.google

    # -- read --------------------------------------------------------------
    def read(self, doc_id: str, *, record: bool = True) -> tuple[DocView, dict]:
        view = parse_document(self.backend(doc_id).get_document(doc_id))
        drift = self.ledger.diff(self.ledger.latest_snapshot(doc_id), view)
        new_tombs = self.ledger.reconcile_tombstones(view) if record else []
        if record:
            self.ledger.save_snapshot(view)
            self.ledger.append(
                event="read", doc_id=doc_id, revision=view.revision_id,
                rows=len(view.data_rows()), sections=len(view.sections),
                larissa_removed=len(new_tombs),
            )
        if new_tombs:
            drift["new_tombstones"] = new_tombs
        return view, drift

    def house_rules(self) -> str:
        p = self.cfg.house_rules_path
        return p.read_text() if p.exists() else "(no rules file at %s)" % p

    def save_house_rules(self, text: str) -> dict[str, Any]:
        """Larissa edits her rules in the workspace. The previous version is kept
        in DRAAIBOEK_HOME/rules_history, and the agent reads the new text on its
        next call -- rules are hot-reloaded, nothing restarts."""
        import os
        from datetime import datetime, timezone

        text = text.replace("\r\n", "\n")
        if not text.strip():
            raise ValueError("The rules cannot be empty.")
        p = self.cfg.house_rules_path
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        if p.exists():
            history = self.cfg.home / "rules_history"
            history.mkdir(parents=True, exist_ok=True)
            (history / f"house_rules-{stamp}.md").write_text(p.read_text())
        tmp = p.with_suffix(".tmp")
        tmp.write_text(text if text.endswith("\n") else text + "\n")
        os.replace(tmp, p)
        self.ledger.append(event="rules_edited", by="Larissa (workspace)", chars=len(text),
                           backup=f"house_rules-{stamp}.md")
        return {"ok": True, "saved_at": stamp}

    # -- write -------------------------------------------------------------
    def apply(self, req: EditRequest, *, dry_run: bool = False,
              by_larissa: frozenset[int] = frozenset()) -> ApplyResult:
        view, drift = self.read(req.doc_id)

        # 1. Larissa's edits win.
        if view.revision_id != req.expected_revision:
            self.ledger.append(event="refused", reason="revision_conflict", doc_id=req.doc_id,
                               expected=req.expected_revision, actual=view.revision_id,
                               edits=[e.model_dump(mode="json") for e in req.edits])
            raise RevisionConflict(req.expected_revision, view.revision_id, drift)

        # 2. House rules, enforced.
        tombs = self.ledger.load_tombstones(req.doc_id)
        violations = self.guard.check(req.edits, view, tombs, by_larissa=by_larissa)
        if blocking(violations):
            self.ledger.append(event="refused", reason="guard", doc_id=req.doc_id,
                               violations=[v.to_dict() for v in blocking(violations)],
                               edits=[e.model_dump(mode="json") for e in req.edits])
            raise GuardRefusal(blocking(violations))
        warnings = [v for v in violations if v.severity == "warn"]
        warnings += self._injection_warnings(req.edits)

        # 2b. Every claim must be supported by something we actually retrieved.
        problems = self._unsupported(req.edits)
        if problems:
            self.ledger.append(event="refused", reason="unsupported_claim",
                               doc_id=req.doc_id, problems=problems)
            raise UnsupportedClaim(problems)

        structurals = plan_structural(view, list(req.edits))
        self._check_conflicts(structurals)
        self._check_same_row(req.edits)

        if dry_run:
            return ApplyResult(req.doc_id, view.revision_id, view.revision_id,
                               applied=0, warnings=warnings, larissa_changes=drift)

        # 3. Phase A -- structure. One atomic batch, descending document order,
        #    so no request invalidates the indices of any request after it.
        be = self.backend(req.doc_id)
        if structurals:
            be.batch_update(req.doc_id, [structural_request(s) for s in structurals])

        # 4. Re-read and locate the rows we created.
        new_rows: dict[int, str] = {}
        if structurals:
            expected = self._expected_row_counts(view, structurals)
            doc = None
            for tbl, count in expected.items():
                doc = be.wait_for_rows(req.doc_id, tbl, count)
            view2 = parse_document(doc) if doc else self.read(req.doc_id, record=False)[0]
            new_rows = self._locate_new_rows(view, view2, structurals, list(req.edits))
        else:
            view2 = view

        # 4b. A row inserted below a section band inherits the band's merged
        #     cell, so everything typed into it lands in column 0 and the row
        #     then reads as another chapter heading. Chapters with no column
        #     header leave no other anchor, so split the merge back apart.
        if new_rows:
            unmerge = []
            for row_id in new_rows.values():
                row = view2.row(row_id)
                if row is None or not row.cells:
                    continue
                table = view2.tables[row.table]
                if max((c.column_span for c in row.cells), default=1) > 1:
                    unmerge.append({"unmergeTableCells": {"tableRange": {
                        "tableCellLocation": {
                            "tableStartLocation": {"index": table.start_index},
                            "rowIndex": row.index, "columnIndex": 0},
                        "rowSpan": 1, "columnSpan": table.columns}}})
            if unmerge:
                be.batch_update(req.doc_id, unmerge)
                view2 = parse_document(be.get_document(req.doc_id))

        # 5. Phase B -- text and shading. One atomic batch, planned on the
        #    re-read document. Row ids are positions: a row below an insert or a
        #    delete has a different id now, so follow each updated row there.
        edits = (self._follow_moved_rows(view, view2, structurals, list(req.edits))
                 if structurals else list(req.edits))
        text_requests = plan_text(view2, edits, new_rows)
        if text_requests:
            be.batch_update(req.doc_id, text_requests)

        # 6. Settle, snapshot, record.
        removed_fps = [
            view.row(e.row_id).fingerprint()
            for e in req.edits
            if isinstance(e, RemoveRow) and view.row(e.row_id) is not None
        ]
        if removed_fps:
            self.ledger.record_agent_removal(req.doc_id, removed_fps)

        mid, _ = self.read(req.doc_id, record=False)
        collapsed = self.collapse_repeated_times(req.doc_id, mid)

        final, _ = self.read(req.doc_id)
        reminders = self.reminders_for(final)
        self.ledger.append(
            event="applied", doc_id=req.doc_id, note=req.note,
            times_collapsed=collapsed, reminders=reminders,
            revision_before=view.revision_id, revision_after=final.revision_id,
            edits=[e.model_dump(mode="json") for e in req.edits],
            provenance=[
                {"op": e.op, "source": e.source.model_dump(mode="json")}
                for e in req.edits if hasattr(e, "source")
            ],
            warnings=[v.to_dict() for v in warnings],
            new_rows=new_rows,
        )
        return ApplyResult(
            doc_id=req.doc_id, revision_before=view.revision_id,
            revision_after=final.revision_id, applied=len(req.edits),
            warnings=warnings, larissa_changes=drift, new_rows=new_rows,
            times_collapsed=collapsed, reminders=reminders,
        )

    # -- the workspace -----------------------------------------------------
    def propose(self, req: EditRequest, *, title: str = "",
                context_refs: list[str] | None = None) -> dict[str, Any]:
        """Check a batch exactly as a write would be checked, then park it for
        Larissa instead of writing it.

        Raises the same refusals as apply(), so Hermes fixes a bad batch before
        she ever sees it -- she only reviews proposals that would go through.
        """
        missing = [i for i, e in enumerate(req.edits) if not (e.reason or "").strip()]
        if missing:
            raise ValueError(
                f"Every proposed edit needs a `reason`: one sentence Larissa reads next to "
                f"the change. Missing on edit(s) {missing}.")
        res = self.apply(req, dry_run=True)
        view, _ = self.read(req.doc_id, record=False)
        if view.revision_id != req.expected_revision:
            raise RevisionConflict(req.expected_revision, view.revision_id, {})

        with self.proposals.lock:
            for old in self.proposals.all(500):
                if old.get("doc_id") == req.doc_id and old.get("status") == P.OPEN:
                    old["status"] = P.SUPERSEDED
                    self.proposals.save(old)
            refs = P.cited_refs(req)
            for ref in context_refs or []:
                if ref not in refs and self.evidence.get(ref) is not None:
                    refs.append(ref)
            rec = self.proposals.create(
                doc_id=req.doc_id, doc_title=view.title, title=title or view.title,
                expected_revision=req.expected_revision, note=req.note,
                edits=[e.model_dump(mode="json") for e in req.edits],
                display=P.describe_edits(view, req), refs=refs,
                warnings=[v.to_dict() for v in res.warnings])
        self.ledger.append(event="proposed", doc_id=req.doc_id, proposal=rec["id"],
                           revision=req.expected_revision, note=req.note,
                           edits=rec["edits"], refs=refs)
        return {"proposal_id": rec["id"],
                "workspace_url": f"{self.cfg.public_url}/?proposal={rec['id']}",
                "expires_at": rec["expires_at"], "edits": len(req.edits),
                "warnings": rec["warnings"]}

    @staticmethod
    def _larissa_ops(view: DocView, own: list[dict], answers: list[dict]):
        """What she did in the workspace, as ops. Her word is the source.

        `expect` is the row's text as she saw it when she made the change, so a
        row that moved underneath her (someone edited the doc) is refused, not
        silently overwritten."""
        from datetime import date
        from .ops import Source, SourceKind

        stamp = date.today().strftime("%-d %b %Y")

        def src(text: str) -> Source:
            text = text.strip()
            return Source(kind=SourceKind.LARISSA, ref=f"workspace · {stamp}",
                          quote=text if len(text) >= 3 else "aangepast door Larissa")

        def data_row(row_id: str, allow_header: bool = False):
            row = view.row(str(row_id))
            if row is None or row.kind == "band" or (row.kind == "header" and not allow_header):
                raise ValueError(f"Row {row_id!r} cannot be changed here. Reload the draaiboek.")
            return row

        ops: list = []
        origins: list[dict] = []
        for j, item in enumerate(own):
            kind = item.get("type")
            if kind == "cell":
                row = data_row(item["row_id"])
                values = {}
                for col, text in (item.get("values") or {}).items():
                    if not str(col).isdigit() or int(col) >= len(row.cells):
                        raise ValueError(f"Column {col!r} does not exist in row {row.row_id}.")
                    values[str(int(col))] = str(text)
                if not values:
                    continue
                ops.append(UpdateRow(row_id=row.row_id, expect_contains=str(item.get("expect", "")),
                                     set_values=values, source=src(" · ".join(values.values())),
                                     category=item.get("category") or None,
                                     reason=item.get("reason") or "Aangepast door Larissa"))
            elif kind == "add":
                anchor = data_row(item["after_row_id"], allow_header=True)
                values = [str(v) for v in item.get("values") or []]
                ops.append(AddRow(section=anchor.section or item.get("section", ""),
                                  after_row_id=anchor.row_id, values=values,
                                  category=item.get("category") or "algemeen",
                                  source=src(" · ".join(v for v in values if v)),
                                  reason=item.get("reason") or "Toegevoegd door Larissa"))
            elif kind == "remove":
                row = data_row(item["row_id"])
                ops.append(RemoveRow(row_id=row.row_id, expect_contains=str(item.get("expect", "")),
                                     reason=item.get("reason") or "Verwijderd door Larissa"))
            else:
                raise ValueError(f"Unknown change type {kind!r}.")
            origins.append({"origin": "larissa", "own_index": j})

        recorded = []
        for k, a in enumerate(answers):
            text = str(a.get("answer", "")).strip()
            if not text:
                continue
            recorded.append({"question": a.get("question", ""), "answer": text,
                             "row_id": a.get("row_id"), "proposal_index": a.get("proposal_index")})
            if a.get("row_id"):
                row = data_row(a["row_id"])
                col = int(a.get("col", len(row.cells) - 1))
                if not 0 <= col < len(row.cells):
                    raise ValueError(f"Column {col} does not exist in row {row.row_id}.")
                ops.append(UpdateRow(row_id=row.row_id, expect_contains=str(a.get("expect", "")),
                                     set_values={str(col): f"Antwoord: {text}"}, source=src(text),
                                     reason="Antwoord van Larissa op een open punt"))
                origins.append({"origin": "answer", "answer_index": k})
        return ops, origins, recorded

    def _workspace_request(self, doc_id: str, expected_revision: str, proposal_id: str | None,
                           include: list[int], own: list[dict], answers: list[dict]):
        view, _ = self.read(doc_id, record=False)
        rec = self.proposals.get(proposal_id) if proposal_id else None
        if rec is not None and (rec.get("doc_id") != doc_id or rec.get("status") != P.OPEN):
            rec = None
        chosen = sorted({i for i in include if rec and 0 <= i < len(rec["edits"])})
        hermes = [rec["edits"][i] for i in chosen] if rec else []
        mine, origins, recorded = self._larissa_ops(view, own, answers)
        origins = [{"origin": "hermes", "proposal_index": i} for i in chosen] + origins
        by_larissa = frozenset(range(len(hermes), len(hermes) + len(mine)))
        edits = hermes + mine
        req = EditRequest(doc_id=doc_id, expected_revision=expected_revision,
                          edits=edits, note="workspace deploy") if edits else None
        return view, req, by_larissa, rec, chosen, origins, recorded

    def check(self, doc_id: str, expected_revision: str, proposal_id: str | None = None,
              include: list[int] = (), own: list[dict] = (), answers: list[dict] = ()) -> dict:
        """Everything apply() would refuse, per change, without raising."""
        from pydantic import ValidationError
        try:
            view, req, by_larissa, rec, chosen, origins, recorded = self._workspace_request(
                doc_id, expected_revision, proposal_id, list(include), list(own), list(answers))
        except (ValueError, ValidationError, KeyError) as e:
            return {"ok": False, "revision_ok": True, "items": [], "errors": [str(e)]}
        out: dict[str, Any] = {"revision_ok": view.revision_id == expected_revision,
                               "current_revision": view.revision_id, "errors": [],
                               "items": [dict(o) for o in origins], "answers": len(recorded)}
        if req is not None:
            violations = self.guard.check(req.edits, view, self.ledger.load_tombstones(doc_id),
                                          by_larissa=by_larissa)
            unsupported = self._unsupported(req.edits)
            for i, item in enumerate(out["items"]):
                item["violations"] = [v.to_dict() for v in violations if v.edit_index == i]
                item["unsupported"] = [u["message"] for u in unsupported if u["edit_index"] == i]
            if not any(v.severity == "block" for v in violations) and not unsupported:
                try:
                    self._check_conflicts(plan_structural(view, list(req.edits)))
                    self._check_same_row(req.edits)
                except WriteError as e:
                    out["errors"].append(str(e))
        blocked = any(v["severity"] == "block"
                      for item in out["items"] for v in item.get("violations", []))
        unsupported_any = any(item.get("unsupported") for item in out["items"])
        out["ok"] = (out["revision_ok"] and not out["errors"] and not blocked
                     and not unsupported_any and (req is not None or bool(recorded)))
        return out

    def deploy(self, doc_id: str, expected_revision: str, proposal_id: str | None = None,
               include: list[int] = (), own: list[dict] = (), answers: list[dict] = ()) -> dict:
        """Larissa pressed Deploy. One batch: the proposed changes she kept, her
        own edits and her answers -- through the same revision lock and rules."""
        with self.proposals.lock:
            view, req, by_larissa, rec, chosen, origins, recorded = self._workspace_request(
                doc_id, expected_revision, proposal_id, list(include), list(own), list(answers))
            res = self.apply(req, by_larissa=by_larissa) if req is not None else None

            removed = [view.row(e.row_id) for i, e in enumerate(req.edits if req else [])
                       if i in by_larissa and isinstance(e, RemoveRow)]
            if removed:
                self.ledger.add_tombstones(doc_id, [r for r in removed if r is not None],
                                           by="Larissa (workspace)")
            for a in recorded:
                self.ledger.append(event="answered", doc_id=doc_id, proposal=rec and rec["id"], **a)
            if rec is not None:
                rec.update(status=P.APPLIED, decided_at=P.now().isoformat(), included=chosen,
                           answers=recorded, own_edits=len(by_larissa),
                           result=res and {"revision_after": res.revision_after,
                                           "reminders": res.reminders})
                self.proposals.save(rec)
            self.ledger.append(event="deployed", doc_id=doc_id, proposal=rec and rec["id"],
                               included=chosen, own_edits=len(by_larissa), answers=len(recorded),
                               revision_after=res and res.revision_after)
        return {
            "applied": res.applied if res else 0,
            "revision_before": expected_revision,
            "revision_after": res.revision_after if res else expected_revision,
            "times_collapsed": res.times_collapsed if res else 0,
            "reminders": res.reminders if res else [],
            "warnings": [v.to_dict() for v in res.warnings] if res else [],
            "answers": recorded,
            "quote_gaps": self.quote_gaps(doc_id),
        }

    def deploy_from_chat(self, proposal_id: str, approved_by: str, approval_quote: str,
                         include: list[int] | None = None) -> dict[str, Any]:
        """Deploy a proposal that a person approved in conversation.

        The workspace is one place a human can say yes; it is not the only one.
        What protects the document is not the web page -- it is that a person
        decided, that the write is locked to the revision the proposal was built
        on, and that every row still carries a quotable source. All of that holds
        here.

        Two things it refuses. It will not deploy without a literal quote of the
        message that approved it -- the same rule the rows themselves live under,
        applied to the approval: if you cannot quote it, you were not told to do
        it. And it pins the write to the revision the proposal was made against,
        so a document somebody edited in the meantime is a conflict rather than
        an overwrite. Her edits still win.
        """
        if not self.cfg.chat_deploy:
            raise PermissionError(
                "Deploying from chat is switched off. Set DRAAIBOEK_CHAT_DEPLOY=1 to "
                "allow it, or send the workspace link and let her deploy there.")
        who = (approved_by or "").strip()
        quote = (approval_quote or "").strip()
        if not who or len(quote) < 3:
            raise ValueError(
                "Deploying needs `approved_by` (who said yes) and `approval_quote` (their "
                "words, literally). Without both there is no record that anyone agreed.")

        rec = self.proposals.get(proposal_id)
        if rec is None:
            raise ValueError(f"No proposal {proposal_id!r}.")
        if rec.get("status") != P.OPEN:
            raise ValueError(f"Proposal {proposal_id} is {rec.get('status')}, not open.")

        total = len(rec.get("edits", []))
        chosen = sorted({i for i in (include if include is not None else range(total))
                         if 0 <= i < total})
        if not chosen:
            raise ValueError("No edits selected, so there is nothing to deploy.")

        self.ledger.append(event="approved", doc_id=rec["doc_id"], proposal=rec["id"],
                           by=who[:120], quote=quote[:500], channel="chat",
                           included=chosen, of=total)
        out = self.deploy(rec["doc_id"], rec["expected_revision"],
                          proposal_id=rec["id"], include=chosen)
        out["approved_by"] = who
        out["included"] = chosen
        out["url"] = f"https://docs.google.com/document/d/{rec['doc_id']}/edit"
        return out

    def reject_proposal(self, proposal_id: str, comment: str = "") -> dict:
        """Send the proposal back to Hermes with what should be different."""
        with self.proposals.lock:
            rec = self.proposals.get(proposal_id)
            if rec is None or rec["status"] != P.OPEN:
                raise ValueError("This proposal is no longer open.")
            rec.update(status=P.REJECTED, decided_at=P.now().isoformat(),
                       comment=comment.strip()[:2000])
            self.proposals.save(rec)
        self.ledger.append(event="proposal_rejected", doc_id=rec["doc_id"],
                           proposal=rec["id"], comment=rec["comment"])
        return rec

    def answers_for(self, doc_id: str) -> list[dict]:
        return [{k: e.get(k) for k in ("ts", "question", "answer", "row_id", "proposal")}
                for e in self.ledger.entries(doc_id, 1000) if e.get("event") == "answered"]

    # -- her standing document rules ---------------------------------------
    @staticmethod
    def _time_tables(view: DocView) -> list[int]:
        return [t.index for t in view.tables
                if t.headers and t.headers[0].strip().lower() in ("tijd", "tijdstip")]

    def collapse_repeated_times(self, doc_id: str, view: DocView) -> int:
        """Blank the time cell when it repeats the row above.

        Larissa, 13 Sep 2026: "I want her not to repeat time in the column of
        time, for every row. She has to leave it open, when it's in the same
        time (this is something I told her already many times)."

        Enforced structurally rather than asked for in a prompt, because asking
        is exactly what has been failing.
        """
        reqs, changed = [], 0
        for ti in self._time_tables(view):
            table = view.tables[ti]
            previous = None
            for row in table.rows:
                if row.kind != "data" or not row.cells:
                    if row.kind == "band":
                        previous = None
                    continue
                current = row.cells[0].text.strip()
                if current and current == previous:
                    cell = row.cells[0]
                    start, end = cell.start_index + 1, cell.end_index - 1
                    if end > start:
                        reqs.append({"deleteContentRange": {
                            "range": {"startIndex": start, "endIndex": end}}})
                        changed += 1
                elif current:
                    previous = current
        if reqs:
            reqs.sort(key=lambda r: r["deleteContentRange"]["range"]["startIndex"],
                      reverse=True)
            self.backend(doc_id).batch_update(doc_id, reqs)
        return changed

    def daily_brief(self, days: int = 21) -> dict[str, Any]:
        """What needs doing today, across every upcoming event."""
        from .daily import Brief
        return Brief(self).today(days)

    def event_format(self, title: str, guests: int | None = None) -> dict[str, Any]:
        """What this recurring format normally needs, and what to order for it."""
        from .formats import Formats
        return Formats(self.cfg.rules_dir).standard_for(title, guests)

    def tables_for(self, metres: float) -> dict[str, Any]:
        from .formats import Formats
        return Formats(self.cfg.rules_dir).tables_for(metres)

    def venue(self) -> dict[str, Any]:
        """The building: rooms, capacities and the thresholds that hang off
        them. Standing facts, so no draaiboek rediscovers them and no two
        disagree about how many fit in the grote zaal."""
        import yaml
        p = self.cfg.venue_path
        if not p.exists():
            return {}
        try:
            return yaml.safe_load(p.read_text()) or {}
        except Exception:  # noqa: BLE001
            return {}

    def _capacity_notes(self, view: DocView) -> list[str]:
        """Check the guest numbers written in the document against the room."""
        import re

        v = self.venue()
        if not v:
            return []
        text = " ".join(r.joined() for r in view.data_rows()) + " " + (view.title or "")
        counts = [int(n) for n in re.findall(r"(?<![\d:.,])(\d{2,4})\s*(?:gasten|pax|personen)", text, re.I)]
        counts += [int(n) for n in re.findall(r"(?:gasten|pax|personen)\D{0,6}(\d{2,4})", text, re.I)]
        if not counts:
            return []
        top = max(counts)
        out: list[str] = []

        hall = next((r for r in v.get("rooms", []) if r.get("max_persons")), None)
        if hall and top > hall["max_persons"]:
            out.append(
                f"{top} gasten is meer dan {hall['name']} aankan "
                f"({hall['max_persons']} volgens de plattegrond). Controleer de verdeling "
                f"over de zalen."
            )
        for t in v.get("thresholds", []):
            over = t.get("over")
            if over and top > over:
                need = t.get("setup")
                low = text.lower()
                already = any(w in low for w in ("triade", "koffiepunt")) if not need else "triade" in low
                if not already:
                    out.append(f"Meer dan {over} gasten ({top}): {t['action']}.")
        return out

    def reminders_for(self, view: DocView) -> list[str]:
        """Things Larissa asked to be reminded about, checked against the doc."""
        out: list[str] = []
        text = " ".join(r.joined().lower() for r in view.data_rows())
        out.extend(self._capacity_notes(view))
        lev = view.section_by_name("Leveringen")
        lev_rows = [view.row(r) for r in lev.row_ids] if lev else []
        lev_text = " ".join((r.joined().lower() if r else "") for r in lev_rows)

        if "triade" not in lev_text:
            out.append(
                "Geen Triade-levering in het hoofdstuk Leveringen. Er is bij elk "
                "evenement een levering van Triade -- controleer of deze nog besteld "
                "moet worden."
            )
        title = (view.title or "").lower()
        if any(w in title or w in text for w in ("bruiloft", "wedding", "huwelijk",
                                                 "ceremonie", "trouw")):
            out.append(
                "Bruiloft: zet altijd een bloemenvaas op de tafel waar het paar tekent."
            )
            out.append(
                "Bruiloft: controleer of de telefoonnummers van de ceremoniemeester(s) "
                "binnen zijn."
            )
        for who, word in (("cateraar", "cateraar"), ("bloemist", "bloemist"),
                          ("DJ", " dj"), ("musicus", "musicus")):
            if word.strip() in text and who.lower() not in " ".join(
                    (view.row(r).joined().lower() if view.row(r) else "")
                    for s in view.sections if s.slug in ("call-sheet", "contacten-call-sheet")
                    for r in s.row_ids):
                out.append(
                    f"Er is sprake van een externe {who}. Neem vóór het evenement "
                    f"contact op en zet naam en telefoonnummer in de Call Sheet."
                )
        return out

    def quote_gaps(self, doc_id: str) -> list[dict]:
        """Rows that are in the draaiboek but not backed by the Xero quote.

        Larissa, 13 Sep 2026: "When there is a contradiction between something
        mentioned in the draaiboek and not in the quote, please alarm Larissa so
        she can put it in the quote."

        Because every row records where it came from, this is just a filter:
        anything agreed by email or by her in chat, which the quote does not
        mention, is unbilled work. Chapters that are pure logistics are skipped.
        """
        billable = {"catering", "inrichting", "leveringen", "techniek", "opbouw"}
        quote_text = ""
        for ref in {e.get("source", {}).get("ref", "")
                    for entry in self.ledger.entries(doc_id, 500)
                    for e in entry.get("edits", [])
                    if e.get("source", {}).get("kind") == "xero"}:
            ev = self.evidence.get(ref)
            if ev:
                quote_text += " " + normalise_text(ev.body)

        gaps, seen = [], set()
        for entry in self.ledger.entries(doc_id, 500):
            if entry.get("event") != "applied":
                continue
            for e in entry.get("edits", []):
                src = e.get("source") or {}
                # xero      -> it is the quote
                # house_rule -> standing practice, not a new agreement
                # doc        -> already in the draaiboek before this run
                if src.get("kind") in ("xero", "house_rule", "doc", None):
                    continue
                section = (e.get("section") or "").lower()
                if not any(b in section for b in billable):
                    continue
                text = " · ".join(x for x in e.get("values", []) if x)
                if not text or text in seen:
                    continue
                seen.add(text)
                key = normalise_text(" ".join(e.get("values", [])[:2]))
                if quote_text and key and key[:28] in quote_text:
                    continue
                gaps.append({"section": e.get("section"), "row": text,
                             "source": f"{src.get('kind')} ({src.get('ref')})"})
        return gaps

    def _injection_warnings(self, edits) -> list[Violation]:
        """Flag a row whose source text is talking to the agent rather than to
        Larissa. Not a refusal -- the mail may be legitimate and the decision is
        hers -- but it never passes silently."""
        out = []
        for i, e in enumerate(edits):
            src = getattr(e, "source", None)
            if src is None or src.kind.value in ("larissa", "house_rule", "doc"):
                continue
            reasons = self.evidence.suspicion(src.ref)
            if reasons:
                out.append(Violation(
                    severity="warn", rule="injected_source", edit_index=i,
                    message=("The source for this row " + ", and ".join(reasons) +
                             ". Someone outside the company wrote it. Check it with "
                             "Larissa before trusting it."),
                    offending=src.ref,
                ))
        return out

    # -- evidence ----------------------------------------------------------
    def _unsupported(self, edits) -> list[dict]:
        out = []
        for i, e in enumerate(edits):
            src = getattr(e, "source", None)
            if src is None:
                continue
            if src.kind.value == "house_rule":
                ok, why = self._verify_house_rule(src.quote)
            else:
                ok, why = self.evidence.verify(src.kind.value, src.ref, src.quote)
            if not ok:
                out.append({"edit_index": i, "ref": src.ref,
                            "kind": src.kind.value, "message": why})
        return out

    def _verify_house_rule(self, quote: str) -> tuple[bool, str]:
        """`house_rule` skips evidence checking, so without this it is the one
        source kind an agent could use to launder anything it liked."""
        import re

        from .sources.base import normalise

        def plain(t: str) -> str:
            # The rules are markdown. Emphasis, table pipes and bullets are
            # formatting, not wording -- a rule must not become unquotable
            # because someone bolded two words in it.
            t = re.sub(r"[*_`#>|]+", " ", t)
            return normalise(t)

        text = plain(self.house_rules())
        if plain(quote) in text:
            return True, ""
        return False, (
            f"No standing rule says {quote!r}. Rules live in rules/house_rules.md "
            f"and only Larissa adds them. If this should be a rule, ask her -- do "
            f"not assert it as one."
        )

    def gather(self, query: str, kinds: list[str] | None = None,
               limit: int = 8) -> dict[str, Any]:
        """Search every connected system and cache what comes back.

        Only refs returned here can be cited by a later write."""
        evs, errors = self.sources.gather(query, kinds, limit)
        self.evidence.put_many(evs)
        self.ledger.append(event="gathered", query=query, found=len(evs),
                           refs=[e.ref for e in evs], errors=errors)
        return {"query": query, "evidence": [e.preview() for e in evs],
                "refs": [e.ref for e in evs], "unavailable": errors}

    def read_attachment(self, ref: str, index: int) -> dict[str, Any]:
        """Pull one attachment and read what is inside it.

        The extracted text is stored as evidence of its own, under the parent
        ref plus the attachment index, so a row taken from page 2 of a rider
        verifies exactly like one taken from an email body. Without this, every
        fact in a floor plan or a menu would have to be retyped -- or invented.
        """
        from .extract import ExtractError, extract

        parent = self.evidence.get(ref) or self.sources.fetch(ref)
        names = (parent.meta.get("attachments") or []) if parent else []
        if parent is None:
            return {"ok": False, "error": "not_found",
                    "message": f"No evidence with ref {ref!r}. Call gather() first."}
        if index < 0 or index >= len(names):
            return {"ok": False, "error": "no_such_attachment",
                    "message": f"{parent.title!r} has {len(names)} attachment(s); "
                               f"you asked for index {index}.",
                    "attachments": names}

        try:
            f = self.sources.attachment(ref, index)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": "download_failed", "message": str(e)[:300]}
        if f is None:
            return {"ok": False, "error": "download_failed",
                    "message": "The source did not return the file."}

        att_ref = f"{ref}#a{index}"
        try:
            text = extract(f)
        except ExtractError as e:
            self.ledger.append(event="attachment_unreadable", ref=att_ref,
                               filename=f.filename, reason=str(e))
            return {"ok": False, "error": "unreadable", "ref": att_ref,
                    "filename": f.filename, "mime": f.mime, "bytes": len(f.data),
                    "message": str(e)}

        ev = Evidence(
            kind=parent.kind, ref=att_ref,
            title=f"{f.filename} ({parent.title})", body=text,
            when=parent.when, url=parent.url,
            meta={"attachment_of": ref, "filename": f.filename,
                  "mime": f.mime, "bytes": len(f.data)},
        )
        self.evidence.put(ev)
        self.ledger.append(event="attachment_read", ref=att_ref,
                           filename=f.filename, chars=len(text))
        return {"ok": True, "ref": att_ref, "filename": f.filename, "mime": f.mime,
                "bytes": len(f.data), "chars": len(text), "text": text}

    def evidence_body(self, ref: str) -> Evidence | None:
        ev = self.evidence.get(ref) or self.sources.fetch(ref)
        if ev is not None:
            self.evidence.put(ev)
        return ev

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _check_conflicts(structurals) -> None:
        deleted = {(s.table_start, s.row_index) for s in structurals if s.kind == "delete"}
        anchored = {(s.table_start, s.row_index) for s in structurals if s.kind == "insert"}
        clash = deleted & anchored
        if clash:
            raise WriteError(
                "One batch both deletes a row and anchors an insert on it. "
                "Split this into two calls."
            )

    @staticmethod
    def _follow_moved_rows(before: DocView, after: DocView, structurals, edits) -> list:
        """Rewrite each UpdateRow's row id to the row's position after the
        structural batch, and verify it is still the same row.

        Without this, an update planned on "t1r8" is written to whatever sits
        at t1r8 after an insert above it -- the row above the intended one.
        """
        start_to_table = {t.start_index: t.index for t in before.tables}
        inserted: dict[int, list[int]] = {}
        deleted: dict[int, list[int]] = {}
        for s in structurals:
            bucket = inserted if s.kind == "insert" else deleted
            bucket.setdefault(start_to_table[s.table_start], []).append(s.row_index)
        out = []
        for e in edits:
            if isinstance(e, UpdateRow):
                old = before.row(e.row_id)
                if old is None:
                    raise WriteError(f"Unknown row {e.row_id!r}")
                t, i = old.table, old.index
                j = (i + sum(1 for a in inserted.get(t, []) if a < i)
                     - sum(1 for d in deleted.get(t, []) if d < i))
                rows = after.tables[t].rows if t < len(after.tables) else []
                moved = rows[j] if j < len(rows) else None
                if moved is None or moved.fingerprint() != old.fingerprint():
                    raise WriteError(
                        f"Row {e.row_id} was not where it should be after rows were added "
                        f"or removed. Its text was not written; re-read the document.")
                e = e.model_copy(update={"row_id": moved.row_id})
            out.append(e)
        return out

    @staticmethod
    def _check_same_row(edits) -> None:
        """Two text changes to one row in one batch would each be planned
        against the row's original indices, and the second would land in the
        wrong place. Refuse instead."""
        seen: set[str] = set()
        for e in edits:
            if isinstance(e, (UpdateRow, RemoveRow)):
                if e.row_id in seen:
                    raise WriteError(
                        f"Two changes target row {e.row_id} in one batch. Combine them "
                        f"into one change.")
                seen.add(e.row_id)

    @staticmethod
    def _expected_row_counts(view: DocView, structurals) -> dict[int, int]:
        start_to_table = {t.start_index: t.index for t in view.tables}
        counts: dict[int, int] = {}
        for s in structurals:
            t = start_to_table[s.table_start]
            counts.setdefault(t, len(view.tables[t].rows))
            counts[t] += 1 if s.kind == "insert" else -1
        return counts

    @staticmethod
    def _locate_new_rows(before: DocView, after: DocView, structurals, edits) -> dict[int, str]:
        """Map each AddRow op to the row id it created.

        Because the structural batch ran in descending document order, every
        request saw the original indices. The final position of a new row is
        therefore computable, and we verify it is blank before writing to it.
        """
        start_to_table = {t.start_index: t.index for t in before.tables}
        by_table: dict[int, dict[str, list]] = {}
        for s in structurals:
            t = start_to_table[s.table_start]
            b = by_table.setdefault(t, {"del": [], "ins": []})
            b["del" if s.kind == "delete" else "ins"].append(s)

        out: dict[int, str] = {}
        for t, b in by_table.items():
            deletes = sorted(s.row_index for s in b["del"])
            inserts = sorted(b["ins"], key=lambda s: s.op_index)  # op order
            seen_same_anchor: dict[int, int] = {}
            for s in inserts:
                a = s.row_index
                j = seen_same_anchor.get(a, 0)
                seen_same_anchor[a] = j + 1
                final = (
                    (a + 1)
                    - sum(1 for d in deletes if d <= a)
                    + sum(1 for o in inserts if o.row_index < a)
                    + j
                )
                table = after.tables[t]
                if final >= len(table.rows):
                    raise WriteError(
                        f"Computed row {final} for edit #{s.op_index}, but table {t} "
                        f"has {len(table.rows)} rows. Nothing was written to it."
                    )
                row = table.rows[final]
                if any(c.text.strip() for c in row.cells):
                    raise WriteError(
                        f"Expected a blank new row at t{t}r{final} for edit #{s.op_index}, "
                        f"found {row.joined()!r}. Refusing to overwrite it."
                    )
                out[s.op_index] = row.row_id
        return out

    # -- template ----------------------------------------------------------
    def make_template(self, source_doc_id: str, name: str = "Draaiboek — TEMPLATE") -> dict:
        """Build the master template from a real draaiboek you are happy with.

        Copies it first, then empties the copy's data rows, keeping the logo,
        column widths, fonts, legend, section bands and colours exactly as a
        human made them. The source document is never touched.

        This is why there is no rendering code: production style is inherited
        from a document, not reimplemented in Python.
        """
        be = self.backend(source_doc_id)
        doc_id = be.copy_document(source_doc_id, name, self.cfg.drive_folder_id)
        view = parse_document(self.backend(doc_id).get_document(doc_id))

        # delete every data row, bottom-up so indices stay valid
        # Bottom of the document upwards, across tables as well as within
        # them. Emptying an earlier table moves every later table, so deleting
        # top-down invalidates the table positions captured from this read.
        targets = [(t, r) for t in view.tables for r in t.rows if r.kind == "data"]
        targets.sort(key=lambda tr: (tr[0].start_index, tr[1].start_index),
                     reverse=True)
        reqs = [{"deleteTableRow": {"tableCellLocation": {
                    "tableStartLocation": {"index": t.start_index},
                    "rowIndex": r.index, "columnIndex": 0}}}
                for t, r in targets]
        removed = len(reqs)
        if reqs:
            be.batch_update(doc_id, reqs)

        # Emptying the tables is not enough. A draaiboek keeps its title, its
        # subtitle and a long tail of loose paragraphs -- open points, the
        # "TO DO - PRODUCTIE" list -- outside any table. Left in place they
        # travel into every document made from this template: the Fever
        # draaiboek arrived carrying the wedding's suppliers and to-do list.
        doc = be.get_document(doc_id)
        body = doc["body"]["content"]
        tables = [el for el in body if "table" in el]
        cuts: list[dict] = []

        if tables:
            tail_from = tables[-1].get("endIndex", 0)
            tail_to = body[-1].get("endIndex", 0) - 1
            if tail_to > tail_from:
                cuts.append({"deleteContentRange": {"range": {
                    "startIndex": tail_from, "endIndex": tail_to}}})

        # Title and subtitle become placeholders a human can see and edit.
        first_table = tables[0].get("startIndex") if tables else None
        headings = []
        for el in body:
            if first_table is not None and el.get("startIndex", 0) >= first_table:
                break
            para = el.get("paragraph")
            if not para:
                continue
            text = "".join(e.get("textRun", {}).get("content", "")
                           for e in para.get("elements", [])).strip()
            if text and "Techniek & Media" not in text and text != "Tijdschema":
                headings.append((el, text))
        for el, text in headings[:2][::-1]:
            start = el["startIndex"]
            cuts.append({"deleteContentRange": {"range": {
                "startIndex": start, "endIndex": start + len(text)}}})
            cuts.append({"insertText": {"location": {"index": start},
                                        "text": "{{TITEL}}" if el is headings[0][0]
                                        else "{{ONDERTITEL}}"}})

        if cuts:
            cuts.sort(key=lambda r: -(r.get("deleteContentRange", r.get("insertText", {}))
                                      .get("range", {}).get("startIndex",
                                      r.get("insertText", {}).get("location", {})
                                      .get("index", 0))))
            be.batch_update(doc_id, cuts)

        final, _ = self.read(doc_id)
        self.ledger.append(event="template_created", doc_id=doc_id,
                           source=source_doc_id, rows_emptied=removed)
        return {"doc_id": doc_id,
                "url": f"https://docs.google.com/document/d/{doc_id}/edit",
                "rows_emptied": removed, "sections": final.section_names(),
                "next": "Set DRAAIBOEK_TEMPLATE_DOC_ID to this id, then check the "
                        "document by eye: logo, column widths, legend, band colours."}

    # -- create ------------------------------------------------------------
    def create(self, title: str, *, subtitle: str = "", template_id: str | None = None,
               folder_id: str | None = None) -> dict[str, Any]:
        """A new draaiboek is a copy of the template document.

        Nothing is drawn by code. The logo, column widths, Calibri, the legend,
        the section bands and the colours are correct because a human made them
        correct once, in a Google Doc anyone can edit.
        """
        tpl = template_id or self.cfg.template_doc_id
        if not tpl:
            raise WriteError(
                "No template configured. Set DRAAIBOEK_TEMPLATE_DOC_ID to the id of the "
                "master draaiboek document, or pass template_id."
            )
        be = self.backend(tpl)
        doc_id = be.copy_document(tpl, title, folder_id or self.cfg.drive_folder_id)
        # The template carries placeholders where its own event's name used to
        # be, so a new draaiboek is named for its own event, not the one the
        # template was cut from.
        fills = [{"replaceAllText": {"containsText": {"text": "{{TITEL}}", "matchCase": True},
                                     "replaceText": title}},
                 {"replaceAllText": {"containsText": {"text": "{{ONDERTITEL}}", "matchCase": True},
                                     "replaceText": subtitle or ""}}]
        try:
            be.batch_update(doc_id, fills)
        except Exception:  # noqa: BLE001 -- a template without placeholders still works
            pass
        view, _ = self.read(doc_id)
        self.ledger.append(event="created", doc_id=doc_id, title=title, template=tpl,
                           revision=view.revision_id)
        return {
            "doc_id": doc_id,
            "url": f"https://docs.google.com/document/d/{doc_id}/edit",
            "revision_id": view.revision_id,
            "sections": view.section_names(),
        }
