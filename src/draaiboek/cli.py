"""Command line. Same code path as the MCP server -- debugging here is
debugging production, not a parallel implementation that drifts."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .config import load_config
from .gdocs import AuthError, Google, blob_client_id
from .ops import EditRequest
from .service import Draaiboek, GuardRefusal, RevisionConflict, UnsupportedClaim
from .writer import WriteError

def _url(doc_id: str) -> str:
    if str(doc_id).startswith("local:") or "/" not in doc_id and "-" in doc_id and len(doc_id) < 40:
        return f"(local document: {doc_id})"
    return f"https://docs.google.com/document/d/{doc_id}/edit"


class _DocUrl:
    @staticmethod
    def format(doc_id: str) -> str:
        return _url(doc_id)


DOC_URL = _DocUrl()


def _svc() -> Draaiboek:
    return Draaiboek(load_config())


def _doc_id(s: str) -> str:
    """Accept a bare id or a pasted Google Docs URL."""
    if "/d/" in s:
        return s.split("/d/", 1)[1].split("/", 1)[0]
    return s.strip()


def cmd_auth(args) -> int:
    cfg = load_config()
    path = Google(cfg).authorise_interactive()
    print(f"Authorised. Token stored at {path}")
    return 0


def cmd_read(args) -> int:
    svc = _svc()
    view, drift = svc.read(_doc_id(args.doc_id))
    if args.json:
        print(json.dumps(view.to_dict(), ensure_ascii=False, indent=2))
        return 0
    print(f"{view.title}\n{DOC_URL.format(view.doc_id)}\nrevision: {view.revision_id}\n")
    for t in view.tables:
        print(f"-- table {t.index} ({t.role}, {t.columns} cols) " + "-" * 30)
        for r in t.rows:
            mark = {"band": "##", "header": " |", "data": "  "}[r.kind]
            print(f"{r.row_id:>8} {mark} {r.joined()[:100]}")
    if view.warnings:
        print("\nwarnings:", *view.warnings, sep="\n  ")
    if drift.get("removed") or drift.get("added"):
        print(f"\nchanged by hand since last read: "
              f"-{len(drift['removed'])} +{len(drift['added'])}")
        for r in drift["removed"]:
            print(f"  - {' | '.join(r['values'])}")
    return 0


def cmd_edit(args) -> int:
    payload: dict[str, Any] = json.loads(
        sys.stdin.read() if args.file == "-" else open(args.file).read()
    )
    payload.setdefault("doc_id", _doc_id(args.doc_id))
    svc = _svc()
    if not payload.get("expected_revision"):
        view, _ = svc.read(payload["doc_id"])
        payload["expected_revision"] = view.revision_id
        print(f"(using current revision {view.revision_id})", file=sys.stderr)
    try:
        res = svc.apply(EditRequest(**payload), dry_run=args.dry_run)
    except GuardRefusal as e:
        print(str(e), file=sys.stderr)
        return 2
    except UnsupportedClaim as e:
        print(str(e), file=sys.stderr)
        return 5
    except RevisionConflict as e:
        print(str(e), file=sys.stderr)
        return 3
    except (WriteError, AuthError) as e:
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        return 4
    print(f"applied {res.applied} edits   ->  {DOC_URL.format(res.doc_id)}")
    print(f"revision {res.revision_before}  ->  {res.revision_after}")
    if res.times_collapsed:
        print(f"\n{res.times_collapsed} repeated time(s) blanked (her rule: never repeat "
              f"the time on consecutive rows)")
    for w in res.warnings:
        print(f"\nwaarschuwing [{w.rule}] {w.message}")
    if res.reminders:
        print("\nHERINNERINGEN VOOR LARISSA")
        for r in res.reminders:
            print(f"  · {r}")
    return 0


def cmd_create(args) -> int:
    print(json.dumps(_svc().create(args.title, template_id=args.template),
                     ensure_ascii=False, indent=2))
    return 0


def cmd_sources(args) -> int:
    svc = _svc()
    for name, st in svc.sources.status().items():
        mark = "ok  " if st.get("configured") else "--  "
        print(f"  [{mark}] {name}{'  ' + st['error'] if st.get('error') else ''}")
    print(f"\n  evidence cached: {svc.evidence.stats()['documents']} documents")
    return 0


def cmd_gather(args) -> int:
    svc = _svc()
    res = svc.gather(args.query, args.kinds.split(",") if args.kinds else None, args.limit)
    for e in res["evidence"]:
        print(f"\n{e['ref']}  [{e['kind']}]  {e['when'][:31]}")
        print(f"  {e['title']}")
        for line in e["body"].splitlines()[:6]:
            print(f"    {line[:110]}")
    if res["unavailable"]:
        print("\nnot searched:")
        for k, v in res["unavailable"].items():
            print(f"  {k}: {v[:140]}")
    print(f"\n{len(res['refs'])} documents cached and quotable.")
    return 0


def cmd_evidence(args) -> int:
    ev = _svc().evidence_body(args.ref)
    if ev is None:
        print(f"No evidence with ref {args.ref!r}", file=sys.stderr)
        return 1
    print(f"{ev.ref}  [{ev.kind}]\n{ev.title}\n{ev.url}\n{'-' * 60}\n{ev.body}")
    return 0


def cmd_new_local(args) -> int:
    from .skeleton import blank
    svc = _svc()
    doc_id = args.name.lower().replace(" ", "-").replace("/", "-")[:60]
    svc.localdocs.create(doc_id, args.title or args.name, blank(args.title or args.name))
    view, _ = svc.read(f"local:{doc_id}")
    print(f"created  local:{doc_id}")
    print(f"sections {', '.join(view.section_names())}")
    print(f"revision {view.revision_id}")
    return 0


def cmd_local(args) -> int:
    docs = _svc().localdocs.list_docs()
    if not docs:
        print("No local documents. Create one:  draaiboek new-local \"15 sep Rogier\"")
    for d in docs:
        print(f"  local:{d['id']:<40} {d['rows']:>3} rows  rev {d['revision']}  {d['title']}")
    return 0


def cmd_preview(args) -> int:
    import subprocess
    from .preview import write_preview
    svc = _svc()
    view, _ = svc.read(_doc_id(args.doc_id))
    path = write_preview(view, svc.cfg.home, args.subtitle)
    print(path)
    if not args.no_open:
        subprocess.run(["open", str(path)], check=False)
    return 0


def cmd_template(args) -> int:
    print(json.dumps(_svc().make_template(_doc_id(args.source), args.name),
                     ensure_ascii=False, indent=2))
    return 0


def cmd_why(args) -> int:
    """Answer 'waar heb je deze informatie vandaan?' for every row in a doc."""
    svc = _svc()
    doc_id = _doc_id(args.doc_id)
    kinds = {"email": "Gmail", "clickup": "ClickUp", "xero": "Xero",
             "larissa": "Larissa zelf", "house_rule": "Vaste regel", "doc": "Stond al in de doc"}
    rows = 0
    for e in svc.ledger.entries(doc_id, 500):
        if e.get("event") != "applied":
            continue
        for edit in e.get("edits", []):
            src = edit.get("source")
            if not src:
                continue
            text = " · ".join(x for x in edit.get("values", []) if x) or edit.get("find", "")
            if args.filter and args.filter.lower() not in text.lower():
                continue
            rows += 1
            print(f"\n{text}")
            print(f"    bron : {kinds.get(src['kind'], src['kind'])}  ({src['ref']})")
            print(f"    zegt : \"{src['quote'][:150]}\"")
    if not rows:
        print("Nothing written to this document yet.")
    else:
        print(f"\n{rows} rows, every one traceable to a source.")
    return 0


def cmd_quote_gaps(args) -> int:
    gaps = _svc().quote_gaps(_doc_id(args.doc_id))
    if not gaps:
        print("Alles in het draaiboek is gedekt door de offerte.")
        return 0
    print("IN HET DRAAIBOEK, NIET IN DE OFFERTE\n"
          "Larissa: controleer of dit in de offerte moet.\n")
    for g in gaps:
        print(f"  [{g['section']}] {g['row']}")
        print(f"      afgesproken via {g['source']}")
    print(f"\n{len(gaps)} punten om na te kijken.")
    return 0


def cmd_log(args) -> int:
    for e in _svc().ledger.entries(_doc_id(args.doc_id) if args.doc_id else None, args.limit):
        bits = [e.get("ts", "")[:19], e.get("event", "?"), (e.get("doc_id") or "")[:12]]
        if e.get("reason"):
            bits.append(f"reason={e['reason']}")
        if e.get("note"):
            bits.append(repr(e["note"]))
        print("  ".join(bits))
    return 0


def cmd_protected(args) -> int:
    t = _svc().ledger.load_tombstones(_doc_id(args.doc_id))
    if not t:
        print("No rows deleted by hand on this document (yet).")
    for rec in t.values():
        print(f"{rec.get('removed_at','')[:10]}  [{rec.get('section')}]  "
              f"{' | '.join(rec.get('values', []))}")
    return 0


def cmd_rules(args) -> int:
    print(_svc().house_rules())
    return 0


def cmd_doctor(args) -> int:
    """Everything that must be true before this touches a real document."""
    cfg = load_config()
    ok = True

    def check(label: str, good: bool, detail: str = "") -> None:
        nonlocal ok
        ok &= good
        print(f"  [{'ok' if good else 'FAIL'}] {label}{'  ' + detail if detail else ''}")

    print("configuration")
    check("state dir", cfg.home.exists(), str(cfg.home))
    check("house rules", cfg.house_rules_path.exists(), str(cfg.house_rules_path))
    check("guards", cfg.guards_path.exists(), str(cfg.guards_path))
    check("template doc", bool(cfg.template_doc_id),
          cfg.template_doc_id or "set DRAAIBOEK_TEMPLATE_DOC_ID")
    check("sandbox doc", bool(cfg.sandbox_doc_id),
          cfg.sandbox_doc_id or "set DRAAIBOEK_SANDBOX_DOC_ID")

    print("rules")
    try:
        import re
        spec = Draaiboek(cfg).guard.spec
        for f in spec.get("forbidden", []):
            re.compile(f["pattern"])
        check("guards.yaml parses", True,
              f"{len(spec.get('forbidden', []))} patterns, "
              f"{len(spec.get('locked_sections', []))} locked sections")
    except Exception as e:  # noqa: BLE001
        check("guards.yaml parses", False, str(e))

    print("sources")
    svc = Draaiboek(cfg)
    for name, st in svc.sources.status().items():
        check(name, bool(st.get("configured")), st.get("error", "")[:90])

    print("google")
    g = svc.google
    try:
        g._credentials()
        check("credentials load", True, blob_client_id(cfg.client_secret))
    except AuthError as e:
        check("credentials load", False, str(e).splitlines()[0])
        print("\nNot ready -- fix the FAIL lines above.")
        return 1

    try:
        files = g.drive.files().list(
            pageSize=5, q="mimeType='application/vnd.google-apps.document'",
            fields="files(id)", supportsAllDrives=True,
            includeItemsFromAllDrives=True).execute().get("files", [])
        check("drive sees documents", bool(files), f"{len(files)} visible")
        if not files:
            print("       ^ the service account has no document access. Either share the"
                  "\n         draaiboeken folder with it, or grant domain-wide delegation"
                  "\n         for the Docs and Drive scopes (see below).")
    except Exception as e:  # noqa: BLE001
        check("drive sees documents", False, f"{type(e).__name__}: {str(e)[:80]}")

    if cfg.sandbox_doc_id:
        try:
            view, _ = svc.read(cfg.sandbox_doc_id)
            check("can read sandbox doc", True,
                  f"{len(view.data_rows())} rows, {len(view.sections)} sections")
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            check("can read sandbox doc", False,
                  "403 -- not shared with the service account"
                  if "permission" in msg.lower() else msg[:90])

    if not ok:
        print(f"""
