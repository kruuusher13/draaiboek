"""MCP server. This is the entire surface Hermes sees.

Errors are returned as structured results, not exceptions, because the error
IS the teaching signal: every refusal tells the agent precisely what to do
next. An agent that gets "EditSafetyError" learns nothing; one that gets
"Larissa deleted this row on 13 Sep, it does not go back" learns the rule.
"""

from __future__ import annotations

from typing import Any

try:  # mcp >= 2.0 renamed FastMCP to MCPServer
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server

from pydantic import ValidationError

from .config import load_config
from .gdocs import AuthError
from .ops import EditRequest, Op
from .service import Draaiboek, GuardRefusal, RevisionConflict, UnsupportedClaim
from .writer import WriteError

mcp = _Server(
    "draaiboek",
    instructions=(
        "Surgical editing of Leeuwenbergh event runbooks. Call house_rules() first, "
        "every time. Always read_draaiboek() before proposing and pass its revision_id "
        "back to propose_edits(). You never decide to write: propose_edits() writes "
        "nothing and returns a proposal a person has to approve. They can do that in "
        "the workspace -- post the workspace_url -- or in the conversation, by telling "
        "you to go ahead: then call review_proposal() to show them what is in it and "
        "deploy_proposal() with their words quoted. Never supply an approval you were "
        "not given. Every fact needs a source with a literal quote -- if you cannot "
        "quote it, put it in Open Punten as a question instead of writing it as fact. "
        "There is no regenerate."
    ),
)
_cfg = load_config()
_svc = Draaiboek(_cfg)


