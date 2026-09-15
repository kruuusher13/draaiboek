"""Gmail. Subject, body and signature in full -- never truncated, because the
supplier's phone number is usually in the signature."""

from __future__ import annotations

import base64
import os
from typing import Any

from .base import AttachmentFile, Evidence, SourceError, attachment_meta, html_to_text

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data.encode() + b"==").decode("utf-8", "replace")


def _walk(part: dict, out: list[str], html: list[str],
          files: list[dict] | None = None) -> None:
    mime, body = part.get("mimeType", ""), part.get("body", {})
    if part.get("filename") and body.get("attachmentId"):
        if files is not None:
            files.append(part)
    elif body.get("data"):
        (out if mime == "text/plain" else html if mime == "text/html" else []).append(
            _decode(body["data"])
        )
    for p in part.get("parts", []):
        _walk(p, out, html, files)


def _is_inline(part: dict) -> bool:
    """Signature logos and pasted images, as opposed to attached files."""
    headers = {h["name"].lower(): h["value"].lower() for h in part.get("headers", [])}
    disposition = headers.get("content-disposition", "")
    if disposition.startswith("attachment"):
        return False
    return disposition.startswith("inline") or "content-id" in headers


_strip_html = html_to_text


def _is_inline(part: dict) -> bool:
    """Signature logos and pasted images, as opposed to attached files."""
    headers = {h["name"].lower(): h["value"].lower() for h in part.get("headers", [])}
    disposition = headers.get("content-disposition", "")
    if disposition.startswith("attachment"):
        return False
    return disposition.startswith("inline") or "content-id" in headers


def _strip_html(s: str) -> str:
    import re
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = (s.replace("&nbsp;", " ").replace("&amp;", "&")
          .replace("&lt;", "<").replace("&gt;", ">").replace("&#39;", "'"))
    return re.sub(r"[ \t]{2,}", " ", s)


class Gmail:
    name = "gmail"

    def __init__(self, google, mailbox: str | None = None):
        self._google = google
        self.mailbox = mailbox or os.environ.get("GOOGLE_IMPERSONATE", "me")
        self._svc = None

    def available(self) -> bool:
        import os
        from pathlib import Path
        # Cheap check only: building the service does not prove delegation works.
        return bool(os.environ.get("GOOGLE_IMPERSONATE")) and Path(
            os.environ.get("GOOGLE_CREDENTIALS", "")
            or os.environ.get("DRAAIBOEK_CLIENT_SECRET", "")
        ).exists()

    @property
    def service(self):
        if self._svc is None:
            from googleapiclient.discovery import build
            self._svc = build("gmail", "v1",
                              credentials=self._google.gmail_credentials(),
                              cache_discovery=False)
        return self._svc

    def _user(self) -> str:
        return "me"

    @staticmethod
    def _queries(query: str) -> list[str]:
        """Gmail matches words literally, so "26th september" finds nothing in a
        mail that says "26 september". Search every way the date is written --
        first in subjects (the event's own threads), then anywhere."""
        from .dates import date_variants, find_date
        d, rest = find_date(query)
        if d is None:
            return [query]
        variants = " OR ".join(f'"{v}"' for v in dict.fromkeys(date_variants(d)))
        return [f"subject:({variants}) {rest}".strip(), f"({variants}) {rest}".strip()]

    def search(self, query: str, limit: int = 10) -> list[Evidence]:
        ids: list[str] = []
        try:
            for q in self._queries(query):
                res = self.service.users().messages().list(
                    userId=self._user(), q=q, maxResults=limit).execute()
                ids += [m["id"] for m in res.get("messages", []) if m["id"] not in ids]
                if len(ids) >= limit:
                    break
        except Exception as e:  # noqa: BLE001
            raise SourceError(f"Gmail search failed: {e}") from e
        return [ev for mid in ids[:limit] if (ev := self.fetch(f"gmail:{mid}")) is not None]

    def _message(self, mid: str) -> dict[str, Any] | None:
        try:
            return self.service.users().messages().get(
                userId=self._user(), id=mid, format="full"
            ).execute()
        except Exception:  # noqa: BLE001
            return None

    def fetch(self, ref: str) -> Evidence | None:
        mid = ref.split(":", 1)[1] if ":" in ref else ref
        msg = self._message(mid)
        if msg is None:
            return None
        headers = {h["name"].lower(): h["value"]
                   for h in msg.get("payload", {}).get("headers", [])}
        plain: list[str] = []
        html: list[str] = []
        files: list[dict] = []
        _walk(msg.get("payload", {}), plain, html, files)
        text = "\n".join(plain).strip() or _strip_html("\n".join(html))
        body = (
            f"From: {headers.get('from','')}\n"
            f"To: {headers.get('to','')}\n"
            f"Date: {headers.get('date','')}\n"
            f"Subject: {headers.get('subject','')}\n\n{text}"
        )
        return Evidence(
            kind="email", ref=f"gmail:{mid}", title=headers.get("subject", ""),
            body=body, when=headers.get("date", ""),
            url=f"https://mail.google.com/mail/u/0/#inbox/{mid}",
            meta={"from": headers.get("from", ""), "thread": msg.get("threadId", ""),
                  "attachments": [
                      attachment_meta(f["filename"], f.get("mimeType", ""),
                                      f.get("body", {}).get("size"), _is_inline(f))
                      for f in files]},
        )

    def attachment(self, ref: str, index: int) -> AttachmentFile | None:
        mid = ref.split(":", 1)[1] if ":" in ref else ref
        msg = self._message(mid)
        if msg is None:
            return None
        files: list[dict] = []
        _walk(msg.get("payload", {}), [], [], files)
        if not 0 <= index < len(files):
            return None
        part = files[index]
        try:
            data = self.service.users().messages().attachments().get(
                userId=self._user(), messageId=mid, id=part["body"]["attachmentId"]
            ).execute()["data"]
        except Exception as e:  # noqa: BLE001
            raise SourceError(f"Gmail attachment download failed: {e}") from e
        return AttachmentFile(part["filename"], part.get("mimeType", ""),
                              base64.urlsafe_b64decode(data.encode() + b"=="))
