"""One shape for everything we read, whatever system it came from."""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


def html_to_text(s: str) -> str:
    s = re.sub(r"(?is)<(script|style|head).*?</\1>", " ", s or "")
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>|</h\d>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = (s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
          .replace("&gt;", ">").replace("&#39;", "'").replace("&quot;", '"'))
    s = re.sub(r"[ \t]{2,}", " ", s)
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", s).strip()


def normalise(text: str) -> str:
    """Whitespace- and case-insensitive, for quote matching across the
    reformatting that email and HTML do to text."""
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


@dataclass
class Evidence:
    """A retrieved document. `ref` is what goes into an op's `source.ref`,
    and the op's `source.quote` must literally appear in `body`."""

    kind: str          # email | clickup | xero | missive
    ref: str           # stable, unique, e.g. "gmail:18f2ac91"
    title: str
    body: str
    when: str = ""
    url: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def digest(self) -> str:
        return hashlib.sha1(self.body.encode()).hexdigest()[:12]

    def contains(self, quote: str) -> bool:
        return normalise(quote) in normalise(self.body)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def preview(self, n: int = 400) -> dict[str, Any]:
        d = self.to_dict()
        d["body"] = self.body[:n] + ("…" if len(self.body) > n else "")
        return d


class SourceClient(Protocol):
    name: str

    def available(self) -> bool: ...
    def search(self, query: str, limit: int = 10) -> list[Evidence]: ...
    def fetch(self, ref: str) -> Evidence | None: ...


class SourceError(RuntimeError):
    pass


@dataclass
class AttachmentFile:
    """The bytes of one attachment, fetched on demand.

    Evidence only ever carries attachment *names* (`meta["attachments"]`:
    filename, mime, size, inline). URLs and download ids stay out of it: they
    are signed or short-lived, and the evidence reaches the agent's context.
    Downloads re-read the source and pick the attachment by its position.
    """

    filename: str
    mime: str
    data: bytes


def attachment_meta(filename: str, mime: str, size, inline: bool = False) -> dict[str, Any]:
    try:
        size = int(size or 0)
    except (TypeError, ValueError):
        size = 0
    return {"filename": filename or "bestand", "mime": mime or "application/octet-stream",
            "size": size, "inline": bool(inline)}


def download(url: str, allowed_hosts: tuple[str, ...], headers: dict | None = None,
             timeout: int = 60) -> bytes:
    """GET an attachment URL that a source API handed us -- and only such URLs."""
    from urllib.parse import urlparse

    import requests

    host = urlparse(url).hostname or ""
    if urlparse(url).scheme != "https" or not any(
            host == h or host.endswith("." + h) for h in allowed_hosts):
        raise SourceError(f"Refusing to download from unexpected host {host!r}")
    r = requests.get(url, headers=headers or {}, timeout=timeout)
    if r.status_code >= 400:
        raise SourceError(f"Attachment download -> {r.status_code}")
    return r.content
