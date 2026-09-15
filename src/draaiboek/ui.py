"""Larissa's workspace: fetch, review, answer, edit, deploy.

One page. She picks an event (or opens the link Hermes sent on Telegram), and
everything that event generated is fetched from Gmail, Missive, ClickUp and
Xero -- threads, internal comments, attachments, quote lines. Next to it: the
house rules, the open questions to answer, and the draaiboek itself, editable,
with Hermes' proposed changes overlaid and the reason behind each one. Deploy
writes the result to the Google Doc through Draaiboek.deploy -> apply, with
the same revision lock, house rules and quote checks as every other write.

Access: always from this machine. From anywhere else only with
DRAAIBOEK_UI_KEY set, after a one-time login that leaves a session cookie.
The page reads all mail, so there is no unauthenticated remote mode.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from .gdocs import AuthError
from .service import Draaiboek, GuardRefusal, RevisionConflict, UnsupportedClaim
from .sources.base import AttachmentFile
from .writer import WriteError

STATIC = Path(__file__).resolve().parent / "static"
MAX_BODY = 256_000
COOKIE = "draaiboek_session"

KIND_LABEL = {"email": "Gmail", "clickup": "ClickUp", "xero": "Xero", "missive": "Missive",
              "larissa": "Larissa", "house_rule": "House rule", "doc": "Already in the draaiboek"}


class _Cache:
    """Gathering hits four APIs; repeated clicks should not re-hit them."""

    def __init__(self) -> None:
        self._d: dict[str, object] = {}
        self._lock = threading.Lock()

    def get(self, key: str):
        with self._lock:
            return self._d.get(key)

    def put(self, key: str, value):
        with self._lock:
            self._d[key] = value
        return value

    def clear(self) -> None:
        with self._lock:
            self._d.clear()


def _doc_id(s: str) -> str:
    """Accept a bare id or a pasted Google Docs URL."""
    s = (s or "").strip()
    if "/d/" in s:
        return s.split("/d/", 1)[1].split("/", 1)[0]
    return s


def _doc_url(doc_id: str) -> str | None:
    return None if doc_id.startswith("local:") else \
        f"https://docs.google.com/document/d/{doc_id}/edit"


class Handler(BaseHTTPRequestHandler):
    svc: Draaiboek
    cache: _Cache

    # -- plumbing ----------------------------------------------------------
    def log_message(self, fmt, *args):  # quieter console
        pass

    def _send(self, code: int, body: bytes, ctype: str, headers: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, code: int = 200) -> None:
        self._send(code, json.dumps(payload, ensure_ascii=False, default=str).encode(),
                   "application/json; charset=utf-8")

    def _html(self, text: str, code: int = 200, headers: dict | None = None) -> None:
        self._send(code, text.encode(), "text/html; charset=utf-8", headers)

    # -- access ------------------------------------------------------------
    def _is_local(self) -> bool:
        """Loopback client *and* a loopback Host header, so a DNS-rebinding page
        cannot read the workspace through someone's browser."""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        return (self.client_address[0] in ("127.0.0.1", "::1")
                and host in ("127.0.0.1", "localhost", "::1"))

    def _session_value(self) -> str:
        key = (self.svc.cfg.ui_key or "").encode()
        return hmac.new(key, b"draaiboek-workspace-session", hashlib.sha256).hexdigest()

    def _authorised(self) -> bool:
        if self._is_local():
            return True
        if not self.svc.cfg.ui_key:
            return False
        for part in (self.headers.get("Cookie") or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE and hmac.compare_digest(value, self._session_value()):
                return True
        return False

    def _login_page(self, next_path: str, error: str = "") -> None:
        self._html((STATIC / "login.html").read_text()
                   .replace("{{next}}", html.escape(next_path, quote=True))
                   .replace("{{error}}", html.escape(error)), 401 if error else 200)

    def _login(self, body: bytes) -> None:
        form = {k: v[0] for k, v in parse_qs(body.decode(errors="replace")).items()}
        nxt = form.get("next", "/")
        if not nxt.startswith("/") or nxt.startswith("//"):
            nxt = "/"
        key = self.svc.cfg.ui_key or ""
        if not key or not hmac.compare_digest(form.get("key", "").encode(), key.encode()):
            time.sleep(1.0)
            return self._login_page(nxt, "That key is not right.")
        secure = "; Secure" if self.svc.cfg.public_url.startswith("https://") else ""
        self._send(303, b"", "text/plain", {
            "Location": nxt,
            "Set-Cookie": f"{COOKIE}={self._session_value()}; HttpOnly; SameSite=Lax; "
                          f"Path=/; Max-Age={60 * 60 * 24 * 30}{secure}"})

    # -- routing -----------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if url.path == "/login":
                return self._login_page(q.get("next", "/"))
            if not self._authorised():
                if url.path.startswith("/api/"):
                    return self._json({"error": "Not signed in."}, 401)
                return self._send(303, b"", "text/plain",
                                  {"Location": "/login?next=" + quote(self.path, safe="")})
            return self._get(url.path, q)
        except Exception as e:  # noqa: BLE001
            return self._json({"error": f"{type(e).__name__}: {e}",
                               "trace": traceback.format_exc()[-1500:] if self._is_local() else ""},
                              500)

    def do_POST(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._json({"error": "Request too large."}, 413)
            raw = self.rfile.read(length) if length else b""
            if url.path == "/login":
                return self._login(raw)
            if not self._authorised():
                return self._json({"error": "Not signed in."}, 401)
            # A JSON body plus a custom header cannot be sent cross-site
            # without a CORS preflight, which this server never grants.
            if (self.headers.get("X-Draaiboek") != "1"
                    or "application/json" not in (self.headers.get("Content-Type") or "")):
                return self._json({"error": "bad request"}, 400)
            return self._post(url.path, json.loads(raw or b"{}"))
        except Exception as e:  # noqa: BLE001
            return self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def _get(self, path: str, q: dict) -> None:
        if path in ("/", "/index.html"):
            return self._html((STATIC / "ui.html").read_text())
        routes = {
            "/api/sources": lambda: self._sources(),
            "/api/events": lambda: self._events(q.get("q", ""), int(q.get("limit", "40"))),
            "/api/gather": lambda: self._gather(q.get("q", ""), int(q.get("limit", "10")),
                                                q.get("fresh") == "1", q.get("kinds", "")),
            "/api/evidence": lambda: self._evidence(q.get("ref", "")),
            "/api/docs": lambda: {"docs": self.svc.localdocs.list_docs()},
            "/api/rules": lambda: self._rules(),
            "/api/doc": lambda: self._doc(_doc_id(q.get("doc", ""))),
            "/api/proposal": lambda: self._proposal_link(q.get("id", "")),
            "/api/proposals": lambda: {"proposals": [
                {k: r.get(k) for k in ("id", "status", "title", "doc_id", "created_at",
                                       "decided_at", "comment")}
                | {"edits": len(r.get("edits", []))} for r in self.svc.proposals.all(100)]},
        }
        if path in routes:
            return self._json(routes[path]())
        if path == "/api/attachment":
            return self._attachment(self.svc.sources.attachment(q.get("ref", ""),
                                                                int(q.get("i", "-1"))))
        if path == "/api/attachment/text":
            return self._json(self.svc.read_attachment(q.get("ref", ""),
                                                       int(q.get("i", "0"))))
        if path == "/api/preview":
            from .preview import render_html
            try:
                view, _ = self.svc.read(_doc_id(q.get("doc", "")), record=False)
            except Exception as e:  # noqa: BLE001
                return self._json({"error": self._doc_error(e)}, 502)
            return self._html(render_html(view))
        return self._json({"error": "not found"}, 404)

    def _post(self, path: str, body: dict) -> None:
        if path == "/api/rules":
            try:
                return self._json(self.svc.save_house_rules(str(body.get("markdown", ""))))
            except ValueError as e:
                return self._json({"error": str(e)}, 422)
        if path == "/api/proposal/reject":
            try:
                rec = self.svc.reject_proposal(str(body.get("proposal_id", "")),
                                               str(body.get("comment", "")))
            except ValueError as e:
                return self._json({"error": str(e)}, 409)
            return self._json({"ok": True, "status": rec["status"]})
        if path not in ("/api/check", "/api/deploy"):
            return self._json({"error": "not found"}, 404)
        args = dict(doc_id=_doc_id(str(body.get("doc_id", ""))),
                    expected_revision=str(body.get("expected_revision", "")),
                    proposal_id=body.get("proposal_id") or None,
                    include=[int(i) for i in body.get("include") or [] if str(i).isdigit()],
                    own=list(body.get("own") or []), answers=list(body.get("answers") or []))
        if path == "/api/check":
            return self._json(self.svc.check(**args))
        try:
            return self._json({"ok": True, **self.svc.deploy(**args)})
        except RevisionConflict as e:
            return self._json({"error": "The draaiboek was changed in Google Docs after you "
                                        "opened it. Reload it: your changes stay in the list "
                                        "and are checked again.", "kind": "conflict",
                               "current_revision": e.actual}, 409)
        except (GuardRefusal, UnsupportedClaim) as e:
            return self._json({"error": str(e), "kind": "refused"}, 422)
        except (WriteError, ValueError) as e:
            return self._json({"error": str(e), "kind": "invalid"}, 422)
        except AuthError as e:
            return self._json({"error": self._doc_error(e), "kind": "access"}, 502)

    # -- data --------------------------------------------------------------
    def _doc_error(self, e: Exception) -> str:
        msg = str(e)
        low = msg.lower()
        if "403" in msg or "404" in msg or "permission" in low or "not found" in low:
            email = ""
            try:
                email = json.loads(self.svc.cfg.client_secret.read_text()).get("client_email", "")
            except Exception:  # noqa: BLE001
                pass
            return (f"Google gives this system no access to that document. Share the document "
                    f"(or the draaiboeken folder) as Editor with {email or 'the service account'}.")
        return f"{type(e).__name__}: {msg[:400]}"

    def _attachment(self, f: AttachmentFile | None) -> None:
        if f is None:
            return self._json({"error": "Attachment not found."}, 404)
        mime = (f.mime or "application/octet-stream").split(";")[0].strip().lower()
        viewable = mime == "application/pdf" or (mime.startswith("image/") and "svg" not in mime)
        headers = {"Content-Disposition": f"{'inline' if viewable else 'attachment'}; "
                                          f"filename*=UTF-8''{quote(f.filename, safe='')}"}
        if not viewable:
            # Anything that could run script (html, svg, ...) is a download,
            # sandboxed in case a browser renders it anyway.
            headers["Content-Security-Policy"] = "sandbox"
        self._send(200, f.data, mime if viewable else "application/octet-stream", headers)

    def _sources(self) -> dict:
        status = self.svc.sources.status()
        try:
            files = self.svc.google.drive.files().list(
                pageSize=1, fields="files(id)", supportsAllDrives=True,
                includeItemsFromAllDrives=True).execute().get("files", [])
            # An empty list is not success: the credential works but nothing is
            # shared with it, which is exactly the state that stranded Larissa.
            docs = ({"configured": True} if files else
                    {"configured": False,
                     "error": "Connected, but no document is shared with this system yet."})
        except Exception as e:  # noqa: BLE001
            docs = {"configured": False, "error": str(e)[:160]}
        status["google docs"] = docs
        return {"sources": status, "evidence": self.svc.evidence.stats()}

    def _events(self, query: str, limit: int) -> dict:
        key = f"events:{query}:{limit}"
        hit = self.cache.get(key)
        if hit is not None:
            return hit
        cu = self.svc.sources.clickup
        if not cu.available():
            return {"events": [], "error": "ClickUp is niet verbonden."}
        evs = (cu.search(query, limit=limit, hydrate=False) if query.strip()
               else cu.upcoming(limit=limit))
        out = [{"ref": e.ref, "title": e.title, "url": e.url, "kind": "clickup",
                "status": e.meta.get("status", ""), "doc_id": e.meta.get("draaiboek_doc_id"),
                "quote_refs": e.meta.get("quote_refs") or [],
                "cancelled": bool(e.meta.get("CANCELLED")), "due": e.meta.get("due_date")}
               for e in evs]
        return self.cache.put(key, {"events": out})

    @staticmethod
    def _item(e: dict) -> dict:
        meta = e.get("meta") or {}
        return {"ref": e["ref"], "kind": e["kind"], "label": KIND_LABEL.get(e["kind"], e["kind"]),
                "title": e.get("title", ""), "when": e.get("when", ""), "url": e.get("url", ""),
                "attachments": sum(1 for a in meta.get("attachments") or [] if not a.get("inline")),
                "comments": len(meta.get("comments") or []), "labels": meta.get("labels") or [],
                "messages": meta.get("messages"), "thread": meta.get("thread"),
                "doc_id": meta.get("draaiboek_doc_id"), "cancelled": bool(meta.get("CANCELLED")),
                "quote_refs": meta.get("quote_refs") or []}

    def _gather(self, query: str, limit: int, fresh: bool, kinds: str = "") -> dict:
        key = f"gather:{query}:{limit}:{kinds}"
        if fresh:
            self.cache.clear()
        hit = self.cache.get(key)
        if hit is not None:
            return hit
        res = self.svc.gather(query, [k for k in kinds.split(",") if k] or None, limit)
        items = [self._item(e) for e in res["evidence"]]
        return self.cache.put(key, {"query": query, "items": items,
                                    "gaps": self._gaps(items, res["unavailable"])})

    @staticmethod
    def _gaps(items: list, unavailable: dict) -> list[dict]:
        """Only what actually went wrong. "Nothing found in Xero" for a name
        search is normal and was just noise."""
        names = {"gmail": "Gmail", "missive": "Missive", "clickup": "ClickUp", "xero": "Xero"}
        return [{"level": "error", "text": f"{names.get(k, k)} could not be searched right now."}
                for k, v in unavailable.items() if v != "not configured"]

    def _evidence(self, ref: str) -> dict:
        ev = self.svc.evidence.get(ref)
        if ev is None or (ev.kind in ("email", "clickup", "missive")
                          and ("attachments" not in ev.meta
                               or (ev.kind != "email" and "comments" not in ev.meta))):
            # Cached before attachments/comments were recorded, or from a list
            # endpoint that omits them: re-read the source once.
            try:
                fresh = self.svc.sources.fetch(ref)
            except Exception:  # noqa: BLE001
                fresh = None
            if fresh is not None:
                self.svc.evidence.put(fresh)
                ev = fresh
        if ev is None:
            return {"error": f"Nothing found for {ref!r}."}
        d = ev.to_dict()
        d["label"] = KIND_LABEL.get(ev.kind, ev.kind)
        from .extract import readable
        d["attachments"] = [
            dict(a, readable=readable(a.get("mime", ""), a.get("filename", "")))
            for a in (ev.meta.get("attachments") or [])
        ]
        d["comments"] = ev.meta.get("comments") or []
        d["labels"] = ev.meta.get("labels") or []
        return d

    def _rules(self) -> dict:
        spec = self.svc.guard.spec
        return {"markdown": self.svc.house_rules(), "enforced": {
            "locked_sections": spec.get("locked_sections", []),
            "forbidden": [{"name": f.get("name"), "message": (f.get("message") or "").strip()}
                          for f in spec.get("forbidden", [])],
            "lexicon": [{"wrong": x.get("wrong"), "right": x.get("right"),
                         "message": (x.get("message") or "").strip()}
                        for x in spec.get("lexicon", [])],
        }}

    def _doc(self, doc_id: str) -> dict:
        from .preview import _bg

        if not doc_id:
            return {"error": "No draaiboek selected."}
        try:
            view, _ = self.svc.read(doc_id, record=False)
        except Exception as e:  # noqa: BLE001
            return {"error": self._doc_error(e), "doc_id": doc_id}

        tables = [{"index": t.index, "columns": t.columns, "headers": t.headers,
                   "rows": [{"row_id": r.row_id, "kind": r.kind, "section": r.section,
                             "values": r.values, "bg": _bg(r) if r.kind == "data" else ""}
                            for r in t.rows]} for t in view.tables]

        questions = []
        section = view.section_by_name("Open Punten")
        if section is not None:
            table = view.tables[section.table]
            low = [h.strip().lower() for h in table.headers]
            col = next((low.index(h) for h in ("antwoord", "opmerkingen") if h in low),
                       max(table.columns - 1, 0))
            for rid in section.row_ids:
                row = view.row(rid)
                if row is not None and row.joined().strip():
                    questions.append({"row_id": rid, "values": row.values,
                                      "headers": table.headers, "answer_col": col})

        rec = self.svc.proposals.open_for(doc_id)
        proposal = None
        if rec is not None:
            by_ref = {}
            for ref in rec.get("refs", []):
                ev = self.svc.evidence.get(ref)
                if ev is not None:
                    by_ref[ref] = ev
            edits = []
            warnings: dict[int, list[str]] = {}
            for w in rec.get("warnings") or []:
                warnings.setdefault(w.get("edit_index", -1), []).append(w.get("message", ""))
            for i, d in enumerate(rec.get("display", [])):
                e = dict(d, index=i, warnings=warnings.get(i, []))
                e["colour"] = self._colour(d.get("category"))
                src = d.get("source")
                if src:
                    ev = by_ref.get(src["ref"])
                    e["source_label"] = KIND_LABEL.get(src["kind"], src["kind"])
                    e["source_title"] = ev.title if ev else ""
                    e["source_openable"] = ev is not None
                    e["quote_found"] = ev.contains(src["quote"]) if ev else None
                edits.append(e)
            proposal = {"id": rec["id"], "title": rec.get("title"), "created_at": rec.get("created_at"),
                        "note": rec.get("note"), "stale": rec["expected_revision"] != view.revision_id,
                        "edits": edits,
                        "sources": [self._item(ev.to_dict()) for ev in by_ref.values()]}

        from . import calllist
        calls = calllist.build(view, self.svc.reminders_for(view))

        return {"doc_id": doc_id, "title": view.title, "revision_id": view.revision_id,
                "calls": calls,
                "calls_open": sum(len(t["tasks"]) for t in calls),
                "url": _doc_url(doc_id), "tables": tables, "questions": questions,
                "proposal": proposal, "warnings": view.warnings,
                "protected_rows": len(self.svc.ledger.load_tombstones(doc_id)),
                "answers": self.svc.answers_for(doc_id)[-30:]}

    @staticmethod
    def _colour(category: str | None) -> str:
        from .ops import Category
        from .writer import CATEGORY_HEX
        try:
            return "#" + CATEGORY_HEX[Category(category or "algemeen")]
        except ValueError:
            return "#ffffff"

    def _proposal_link(self, pid: str) -> dict:
        rec = self.svc.proposals.get(pid)
        if rec is None:
            return {"error": "That proposal does not exist (any more)."}
        return {"id": rec["id"], "doc_id": rec["doc_id"], "title": rec.get("title"),
                "status": rec["status"], "comment": rec.get("comment")}


def serve(port: int = 8765, open_browser: bool = True, host: str = "127.0.0.1") -> int:
    svc = Draaiboek()
    if host not in ("127.0.0.1", "localhost", "::1") and not svc.cfg.ui_key:
        print("Refusing to serve other machines without a key: the workspace reads all mail.\n"
              "Set DRAAIBOEK_UI_KEY (a long random string) in ~/.draaiboek/env and try again.")
        return 2
    Handler.svc = svc
    Handler.cache = _Cache()
    server = ThreadingHTTPServer((host, port), Handler)
    url = f"http://127.0.0.1:{port}"
    print(f"Draaiboek workspace  ->  {url}")
    if host not in ("127.0.0.1", "localhost", "::1"):
        print(f"Also on {host}:{port}, sign-in required. Hermes links to {svc.cfg.public_url}")
    print("Ctrl-C to stop.")
    if open_browser:
        import subprocess
        subprocess.run(["open", url], check=False)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0
