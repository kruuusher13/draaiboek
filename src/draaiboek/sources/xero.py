"""Xero quotes -- item names and quantities only.

Monetary amounts are stripped before the evidence ever reaches an agent. The
guard would refuse a price on its way into the document anyway; removing it
here means the number is never in the agent's context to copy in the first place.
"""

from __future__ import annotations

import os
from typing import Any

import requests

from .base import Evidence, SourceError

def _date_strings(d) -> list[str]:
    """How this date is written in a Xero reference: "26 september 2026"."""
    from .dates import MONTHS
    month = max((name for name, n in MONTHS.items() if n == d.month), key=len)
    return [f"{d.day} {month} {d.year}", f"{d.day:02d}-{d.month:02d}-{d.year}",
            d.isoformat()]


TOKEN_URL = "https://identity.xero.com/connect/token"
API = "https://api.xero.com/api.xro/2.0"


class Xero:
    name = "xero"

    def __init__(self):
        self.client_id = os.environ.get("XERO_CLIENT_ID", "")
        self.client_secret = os.environ.get("XERO_CLIENT_SECRET", "")
        self.refresh_token = os.environ.get("XERO_REFRESH_TOKEN", "")
        self.tenant_id = os.environ.get("XERO_TENANT_ID", "")
        self._access: str | None = None
        self._access_until = 0.0

    def available(self) -> bool:
        return bool(self.client_id and self.client_secret and self.refresh_token)

    def _token(self) -> str:
        import time
        # Access tokens last 30 minutes. A long-running process (the workspace,
        # the MCP server) must refresh, or every search after that fails.
        if self._access and time.time() < self._access_until:
            return self._access
        r = requests.post(TOKEN_URL, data={
            "grant_type": "refresh_token", "refresh_token": self.refresh_token,
        }, auth=(self.client_id, self.client_secret), timeout=30)
        if r.status_code >= 400:
            raise SourceError(
                f"Xero token refresh failed ({r.status_code}). The refresh token is "
                f"single-use: store the new one Xero returns each time, or it expires."
            )
        data = r.json()
        self._access = data["access_token"]
        self._access_until = time.time() + int(data.get("expires_in", 1800)) - 60
        if data.get("refresh_token"):
            self._rotate(data["refresh_token"])
        return self._access

    @staticmethod
    def _rotate(new: str) -> None:
        """Xero rotates the refresh token on every use: the old one dies
        immediately. Writing it only to a side file was not enough -- the env
        file is what the next process reads, so it has to be updated in place
        or Xero breaks on the second run."""
        from pathlib import Path

        os.environ["XERO_REFRESH_TOKEN"] = new
        env = Path(os.environ.get("DRAAIBOEK_ENV", Path.home() / ".draaiboek" / "env"))
        if env.exists():
            lines = env.read_text().splitlines()
            out, seen = [], False
            for ln in lines:
                if ln.strip().startswith("XERO_REFRESH_TOKEN="):
                    out.append(f"XERO_REFRESH_TOKEN={new}")
                    seen = True
                else:
                    out.append(ln)
            if not seen:
                out.append(f"XERO_REFRESH_TOKEN={new}")
            tmp = env.with_suffix(".tmp")
            tmp.write_text("\n".join(out) + "\n")
            tmp.chmod(0o600)
            tmp.replace(env)
        else:
            p = Path.home() / ".draaiboek" / "xero_refresh_token"
            p.write_text(new)
            p.chmod(0o600)

    def _headers(self) -> dict:
        if not self.tenant_id:
            raise SourceError("XERO_TENANT_ID is not set")
        return {"Authorization": f"Bearer {self._token()}",
                "Xero-tenant-id": self.tenant_id, "Accept": "application/json"}

    @staticmethod
    def _to_evidence(q: dict[str, Any]) -> Evidence:
        lines = [
            f"{li.get('Quantity','')} x {li.get('Description','').strip()}"
            for li in q.get("LineItems", []) if li.get("Description")
        ]
        body = "\n".join([
            f"Quote: {q.get('QuoteNumber','')}",
            f"Contact: {(q.get('Contact') or {}).get('Name','')}",
            f"Reference: {q.get('Reference','')}",
            f"Date: {q.get('DateString', q.get('Date',''))}",
            f"Status: {q.get('Status','')}",
            "", "Line items (quantities and descriptions only):", *lines,
        ])
        return Evidence(
            kind="xero", ref=f"xero:{q.get('QuoteNumber','')}",
            title=f"{q.get('QuoteNumber','')} {(q.get('Contact') or {}).get('Name','')}",
            body=body, when=str(q.get("DateString", q.get("Date", ""))),
            meta={"quote_number": q.get("QuoteNumber", ""),
                  "contact": (q.get("Contact") or {}).get("Name", ""),
                  "warning": "Verify this quote number against the ClickUp task "
                             "before use -- quotes have matched the wrong event."},
        )

    def _quotes(self, **params) -> list[dict]:
        """One page of quotes. Xero returns 100 per page, oldest first, so the
        filters below matter -- there are well over a thousand."""
        r = requests.get(f"{API}/Quotes", headers=self._headers(), params=params,
                         timeout=45)
        if r.status_code == 401:  # revoked or expired early: refresh once
            self._access = None
            r = requests.get(f"{API}/Quotes", headers=self._headers(), params=params,
                             timeout=45)
        if r.status_code >= 400:
            raise SourceError(f"Xero Quotes -> {r.status_code}: {r.text[:200]}")
        return r.json().get("Quotes", [])

    def search(self, query: str, limit: int = 10) -> list[Evidence]:
        from .clickup import QUOTE_REF, _fuzzy, _terms
        from .dates import find_date, mentions

        # 1. An explicit quote number is an exact lookup.
        refs = QUOTE_REF.findall(query)
        if refs:
            out = []
            for num in refs[:limit]:
                out.extend(self._to_evidence(q)
                           for q in self._quotes(QuoteNumber=num))
            if out:
                return out[:limit]

        d, rest = find_date(query)
        terms = _terms(rest)

        # 2. Everything else is one scoring pass.
        #
        #    A Xero quote attached to the wrong event is the oldest bug here --
        #    the Uitvaart van den Bogerd quote kept turning up on unrelated
        #    events. Two rules keep it honest:
        #
        #      * A date alone never qualifies a quote. Several weddings share a
        #        Saturday in September, and the quote references all say
        #        "Bruiloft", so matching on those words returns every wedding in
        #        the file. Words that describe every second event score nothing.
        #      * Only the best-scoring quotes come back. A weaker match is a
        #        different event, and returning it reads as confirmation.
        from .clickup import GENERIC, match_score

        distinctive = [t for t in terms if t not in GENERIC]
        dates = _date_strings(d) if d else []

        scored: list[tuple[int, dict]] = []
        for q in self._all_quotes():
            contact = (q.get("Contact") or {}).get("Name", "")
            ref = (q.get("Reference", "") or "")
            hay = f"{q.get('QuoteNumber','')} {contact} {ref}"
            named = match_score(terms, hay)
            # The year matters. Half the references say "26 september"; only
            # one of them is this year's event.
            dated = any(ds in ref.lower() for ds in dates)

            if distinctive:
                # A name was given: it has to match. The date only ranks.
                if not named:
                    continue
                scored.append((named + (2 if dated else 0), q))
            elif dated:
                # Only a date was given. Every quote for that exact day is a
                # candidate; none of them is confirmed to be the right one.
                scored.append((1, q))

        if not scored:
            return []
        best = max(s for s, _ in scored)
        return [self._to_evidence(q)
                for s, q in sorted(scored, key=lambda sq: -sq[0])
                if s == best][:limit]

    _cache: list[dict] | None = None

    def _all_quotes(self) -> list[dict]:
        """Xero pages 100 at a time and has well over a thousand quotes.
        Fetched once per process; a quote does not change mid-run."""
        if Xero._cache is None:
            out, page = [], 1
            while page <= 30:
                batch = self._quotes(page=page)
                if not batch:
                    break
                out.extend(batch)
                page += 1
            Xero._cache = out
        return Xero._cache

    def fetch(self, ref: str) -> Evidence | None:
        num = ref.split(":", 1)[1] if ":" in ref else ref
        found = self._quotes(QuoteNumber=num)
        return self._to_evidence(found[0]) if found else None
