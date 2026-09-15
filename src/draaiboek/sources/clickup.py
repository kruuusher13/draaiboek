"""ClickUp -- the spine of an event.

Event tasks are named "YYYY/MM/DD · Client Name" in the LU | All Events list.
The description holds what matters: guest count, an indicative schedule, the
Xero quote number, the link to the draaiboek, and usually the whole email
thread pasted in. It is read first and never truncated.

Financial custom fields (Est. profit, Downpayment, invoice links) are stripped
before the evidence leaves this module. Money must never enter the document,
so the cheapest guarantee is that it never enters the agent's context.
"""

from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import requests

from .base import AttachmentFile, Evidence, SourceError, attachment_meta, download
from .dates import day_bounds_ms, find_date, mentions

API = "https://api.clickup.com/api/v2"

MONEY_FIELD = re.compile(
    r"(?i)profit|downpayment|invoice|factuur|price|prijs|bedrag|omzet|cost|kosten|budget|"
    r"payment|betaling|rabo|bank|incasso|xero|"
    r"revenue|marge|deposit|value"
)
DOC_LINK = re.compile(r"https://docs\.google\.com/document/d/([A-Za-z0-9_-]{20,})")
QUOTE_REF = re.compile(r"\bQU-\d{3,}(?:\.\d+)?\b")
CANCELLED = re.compile(r"(?i)cancel|lost|geannuleerd|afgelast")
ATTACHMENT_HOSTS = ("clickup-attachments.com", "clickup.com")


def _ms(ts) -> str:
    from datetime import datetime, timezone
    try:
        return datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError, OSError):
        return str(ts or "")


def _terms(query: str) -> list[str]:
    stop = {"draaiboek", "voor", "van", "het", "de", "een", "maken", "event",
            "evenement", "kan", "je", "maak", "the", "for"}
    return [t for t in re.split(r"[^\w]+", query.lower())
            if len(t) > 2 and t not in stop and not t.isdigit()]


def _fuzzy(terms: list[str], text: str) -> bool:
    """Prefix matching in both directions, so "Kalma" finds "Kalman" and
    "Bogerd" finds "van den Bogerd"."""
    if not terms:
        return True
    tokens = set(re.split(r"[^\w]+", text.lower()))
    for term in terms:
        if not any(tok.startswith(term) or (len(tok) > 3 and term.startswith(tok))
                   for tok in tokens if tok):
            return False
    return True