def _fail(kind: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": kind, "message": message, **extra}


@mcp.tool()
def house_rules() -> str:
    """Larissa's standing rules for draaiboeken. Read this before every edit.

    These change often -- she adds rules in conversation ("lock this in") and
    they are written straight into the rules file. Never cache them.
    """
    return _svc.house_rules()


@mcp.tool()
def read_draaiboek(doc_id: str) -> dict[str, Any]:
    """Read a draaiboek. Always do this immediately before writing.

    Returns every row with a stable `row_id`, the section it sits in, and a
    `revision_id` you must pass back to apply_edits. Also reports what Larissa
    has changed by hand since the last time we looked.
    """
    try:
        view, drift = _svc.read(doc_id)
    except AuthError as e:
        return _fail("auth", str(e))
    except Exception as e:  # noqa: BLE001 -- surfaced to the agent verbatim
        return _fail("read_failed", f"{type(e).__name__}: {e}")

    out = view.to_dict()
    out["ok"] = True
    if drift.get("baseline"):
        out["changed_by_larissa_since_last_read"] = {
            "removed": drift["removed"], "added": drift["added"],
        }
    if drift.get("new_tombstones"):
        out["newly_protected"] = [
            {"section": t["section"], "values": t["values"]} for t in drift["new_tombstones"]
        ]
        out["note"] = (
            "Rows under 'newly_protected' were deleted by Larissa by hand. They are "
            "permanently blocked from being re-added."
        )
    return out


@mcp.tool()
def apply_edits(doc_id: str, expected_revision: str, edits: list[Op],
                note: str = "", dry_run: bool = False) -> dict[str, Any]:
    """Check (dry_run=true) or apply surgical edits to an existing draaiboek.

    Writing is disabled unless the operator set DRAAIBOEK_DIRECT_APPLY: changes
    go through propose_edits so Larissa approves them. There is no regenerate.

    `expected_revision` must be the revision_id from your most recent
    read_draaiboek of this doc. If Larissa edited it in the meantime the write
    is refused -- re-read and rebuild your edits on what is there now.

    Every add_row/update_row needs a `source` naming the email, ClickUp task,
    Xero quote or message from Larissa it came from, with a literal quote. If
    you cannot quote it, you do not know it: add it as an open question instead.
    """
    if not dry_run and not _cfg.direct_apply:
        return _fail("review_required",
                     "Edits are not written directly. Larissa approves every change.",
                     fix="Call propose_edits with the same arguments (plus a reason per "
                         "edit) and post the workspace_url to Larissa on Telegram. "
                         "dry_run=true still works here for checking a batch.")
    try:
        req = EditRequest(doc_id=doc_id, expected_revision=expected_revision,
                          edits=edits, note=note)
    except ValidationError as e:
        return _fail("invalid_edits", "The edits do not match the required shape.",
                     detail=e.errors(include_url=False))

    try:
        res = _svc.apply(req, dry_run=dry_run)
    except RevisionConflict as e:
        return _fail("revision_conflict", str(e),
                     current_revision=e.actual,
                     larissa_changed=e.drift,
                     fix="Call read_draaiboek again, then resend your edits against the new revision_id.")
    except UnsupportedClaim as e:
        return _fail("unsupported_claim",
                     "Your sources do not say what you wrote. Nothing was written.",
                     problems=e.problems,
                     fix="Re-read the evidence with read_evidence(ref) and quote it "
                         "exactly, or add the item to Open Punten as a question.")
    except GuardRefusal as e:
        return _fail("refused_by_house_rules", str(e),
                     violations=[v.to_dict() for v in e.violations],
                     fix="Nothing was written. Change the content to satisfy the rules, or "
                         "raise it with Larissa. Do not retry unchanged.")
    except WriteError as e:
        return _fail("write_error", str(e))
    except AuthError as e:
        return _fail("auth", str(e))
    except Exception as e:  # noqa: BLE001
        return _fail("failed", f"{type(e).__name__}: {e}")

    return {
        "ok": True,
        "dry_run": dry_run,
        "applied": res.applied,
        "revision_before": res.revision_before,
        "revision_after": res.revision_after,
        "new_row_ids": res.new_rows,
        "warnings": [v.to_dict() for v in res.warnings],
        "url": f"https://docs.google.com/document/d/{doc_id}/edit",
    }


@mcp.tool()
def propose_edits(doc_id: str, expected_revision: str, edits: list[Op], note: str = "",
                  title: str = "", context_refs: list[str] | None = None) -> dict[str, Any]:
    """Propose edits to a draaiboek for Larissa. Nothing is written.

    The batch is checked exactly like a write -- revision, house rules, quotes --
    and refused the same way, so fix any refusal before she sees it.

    Every edit needs a `reason`: one plain sentence she reads next to the change
    ("Bruidspaar wil proosten zonder alcohol, zie mail van 12 sep" is too much --
    no citations; "Het bruidspaar wil het proost-moment zonder alcohol" is right).

    On success you get a `workspace_url`: post it to Larissa on Telegram with one
    line on what changes. In the workspace she reads the sources, answers open
    questions, adjusts and deploys. Then call proposal_status(proposal_id).

    `title`: the event as she knows it ("Bruiloft Lisa-Lynde · 26 sep").
    `context_refs`: every evidence ref you gathered for this event, so she sees
    what was fetched. A new proposal for the same doc replaces the open one.
    """
    try:
        req = EditRequest(doc_id=doc_id, expected_revision=expected_revision,
                          edits=edits, note=note)
    except ValidationError as e:
        return _fail("invalid_edits", "The edits do not match the required shape.",
                     detail=e.errors(include_url=False))
    try:
        out = _svc.propose(req, title=title, context_refs=context_refs)
    except ValueError as e:
        return _fail("missing_reason", str(e), fix="Add a `reason` to every edit.")
    except RevisionConflict as e:
        return _fail("revision_conflict", str(e), current_revision=e.actual,
                     fix="Call read_draaiboek again and rebuild the edits on what is there now.")
    except UnsupportedClaim as e:
        return _fail("unsupported_claim",
                     "Your sources do not say what you wrote. Nothing was proposed.",
                     problems=e.problems,
                     fix="Quote the evidence exactly, or add the item to Open Punten as a question.")
    except GuardRefusal as e:
        return _fail("refused_by_house_rules", str(e),
                     violations=[v.to_dict() for v in e.violations],
                     fix="Change the content to satisfy the rules. Do not retry unchanged.")
    except (WriteError, AuthError) as e:
        return _fail("propose_failed", str(e))
    except Exception as e:  # noqa: BLE001
        return _fail("propose_failed", f"{type(e).__name__}: {e}")
    return {"ok": True, **out,
            "next": "Post workspace_url to Larissa on Telegram. Nothing changes until she deploys."}


@mcp.tool()
def proposal_status(proposal_id: str) -> dict[str, Any]:
    """What Larissa did with a proposal.

    status: open (not decided yet) | applied (deployed -- `included` lists the
    edit indices she kept; do not propose the others again unless she asks) |
    rejected (read `comment`: what she wants different) | superseded | expired.
    `answers`: her answers to open questions. Facts in an answer go into the
    draaiboek in a new proposal, with source kind `larissa`.
    """
    rec = _svc.proposals.get(proposal_id)
    if rec is None:
        return _fail("not_found", f"No proposal {proposal_id!r}.")
    return {"ok": True, **{k: rec.get(k) for k in (
        "id", "status", "doc_id", "title", "created_at", "expires_at", "decided_at",
        "included", "own_edits", "answers", "comment", "result")},
        "edits": len(rec.get("edits", [])),
        "url": f"https://docs.google.com/document/d/{rec['doc_id']}/edit"}


@mcp.tool()
def open_proposals() -> dict[str, Any]:
    """Everything waiting for a yes, newest first.

    Larissa does not say "deploy proposal 3b16fca76b39". She says "ja doe maar",
    an hour later, in whichever topic she happens to be in. Call this to find
    out what she means: each entry has the event as she knows it and the
    proposal_id the other tools need.

    One open proposal and an unmistakable yes -- deploy it. More than one, or
    any doubt about which event she means, ask her by name ("de bruiloft van
    26 september of de Rabobank-lunch?") and wait. Do not guess: a yes meant for
    one event is not approval for another.
    """
    out = [{"proposal_id": r["id"], "title": r.get("title") or r.get("doc_title"),
            "doc_id": r.get("doc_id"), "edits": len(r.get("edits", [])),
            "created_at": r.get("created_at"), "expires_at": r.get("expires_at")}
           for r in _svc.proposals.all(200) if r.get("status") == "open"]
    return {"ok": True, "open": len(out), "proposals": out}


@mcp.tool()
def review_proposal(proposal_id: str) -> dict[str, Any]:
    """Every proposed change in readable form, so you can show it in the chat.

    Use this when someone asks what you are proposing, or before you ask them to
    approve it. Each item has the index you pass to `include` in deploy_proposal,
    the section it lands in, the row as it will read, and the reason. Read them
    out grouped by section -- a person deciding does not want forty-two lines in
    one block.
    """
    rec = _svc.proposals.get(proposal_id)
    if rec is None:
        return _fail("not_found", f"No proposal {proposal_id!r}.")
    display = rec.get("display") or []
    items = [{"index": i, **(d if isinstance(d, dict) else {"text": str(d)})}
             for i, d in enumerate(display)]
    return {"ok": True, "id": rec["id"], "status": rec.get("status"),
            "title": rec.get("title"), "doc_id": rec.get("doc_id"),
            "note": rec.get("note"), "warnings": rec.get("warnings") or [],
            "edits": len(rec.get("edits", [])), "items": items,
            "url": f"https://docs.google.com/document/d/{rec['doc_id']}/edit"}


@mcp.tool()
def deploy_proposal(proposal_id: str, approved_by: str, approval_quote: str,
                    include: list[int] | None = None) -> dict[str, Any]:
    """Write a proposal that a person approved in the conversation.

    Only call this when someone has actually said yes, in words, in this
    conversation. `approval_quote` is their message, literally -- the same rule
    the rows live under, applied to the approval itself. Do not paraphrase it,
    do not summarise it, and never supply it for a yes you did not receive.
    "Ja doe maar" is a quote; "Romir approved it" is not.

    `include`: leave empty to deploy everything, or pass the indices from
    review_proposal to deploy part of it -- that is how "alles behalve de
    Triade-regel" is done without a round trip through the workspace.

    A partial deploy still closes the proposal. Deploying three of forty-two
    writes those three and marks the whole thing applied; the other thirty-nine
    are not waiting for you, and this tool will refuse the proposal as "not
    open" if you come back for them. So do not promise someone the rest later
    from the same proposal. If they approve part now and more afterwards, build
    a new proposal for the rest -- read the document again first, because you
    have just changed it.

    The write is pinned to the revision the proposal was built on, so if the
    document changed in the meantime this refuses instead of overwriting. That
    is not a failure: re-read, rebuild on what is there now, propose again.
    """
    try:
        out = _svc.deploy_from_chat(proposal_id, approved_by, approval_quote, include)
    except PermissionError as e:
        return _fail("chat_deploy_disabled", str(e),
                     fix="Post the workspace_url instead, or ask the operator to enable it.")
    except ValueError as e:
        return _fail("cannot_deploy", str(e))
    except RevisionConflict as e:
        return _fail("revision_conflict",
                     "The draaiboek changed after this proposal was made. Nothing was written.",
                     current_revision=e.actual,
                     fix="Call read_draaiboek again, rebuild the edits on what is there now, "
                         "and propose again before asking for approval.")
    except UnsupportedClaim as e:
        return _fail("unsupported_claim", "A source no longer supports its row.",
                     problems=e.problems)
    except GuardRefusal as e:
        return _fail("refused_by_house_rules", str(e),
                     violations=[v.to_dict() for v in e.violations])
    except (WriteError, AuthError) as e:
        return _fail("deploy_failed", str(e))
    except Exception as e:  # noqa: BLE001
        return _fail("deploy_failed", f"{type(e).__name__}: {e}")
    return {"ok": True, **out,
            "next": "Tell them what went in and what is still open. The document is now live."}


@mcp.tool()
def reject_proposal(proposal_id: str, comment: str) -> dict[str, Any]:
    """Close a proposal that was turned down, recording why.

    `comment` is what they want different, in their words. Then build the next
    proposal from it -- do not re-propose the same batch.
    """
    try:
        rec = _svc.reject_proposal(proposal_id, comment)
    except ValueError as e:
        return _fail("cannot_reject", str(e))
    return {"ok": True, "id": rec["id"], "status": rec["status"],
            "comment": rec.get("comment", "")}


@mcp.tool()
def open_point_answers(doc_id: str) -> dict[str, Any]:
    """Every answer Larissa gave to an open question on this draaiboek, oldest
    first. An answer is her word: cite it with source kind `larissa`."""
    return {"ok": True, "answers": _svc.answers_for(doc_id)}


@mcp.tool()
def create_draaiboek(title: str, template_id: str = "", folder_id: str = "") -> dict[str, Any]:
    """Create a new draaiboek for a brand new event, as a copy of the template.

    Use this ONLY when no document exists yet -- check the ClickUp task
    description first, Larissa pastes the link there. Never for a recurring
    format where a previous doc exists.
    """
    try:
        return {"ok": True, **_svc.create(title, template_id=template_id or None,
                                          folder_id=folder_id or None)}
    except (WriteError, AuthError) as e:
        return _fail("create_failed", str(e))
    except Exception as e:  # noqa: BLE001
        return _fail("create_failed", f"{type(e).__name__}: {e}")


@mcp.tool()
def gather(query: str, kinds: list[str] | None = None, limit: int = 8) -> dict[str, Any]:
    """Search Gmail, ClickUp, Xero and Missive at once for an event.

    Call this BEFORE writing anything. Only `ref`s returned here can be cited
    as a source in apply_edits -- a reference you compose yourself will be
    refused, and so will a quote that does not literally appear in the text.

    Query naturally: "Rogier Kalma 15 september", "uitvaart van den Bogerd
    cateraar". Bodies are previewed here; read_evidence(ref) gives you the
    full untruncated text, which is where supplier phone numbers live.
    """
    try:
        return {"ok": True, **_svc.gather(query, kinds, limit)}
    except Exception as e:  # noqa: BLE001
        return _fail("gather_failed", f"{type(e).__name__}: {e}")


@mcp.tool()
def read_evidence(ref: str) -> dict[str, Any]:
    """The full text of one gathered document -- email, task or quote.

    Never truncated: signatures carry the phone numbers the call sheet needs.
    """
    ev = _svc.evidence_body(ref)
    if ev is None:
        return _fail("not_found", f"No evidence with ref {ref!r}. Call gather() first.")
    return {"ok": True, **ev.to_dict()}


@mcp.tool()
def read_attachment(ref: str, index: int = 0) -> dict[str, Any]:
    """Read what is INSIDE an attachment -- floor plan, rider, menu, signed quote.

    gather() and read_evidence() list an item's attachments by name; this opens
    one and returns its text. The text becomes quotable evidence in its own
    right under `ref` + "#a<index>", so a row sourced from page 2 of a rider is
    verified exactly like one sourced from an email body.

    A scanned PDF or a photo has no text to give. You will be told so plainly.
    When that happens, ask Larissa what it says -- never guess at the contents
    of a document you could not read.
    """
    return _svc.read_attachment(ref, index)


@mcp.tool()
def daily_brief(days: int = 21) -> dict[str, Any]:
    """Start here, every day.

    Every upcoming event with what is still missing from it: no draaiboek, open
    points, phone numbers not yet obtained, no Triade delivery booked, a head
    count the room will not take. Ranked by how soon it bites, cancelled events
    left out.

    Work the urgent ones first. This replaces deciding by hand what to look at,
    which is the same question every morning.
    """
    return {"ok": True, **_svc.daily_brief(days)}


@mcp.tool()
def event_format(title: str, guests: int = 0) -> dict[str, Any]:
    """What a recurring format normally needs: room, partner, standing routines,
    the documents it requires, and the order to place.

    Read it before building a draaiboek for a format that repeats -- AIGTW,
    Jazz, a wedding. It answers "wat is de normale bestelling voor deze show",
    which is otherwise rebuilt from memory every time. Quantities that depend on
    head count are worked out from it; check them against the actual ticket
    sales before ordering.

    An unrecognised format says so rather than inventing a standard.
    """
    return {"ok": True, **_svc.event_format(title, guests or None)}


@mcp.tool()
def tables_for(metres: float) -> dict[str, Any]:
    """How many tables of which length to order for a given run of metres.

    Triade stocks 1.2 m and 2 m; this returns whole-unit combinations that are
    never short, fewest pieces first.
    """
    return {"ok": True, **_svc.tables_for(metres)}


@mcp.tool()
def venue() -> dict[str, Any]:
    """Leeuwenbergh itself: rooms, capacities, and the rules that follow from
    them. Read it before writing an Inrichting chapter or a guest count -- the
    grote zaal holds 225, Córdoba 20, the small meeting room 9, and a full
    house is 200 across the three.
    """
    return {"ok": True, **_svc.venue()}


@mcp.tool()
def source_status() -> dict[str, Any]:
    """Which systems are connected. A system that is not configured is skipped
    silently by gather(), so check here when evidence looks thin."""
    return {"ok": True, "sources": _svc.sources.status(),
            "evidence_cached": _svc.evidence.stats()}


@mcp.tool()
def history(doc_id: str = "", limit: int = 25) -> dict[str, Any]:
    """Everything that has been done to a draaiboek: edits, refusals, sources."""
    return {"ok": True, "entries": _svc.ledger.entries(doc_id or None, limit)}


@mcp.tool()
def protected_rows(doc_id: str) -> dict[str, Any]:
    """Rows Larissa deleted by hand, which may never be re-added."""
    tombs = _svc.ledger.load_tombstones(doc_id)
    return {"ok": True, "count": len(tombs),
            "rows": [{"section": t.get("section"), "values": t.get("values"),
                      "removed_at": t.get("removed_at")} for t in tombs.values()]}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
