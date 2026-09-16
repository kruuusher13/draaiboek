"""One search across every system, returning quotable evidence."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .base import AttachmentFile, Evidence, SourceError
from .clickup import ClickUp
from .gmail import Gmail
from .memory import Memory
from .missive import Missive
from .xero import Xero


class Sources:
    def __init__(self, google=None):
        self.clickup = ClickUp()
        self.missive = Missive()
        self.xero = Xero()
        self.memory = Memory()
        self.gmail = Gmail(google) if google is not None else None

    def _clients(self) -> dict[str, Any]:
        c = {"clickup": self.clickup, "missive": self.missive, "xero": self.xero,
             "memory": self.memory}
        if self.gmail is not None:
            c["gmail"] = self.gmail
        return c

    def status(self) -> dict[str, Any]:
        out = {}
        for name, client in self._clients().items():
            try:
                out[name] = {"configured": bool(client.available())}
            except Exception as e:  # noqa: BLE001
                out[name] = {"configured": False, "error": str(e)[:200]}
        return out

    def gather(self, query: str, kinds: list[str] | None = None,
               limit: int = 8) -> tuple[list[Evidence], dict[str, str]]:
        """Search every configured system in parallel.

        Returns (evidence, errors). A system that is down or unconfigured never
        blocks the others -- partial evidence beats no evidence, as long as the
        agent is told which systems it did not hear from.
        """
        clients = {k: v for k, v in self._clients().items()
                   if (not kinds or k in kinds)}
        results: list[Evidence] = []
        errors: dict[str, str] = {}

        def run(item):
            name, client = item
            try:
                if not client.available():
                    return name, None, "not configured"
                return name, client.search(query, limit), None
            except SourceError as e:
                return name, None, str(e)
            except Exception as e:  # noqa: BLE001
                return name, None, f"{type(e).__name__}: {e}"

        with ThreadPoolExecutor(max_workers=4) as pool:
            for name, evs, err in pool.map(run, clients.items()):
                if err:
                    errors[name] = err
                elif evs:
                    results.extend(evs)

        # Missive cannot be searched, so reach its threads through the links
        # ClickUp holds. Done after the parallel pass, which is when the
        # ClickUp evidence exists to read them from.
        for ev in self.follow_missive(results):
            if ev.ref not in {e.ref for e in results}:
                results.append(ev)

        # Gmail first: her instruction is that direct Gmail search beats Missive.
        order = {"email": 0, "clickup": 1, "xero": 2, "missive": 3}
        results.sort(key=lambda e: order.get(e.kind, 9))
        return results, errors

    def _client_for(self, ref: str):
        prefix = ref.split(":", 1)[0] if ":" in ref else ""
        return {"gmail": self.gmail, "clickup": self.clickup,
                "xero": self.xero, "missive": self.missive}.get(prefix)

    def follow_missive(self, found: list) -> list:
        """Missive has no search endpoint -- `search` and `q` are accepted and
        ignored. Guessing from the most recent conversations finds an event
        from three weeks ago only by luck.

        ClickUp tasks carry the thread link, so follow that instead. It is
        exact, and it is the only way the internal comments -- the notes the
        team writes to each other, which never appear in the mailbox -- reach
        the agent at all.
        """
        if self.missive is None or not self.missive.available():
            return []
        ids: list[str] = []
        for ev in found:
            for cid in (ev.meta.get("missive_ids") or []):
                if cid not in ids:
                    ids.append(cid)
        out = []
        for cid in ids[:6]:
            try:
                ev = self.missive.fetch(f"missive:{cid}")
            except Exception:  # noqa: BLE001
                continue
            if ev is not None:
                out.append(ev)
        return out

    def fetch(self, ref: str) -> Evidence | None:
        client = self._client_for(ref)
        return client.fetch(ref) if client else None

    def attachment(self, ref: str, index: int) -> AttachmentFile | None:
        """Download one attachment, by its position in `meta["attachments"]`."""
        client = self._client_for(ref)
        if client is None or not hasattr(client, "attachment"):
            return None
        return client.attachment(ref, index)