class ClickUp:
    name = "clickup"

    def __init__(self, token: str | None = None, team_id: str | None = None):
        self.token = token or os.environ.get("CLICKUP_TOKEN", "")
        self.team_id = team_id or os.environ.get("CLICKUP_TEAM_ID", "")

    def available(self) -> bool:
        return bool(self.token)

    def _get(self, path: str, **params):
        r = requests.get(f"{API}{path}", headers={"Authorization": self.token},
                         params=params, timeout=40)
        if r.status_code >= 400:
            raise SourceError(f"ClickUp {path} -> {r.status_code}: {r.text[:200]}")
        return r.json()

    def _team(self) -> str:
        if not self.team_id:
            teams = self._get("/team").get("teams", [])
            if not teams:
                raise SourceError("No ClickUp teams visible to this token")
            self.team_id = teams[0]["id"]
        return self.team_id

    # -- conversion --------------------------------------------------------
    @staticmethod
    def _attachments(task: dict) -> list[dict]:
        return [a for a in task.get("attachments") or []
                if not a.get("deleted") and not a.get("is_folder") and a.get("url")]

    @classmethod
    def _to_evidence(cls, task: dict) -> Evidence:
        desc = (task.get("description") or task.get("text_content") or "").strip()
        status = (task.get("status") or {}).get("status", "")
        fields = "\n".join(
            f"{f['name']}: {f.get('value')}"
            for f in task.get("custom_fields", [])
            if f.get("value") not in (None, "", [], {}) and not MONEY_FIELD.search(f.get("name", ""))
        )
        comments = [{"author": (c.get("user") or {}).get("username", ""),
                     "when": _ms(c.get("date")), "text": (c.get("comment_text") or "").strip()}
                    for c in task.get("_comments") or []]
        body = "\n\n".join(x for x in [
            f"Task: {task.get('name', '')}",
            f"List: {(task.get('list') or {}).get('name', '')}",
            f"Status: {status}",
            desc,
            (f"Fields:\n{fields}" if fields else ""),
            ("Comments:\n" + "\n".join(f"- {c['author']} ({c['when']}): {c['text']}"
                                        for c in comments) if comments else ""),
        ] if x)

        docs = DOC_LINK.findall(desc)
        quotes = QUOTE_REF.findall(desc)
        meta = {
            "status": status,
            "list": (task.get("list") or {}).get("name", ""),
            "draaiboek_doc_id": docs[0] if docs else None,
            "quote_refs": sorted(set(quotes)),
            "due_date": task.get("due_date"),
        }
        if "_comments" in task:
            meta["comments"] = comments
        if "attachments" in task:
            # Only the single-task endpoint returns attachments; list results
            # leave the key out, which is not the same as "none".
            meta["attachments"] = [
                attachment_meta(a.get("title", ""), a.get("mimetype", ""), a.get("size"))
                for a in cls._attachments(task)
            ]
        if CANCELLED.search(status):
            meta["CANCELLED"] = (
                "This event is cancelled. Larissa's rule: do nothing at all -- no doc "
                "updates, no questions, no mentions."
            )
        return Evidence(
            kind="clickup", ref=f"clickup:{task['id']}", title=task.get("name", ""),
            body=body, when=str(task.get("due_date") or task.get("date_updated", "")),
            url=task.get("url", ""), meta=meta,
        )

    # -- search ------------------------------------------------------------
    def _by_date(self, d: date, limit: int) -> list[dict]:
        lo, hi = day_bounds_ms(d)
        out, seen = [], set()
        for page in range(3):
            data = self._get(f"/team/{self._team()}/task", page=page,
                             include_closed="true", subtasks="true",
                             due_date_gt=lo, due_date_lt=hi)
            tasks = data.get("tasks", [])
            for t in tasks:
                if t["id"] not in seen:
                    seen.add(t["id"])
                    out.append(t)
            if data.get("last_page") or not tasks or len(out) >= limit * 4:
                break
        return out

    def _by_text(self, terms: list[str], limit: int, max_pages: int = 25,
                 when: date | None = None) -> list[dict]:
        if not terms and when is None:
            return []
        out = []
        for page in range(max_pages):
            data = self._get(f"/team/{self._team()}/task", page=page,
                             include_closed="true", subtasks="true")
            tasks = data.get("tasks", [])
            if not tasks:
                break
            for t in tasks:
                name = t.get("name", "")
                if (when is None or mentions(name, when)) and _fuzzy(terms, name):
                    out.append(t)
                    if len(out) >= limit:
                        return out
            if data.get("last_page"):
                break
        return out

    @staticmethod
    def _rank(tasks: list[dict], terms: list[str]) -> list[dict]:
        def score(t: dict) -> tuple:
            name = t.get("name", "")
            return (
                0 if "All Events" in (t.get("list") or {}).get("name", "") else 1,
                0 if _fuzzy(terms, name) else 1,
                -len(t.get("description") or ""),
            )
        return sorted(tasks, key=score)

    def upcoming(self, days: int = 60, limit: int = 40) -> list[Evidence]:
        """Events due from today, soonest first -- the list Larissa starts from."""
        import time
        now_ms = int(time.time() * 1000) - 12 * 3600 * 1000  # include today
        out, seen = [], set()
        for page in range(4):
            data = self._get(f"/team/{self._team()}/task", page=page, subtasks="false",
                             include_closed="true", order_by="due_date", reverse="false",
                             due_date_gt=now_ms, due_date_lt=now_ms + days * 86_400_000)
            for t in data.get("tasks", []):
                if t["id"] not in seen and "All Events" in (t.get("list") or {}).get("name", ""):
                    seen.add(t["id"])
                    out.append(t)
            if data.get("last_page") or not data.get("tasks") or len(out) >= limit:
                break
        out.sort(key=lambda t: int(t.get("due_date") or 0))
        return [self._to_evidence(t) for t in out[:limit]]

    def _hydrate(self, tasks: list[dict]) -> list[dict]:
        """Re-read each task on its own endpoint, which is the only one that
        includes attachments. A task that fails to load keeps its list data."""
        def one(t: dict) -> dict:
            try:
                return self._full(t["id"])
            except SourceError:
                return t
        with ThreadPoolExecutor(max_workers=5) as pool:
            return list(pool.map(one, tasks))

    def search(self, query: str, limit: int = 10, hydrate: bool = True) -> list[Evidence]:
        d, rest = find_date(query)
        terms = _terms(rest)
        tasks: list[dict] = []
        if d:
            tasks = self._by_date(d, limit)
            if not tasks:
                # Due date not set, or set to a different day: the name still
                # carries the date ("2026/09/26 · Lisa-Lynde & Ivan Leeuwin").
                tasks = self._by_text(terms, limit, when=d)
            if terms:
                narrowed = [t for t in tasks if _fuzzy(terms, t.get("name", ""))]
                tasks = narrowed or tasks
        if not tasks and terms:
            tasks = self._by_text(terms, limit)
        ranked = self._rank(tasks, terms)[:limit]
        if hydrate:
            ranked = self._hydrate(ranked)
        return [self._to_evidence(t) for t in ranked]

    def _full(self, tid: str) -> dict:
        task = self._get(f"/task/{tid}", include_subtasks="true")
        try:
            task["_comments"] = self._get(f"/task/{tid}/comment").get("comments", [])
        except SourceError:
            pass
        return task

    def fetch(self, ref: str) -> Evidence | None:
        tid = ref.split(":", 1)[1] if ":" in ref else ref
        try:
            return self._to_evidence(self._full(tid))
        except SourceError:
            return None

    def attachment(self, ref: str, index: int) -> AttachmentFile | None:
        tid = ref.split(":", 1)[1] if ":" in ref else ref
        files = self._attachments(self._get(f"/task/{tid}"))
        if not 0 <= index < len(files):
            return None
        a = files[index]
        return AttachmentFile(a.get("title", "bestand"), a.get("mimetype", ""),
                              download(a["url"], ATTACHMENT_HOSTS))
