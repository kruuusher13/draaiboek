"""Missive. Its search is sparse -- supplier names often return nothing even
when the mail exists -- so Gmail is the primary email source and this is the
fallback for conversation context."""

from __future__ import annotations

import os

import requests

from concurrent.futures import ThreadPoolExecutor

from .base import (AttachmentFile, Evidence, SourceError, attachment_meta, download,
                   html_to_text)

API = "https://public.missiveapp.com/v1"
ATTACHMENT_HOSTS = ("missiveapp.com",)


def _files(messages: list[dict]) -> list[dict]:
    return [a for m in messages for a in (m.get("attachments") or []) if a.get("url")]


def _mime(a: dict) -> str:
    if a.get("media_type") and a.get("sub_type"):
        return f"{a['media_type']}/{a['sub_type']}"
    return a.get("media_type") or ""


class Missive:
    name = "missive"

    def __init__(self, token: str | None = None):
        self.token = token or os.environ.get("MISSIVE_TOKEN", "")

    def available(self) -> bool:
        return bool(self.token)

    def _get(self, path: str, **params):
        r = requests.get(f"{API}{path}",
                         headers={"Authorization": f"Bearer {self.token}"},
                         params=params, timeout=30)
        if r.status_code >= 400:
            raise SourceError(f"Missive {path} -> {r.status_code}: {r.text[:200]}")
        return r.json()

    def search(self, query: str, limit: int = 10, pages: int = 6) -> list[Evidence]:
        """Missive's public API has no full-text search -- only chronological
        listing -- so this walks recent conversations and matches the subject
        and the shared labels. Leeuwenbergh labels conversations with the event
        name ("2026/09/21 · Rabobank lunch"), which makes labels the best hit.

        Treat an empty result as "not found by Missive", never as "no such
        email exists".
        """
        from .clickup import _fuzzy, _terms
        from .dates import find_date, mentions
        d, rest = find_date(query)
        terms = _terms(rest)
        if not terms and d is None:
            return []
        matched: list[dict] = []
        until: int | None = None
        for _ in range(pages):
            params = {"all": "true", "limit": 50}
            if until:
                params["until"] = until
            convs = self._get("/conversations", **params).get("conversations", [])
            if not convs:
                break
            for c in convs:
                hay = " ".join([c.get("subject") or "", c.get("latest_message_subject") or "",
                                *_labels(c)])
                if _fuzzy(terms, hay) and (d is None or mentions(hay, d)):
                    matched.append(c)
                    if len(matched) >= limit:
                        break
            if len(matched) >= limit:
                break
            until = convs[-1].get("last_activity_at") or convs[-1].get("created_at")
        with ThreadPoolExecutor(max_workers=3) as pool:
            evs = list(pool.map(lambda c: self._evidence(c), matched))
        return [e for e in evs if e is not None]

    # -- one conversation ---------------------------------------------------
    def _messages(self, cid: str, pages: int = 3) -> list[dict]:
        """Newest first from the API; up to 30, returned oldest first."""
        out: list[dict] = []
        until = None
        for _ in range(pages):
            params = {"limit": 10}
            if until:
                params["until"] = until
            batch = self._get(f"/conversations/{cid}/messages", **params).get("messages", [])
            out.extend(batch)
            if len(batch) < 10:
                break
            until = batch[-1].get("delivered_at") or batch[-1].get("created_at")
        return list(reversed(out))

    def _bodies(self, ids: list[str]) -> dict[str, str]:
        bodies: dict[str, str] = {}
        for i in range(0, len(ids), 10):
            got = self._get(f"/messages/{','.join(ids[i:i + 10])}").get("messages", [])
            for m in (got if isinstance(got, list) else [got]):
                bodies[m["id"]] = html_to_text(m.get("body") or "")
        return bodies

    def _evidence(self, conv: dict) -> Evidence | None:
        cid = conv["id"]
        try:
            msgs = self._messages(cid)
            bodies = self._bodies([m["id"] for m in msgs])
        except SourceError:
            return None
        try:
            comments = self._get(f"/conversations/{cid}/comments").get("comments", [])
        except SourceError:
            comments = []
        subject = conv.get("subject") or conv.get("latest_message_subject") or next(
            (m.get("subject") for m in msgs if m.get("subject")), "")
        labels = _labels(conv)

        parts = [f"Subject: {subject}"]
        if labels:
            parts.append(f"Labels: {', '.join(labels)}")
        for m in msgs:
            frm = (m.get("from_field") or {})
            who = frm.get("name") or frm.get("address", "")
            parts.append(f"--- message from {who} <{frm.get('address', '')}> "
                         f"({_when(m.get('delivered_at'))}) ---\n"
                         f"{bodies.get(m['id']) or m.get('preview') or ''}")
        comment_meta = []
        for c in sorted(comments, key=lambda c: c.get("created_at") or 0):
            author = (c.get("author") or {}).get("name", "")
            text = html_to_text(c.get("body") or "")
            comment_meta.append({"author": author, "when": _when(c.get("created_at")), "text": text})
            parts.append(f"--- internal comment by {author} ({_when(c.get('created_at'))}) ---\n{text}")

        return Evidence(
            kind="missive", ref=f"missive:{cid}", title=subject,
            body="\n\n".join(parts),
            when=_when(conv.get("last_activity_at") or (msgs[-1].get("delivered_at") if msgs else None)),
            url=conv.get("web_url") or conv.get("app_url") or "",
            meta={"messages": len(msgs), "labels": labels, "comments": comment_meta,
                  "attachments": [attachment_meta(a.get("filename", ""), _mime(a), a.get("size"))
                                  for a in _files(msgs)]})

    def fetch(self, ref: str) -> Evidence | None:
        cid = ref.split(":", 1)[1] if ":" in ref else ref
        try:
            got = self._get(f"/conversations/{cid}").get("conversations") or [{}]
        except SourceError:
            return None
        conv = got[0] if isinstance(got, list) else got
        conv.setdefault("id", cid)
        return self._evidence(conv)

    def attachment(self, ref: str, index: int) -> AttachmentFile | None:
        cid = ref.split(":", 1)[1] if ":" in ref else ref
        files = _files(self._messages(cid))
        if not 0 <= index < len(files):
            return None
        a = files[index]
        return AttachmentFile(a.get("filename", "bestand"), _mime(a),
                              download(a["url"], ATTACHMENT_HOSTS))


def _when(ts) -> str:
    from datetime import datetime, timezone
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError, OSError):
        return str(ts or "")


def _labels(conv: dict) -> list[str]:
    """Shared labels -- at Leeuwenbergh, the event a conversation belongs to.
    (`shared_label_names` is one comma-joined string; the objects are safer.)"""
    return [l.get("name", "").replace("\xa0", " ") for l in conv.get("shared_labels") or []
            if l.get("name")]
