"""The evidence cache, and the check that closes the hallucination loop.

Gathering writes every retrieved document here. Before any write, each op's
`source.quote` is verified to literally appear in the cached body of the
document its `source.ref` names.

The effect: the agent cannot write a fact it did not retrieve. Not
"discouraged by the prompt" -- the write is refused, with the quote it
could not find.
"""

from __future__ import annotations

import json
from pathlib import Path

from .sources.base import Evidence, normalise


class EvidenceStore:
    def __init__(self, home: Path):
        self.dir = home / "evidence"
        self.dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe(ref: str) -> str:
        return "".join(c if c.isalnum() or c in "-_." else "_" for c in ref)[:120]

    def put(self, ev: Evidence) -> None:
        (self.dir / f"{self._safe(ev.ref)}.json").write_text(
            json.dumps(ev.to_dict(), ensure_ascii=False)
        )

    def put_many(self, evs: list[Evidence]) -> None:
        for e in evs:
            self.put(e)

    def get(self, ref: str) -> Evidence | None:
        p = self.dir / f"{self._safe(ref)}.json"
        if not p.exists():
            return None
        try:
            return Evidence(**json.loads(p.read_text()))
        except (json.JSONDecodeError, TypeError):
            return None

    def verify(self, kind: str, ref: str, quote: str) -> tuple[bool, str]:
        """(ok, explanation). Kinds that cannot be retrieved are exempt."""
        if kind in ("larissa", "house_rule", "doc"):
            # She said it in chat, it is a standing rule, or it is already in
            # the document. None of these are retrievable evidence.
            return True, ""
        ev = self.get(ref)
        if ev is None:
            return False, (
                f"No gathered evidence with ref {ref!r}. Call gather() first and cite a "
                f"ref it returned -- a reference you composed yourself proves nothing."
            )
        if not ev.contains(quote):
            return False, (
                f"The quote does not appear in {ref!r} ({ev.title!r}). You wrote:\n"
                f"  {quote!r}\n"
                f"If the source does not say it, it is not a fact. Put it in Open Punten "
                f"as a question instead."
            )
        return True, ""

    def stats(self) -> dict:
        files = list(self.dir.glob("*.json"))
        return {"documents": len(files)}
