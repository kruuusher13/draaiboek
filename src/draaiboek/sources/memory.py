"""Larissa's own history, as a source any agent can read.

Most of what matters about an event is never written in an email. It is said
in chat: the session times, the interval routine, which technician is free,
what she decided last time. That history has lived inside one agent's memory,
which meant only that agent could write a draaiboek.

This reads the same store over HTTP, so any agent can. And because what comes
back is retrievable evidence, a claim that "Larissa said X" is verified like
any other quote rather than taken on trust.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone

import requests

from .base import Evidence, SourceError

DEFAULT_PEER = "peer_Larissa_Groeneveld"
NOISE = re.compile(r"(IMPORTANT: Background process|exit code|Traceback|"
                   r"hermes-agent/venv|proc_[0-9a-f]{6}|\[The user sent a)", re.I)


def _body(content: str) -> str:
    """Entries are stored as "<speaker>: <message>"; the same message appears
    once per speaker id, so the prefix comes off before de-duplicating."""
    return content.split(": ", 1)[1] if ": " in content[:40] else content


class Memory:
    name = "memory"

    def __init__(self, base_url: str | None = None, peer: str | None = None):
        self.base = (base_url or os.environ.get("DRAAIBOEK_MEMORY_URL", "")).rstrip("/")
        self.peer = peer or os.environ.get("DRAAIBOEK_MEMORY_PEER", DEFAULT_PEER)
        self._all: list[dict] | None = None

    def available(self) -> bool:
        return bool(self.base)

    def _get(self, path: str, **params):
        if not self.base:
            raise SourceError(
                "No memory store configured. Set DRAAIBOEK_MEMORY_URL to reach "
                "Larissa's chat history; without it, what she has said in chat "
                "is invisible and only written sources can be quoted.")
        r = requests.get(f"{self.base}{path}", params=params, timeout=30)
        if r.status_code >= 400:
            raise SourceError(f"Memory {path} -> {r.status_code}: {r.text[:160]}")
        return r.json()

    def _entries(self) -> list[dict]:
        if self._all is not None:
            return self._all
        out, seen = [], set()
        for page in range(1, 8):
            data = self._get("/api/conclusions", observed_id=self.peer,
                             size=500, page=page)
            for it in data.get("items", []):
                body = _body(it.get("content", "")).strip()
                if not body or body in seen or NOISE.search(body):
                    continue
                seen.add(body)
                it["_body"] = body
                out.append(it)
            if page >= data.get("pages", 1):
                break
        self._all = out
        return out

    def search(self, query: str, limit: int = 8) -> list[Evidence]:
        from .clickup import GENERIC, match_score, _terms

        terms = _terms(query)
        if not terms:
            return []
        scored = []
        for it in self._entries():
            score = match_score(terms, it["_body"])
            if score:
                scored.append((score, it))
        if not scored:
            return []
        best = max(s for s, _ in scored)
        # Newest first among the strongest matches: her latest word wins.
        top = sorted((x for x in scored if x[0] >= max(best - 1, 1)),
                     key=lambda x: (-x[0], x[1].get("created_at", "")), reverse=False)
        top.sort(key=lambda x: x[1].get("created_at", ""), reverse=True)
        return [self._to_evidence(it) for _, it in top[:limit]]

    @staticmethod
    def _to_evidence(it: dict) -> Evidence:
        when = it.get("created_at", "")
        try:
            when = datetime.fromisoformat(when.replace("Z", "+00:00")).astimezone(
                timezone.utc).strftime("%Y-%m-%d %H:%M")
        except (ValueError, AttributeError):
            pass
        body = it["_body"]
        return Evidence(
            kind="memory", ref=f"memory:{it['id']}",
            title=f"Larissa, {when}", body=body, when=when,
            meta={"observed": it.get("observed_id", ""),
                  "note": "Said in chat, not written in an email. Her own words."},
        )

    def fetch(self, ref: str) -> Evidence | None:
        wanted = ref.split(":", 1)[1] if ":" in ref else ref
        for it in self._entries():
            if it.get("id") == wanted:
                return self._to_evidence(it)
        return None