To give the service account full access as Larissa, add these scopes in
Google Workspace admin (Security -> Access and data control -> API controls
-> Domain-wide delegation), on client ID {blob_client_id(cfg.client_secret)}:

    https://www.googleapis.com/auth/documents
    https://www.googleapis.com/auth/drive
    https://www.googleapis.com/auth/gmail.readonly   (already granted)

Delegation is detected automatically -- nothing here needs reconfiguring
once the scopes are added.""")

    print("\n" + ("All good." if ok else "Not ready -- fix the FAIL lines above."))
    return 0 if ok else 1


def cmd_ui(args) -> int:
    from .ui import serve
    return serve(port=args.port, host=args.host, open_browser=not args.no_open)


def cmd_propose(args) -> int:
    """What Hermes does via MCP, from a JSON file -- for testing the review flow."""
    payload: dict[str, Any] = json.loads(
        sys.stdin.read() if args.file == "-" else open(args.file).read()
    )
    payload.setdefault("doc_id", _doc_id(args.doc_id))
    svc = _svc()
    if not payload.get("expected_revision"):
        view, _ = svc.read(payload["doc_id"])
        payload["expected_revision"] = view.revision_id
    context = payload.pop("context_refs", None)
    title = payload.pop("title", "") or args.title
    try:
        out = svc.propose(EditRequest(**payload), title=title, context_refs=context)
    except (GuardRefusal, UnsupportedClaim, RevisionConflict, ValueError) as e:
        print(str(e), file=sys.stderr)
        return 2
    except (WriteError, AuthError) as e:
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        return 4
    print(f"proposal {out['proposal_id']}  ({out['edits']} edits, expires {out['expires_at'][:10]})")
    print(out["workspace_url"])
    return 0


def cmd_proposals(args) -> int:
    recs = _svc().proposals.all(args.limit)
    if not recs:
        print("No proposals yet.")
    for r in recs:
        print(f"  {r['id']}  {r['status']:<10} {r.get('created_at', '')[:16]}  "
              f"{len(r.get('edits', [])):>2} edits  {r.get('title') or r.get('doc_id')}")
        if r.get("comment"):
            print(f"               \"{r['comment'][:100]}\"")
    return 0


def cmd_serve(args) -> int:
    from .server import main as serve
    serve()
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser("draaiboek", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("auth", help="authorise with Google").set_defaults(fn=cmd_auth)

    r = sub.add_parser("read", help="show a document's rows and ids")
    r.add_argument("doc_id")
    r.add_argument("--json", action="store_true")
    r.set_defaults(fn=cmd_read)

    e = sub.add_parser("edit", help="apply an edit batch from a JSON file")
    e.add_argument("doc_id")
    e.add_argument("--file", "-f", default="-", help="JSON file, or - for stdin")
    e.add_argument("--dry-run", action="store_true", help="guard-check only, write nothing")
    e.set_defaults(fn=cmd_edit)

    c = sub.add_parser("create", help="new doc from the template")
    c.add_argument("title")
    c.add_argument("--template", default=None)
    c.set_defaults(fn=cmd_create)

    nl = sub.add_parser("new-local", help="create a blank draaiboek on disk (no Google needed)")
    nl.add_argument("name")
    nl.add_argument("--title", default="")
    nl.set_defaults(fn=cmd_new_local)

    sub.add_parser("local", help="list local documents").set_defaults(fn=cmd_local)

    pv = sub.add_parser("preview", help="render a document as HTML and open it")
    pv.add_argument("doc_id")
    pv.add_argument("--subtitle", default="")
    pv.add_argument("--no-open", action="store_true")
    pv.set_defaults(fn=cmd_preview)

    tp = sub.add_parser("template-from",
                        help="build the master template from a real draaiboek (copies it first)")
    tp.add_argument("source", help="doc id or URL of a draaiboek whose styling is correct")
    tp.add_argument("--name", default="Draaiboek — TEMPLATE")
    tp.set_defaults(fn=cmd_template)

    sub.add_parser("sources", help="which systems are connected").set_defaults(fn=cmd_sources)

    g = sub.add_parser("gather", help="search Gmail/ClickUp/Xero/Missive for an event")
    g.add_argument("query")
    g.add_argument("--kinds", default="", help="comma list: gmail,clickup,xero,missive")
    g.add_argument("--limit", type=int, default=8)
    g.set_defaults(fn=cmd_gather)

    ev = sub.add_parser("evidence", help="full text of one gathered document")
    ev.add_argument("ref")
    ev.set_defaults(fn=cmd_evidence)

    wy = sub.add_parser("why", help="show the source behind every row in a document")
    wy.add_argument("doc_id")
    wy.add_argument("--filter", default="", help="only rows containing this text")
    wy.set_defaults(fn=cmd_why)

    qg = sub.add_parser("quote-gaps",
                        help="what is in the draaiboek but not in the Xero quote")
    qg.add_argument("doc_id")
    qg.set_defaults(fn=cmd_quote_gaps)

    lg = sub.add_parser("log", help="audit trail")
    lg.add_argument("doc_id", nargs="?")
    lg.add_argument("--limit", type=int, default=30)
    lg.set_defaults(fn=cmd_log)

    pr = sub.add_parser("protected", help="rows Larissa deleted by hand")
    pr.add_argument("doc_id")
    pr.set_defaults(fn=cmd_protected)

    sub.add_parser("rules", help="print the house rules").set_defaults(fn=cmd_rules)
    ui = sub.add_parser("ui", help="Larissa's workspace: fetch, review, edit, deploy")
    ui.add_argument("--port", type=int, default=8765)
    ui.add_argument("--host", default="127.0.0.1",
                    help="0.0.0.0 to serve other machines (requires DRAAIBOEK_UI_KEY)")
    ui.add_argument("--no-open", action="store_true")
    ui.set_defaults(fn=cmd_ui)

    po = sub.add_parser("propose", help="propose an edit batch for Larissa (as Hermes would)")
    po.add_argument("doc_id")
    po.add_argument("--file", "-f", default="-", help="JSON file, or - for stdin")
    po.add_argument("--title", default="")
    po.set_defaults(fn=cmd_propose)

    ps = sub.add_parser("proposals", help="Hermes' proposals and what Larissa decided")
    ps.add_argument("--limit", type=int, default=20)
    ps.set_defaults(fn=cmd_proposals)

    sub.add_parser("doctor", help="pre-flight checks").set_defaults(fn=cmd_doctor)
    sub.add_parser("serve", help="run the MCP server on stdio").set_defaults(fn=cmd_serve)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
