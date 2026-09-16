"""What needs doing today, across every upcoming event.

Fifty events a month cannot be worked one bespoke script at a time. The
judgment in a draaiboek -- which sentence belongs in which chapter -- is the
agent's job. Deciding *what to look at this morning* is not: it is the same
question every day, and it has a mechanical answer.

This asks it. For each event in the window: is there a draaiboek, how close is
it, what is still open, whose number is missing, has Triade been booked, does
the head count fit the room. Then it ranks by how soon it bites.
"""

from __future__ import annotations

import datetime
import re
from typing import Any

from .sources.dates import from_task_name

PENDING = re.compile(
    r"(?i)nog\s+(?:op\s*te\s*vragen|opvragen|bevestigen|ontvangen|inplannen|afstemmen)"
    r"|\[\s*nog[^\]]*\]|nog\s+geen\s+antwoord|\bonbekend\b")

# How long before the day itself each thing has to be settled.
DEADLINES = [
    ("draaiboek", 7, "Er is nog geen draaiboek."),
    ("triade", 5, "Geen Triade-levering geboekt."),
    ("nummers", 3, "Telefoonnummers ontbreken in de call sheet."),
    ("open", 2, "Open punten staan nog open."),
]


def _date(ms: Any) -> datetime.date | None:
    try:
        return datetime.date.fromtimestamp(int(ms) / 1000)
    except (TypeError, ValueError):
        return None


def event_date(ev) -> datetime.date | None:
    """When the event actually is.

    Tasks are named "YYYY/MM/DD · Client", and that date is the event. The
    ClickUp due date often is not -- it drifts, or it is the date somebody has
    to do something about the task. Trusting it put the Marron Festival on the
    twentieth of September when the task itself says the fourth of October.

    The source settles this now and carries it in `event_date`; the parse here
    is for evidence that predates that, or that comes from somewhere else.
    """
    iso = ev.meta.get("event_date")
    if iso:
        try:
            return datetime.date.fromisoformat(iso)
        except ValueError:
            pass
    return from_task_name(ev.title or "") or _date(ev.meta.get("due_date"))


class Brief:
    def __init__(self, svc):
        self.svc = svc

    def today(self, days: int = 21, today: datetime.date | None = None) -> dict[str, Any]:
        today = today or datetime.date.today()
        horizon = today + datetime.timedelta(days=days)
        # Ask for the window this brief actually reports on, and for enough of
        # it. Asking for sixty days and taking the default forty tasks quietly
        # cut a fifty-events-a-month venue down to whatever fitted: the brief
        # listed twelve of the thirty-one events in its own three weeks, and
        # said nothing about the rest.
        events = self.svc.sources.clickup.upcoming(days, limit=250)

        rows: list[dict[str, Any]] = []
        for ev in events:
            when = event_date(ev)
            if not when or when < today or when > horizon:
                continue
            if ev.meta.get("CANCELLED"):
                # "Als een event is geannuleerd, doe dan niets." Not a task.
                continue
            rows.append(self._event(ev, when, today))

        rows.sort(key=lambda r: (not r["urgent"], r["days"], r["title"]))
        urgent = [r for r in rows if r["urgent"]]
        waiting = self._waiting(today)
        return {
            "date": today.isoformat(),
            "window_days": days,
            "events": rows,
            "urgent": len(urgent),
            "waiting": waiting,
            "summary": self._summary(rows, urgent, waiting),
        }

    def _waiting(self, today: datetime.date) -> list[dict[str, Any]]:
        """Proposals sitting unanswered.

        A proposal expires after a fortnight and takes its rows with it, without
        telling anyone. This is the one thing Larissa reads every morning, so a
        batch of sourced work quietly running out of time belongs in it.
        """
        out = []
        for rec in self.svc.proposals.all(200):
            if rec.get("status") != "open":
                continue
            left = None
            try:
                left = (datetime.date.fromisoformat(rec["expires_at"][:10]) - today).days
            except (KeyError, TypeError, ValueError):
                pass
            out.append({"proposal_id": rec["id"],
                        "title": rec.get("title") or rec.get("doc_title") or "",
                        "edits": len(rec.get("edits", [])),
                        "expires_in_days": left})
        out.sort(key=lambda r: (r["expires_in_days"] is None, r["expires_in_days"]))
        return out

    def _event(self, ev, when: datetime.date, today: datetime.date) -> dict[str, Any]:
        days = (when - today).days
        doc_id = ev.meta.get("draaiboek_doc_id")
        item: dict[str, Any] = {
            "ref": ev.ref, "title": ev.title, "date": when.isoformat(),
            "days": days, "doc_id": doc_id, "todo": [], "urgent": False,
            "url": ev.url,
        }
        if not doc_id:
            item["todo"].append({"what": "draaiboek", "note": DEADLINES[0][2]})
        else:
            item.update(self._from_doc(doc_id, item))

        for key, lead, note in DEADLINES:
            if any(t["what"] == key for t in item["todo"]) and days <= lead:
                item["urgent"] = True
        return item

    def _from_doc(self, doc_id: str, item: dict[str, Any]) -> dict[str, Any]:
        from . import calllist
        try:
            view, _ = self.svc.read(doc_id, record=False)
        except Exception as e:  # noqa: BLE001 -- an unreachable doc is itself the news
            shared = "permission" in str(e).lower() or "403" in str(e)
            item["todo"].append({
                "what": "toegang" if shared else "doc",
                "note": ("Draaiboek is niet gedeeld met het systeem — deel het, of "
                         "verplaats het naar de Draaiboeken-map."
                         if shared else f"Draaiboek niet leesbaar: {str(e)[:70]}")})
            item["urgent"] = shared
            return {}

        rows = view.data_rows()
        open_section = view.section_by_name("Open punten") or view.section_by_name("Open Punten")
        open_n = len(open_section.row_ids) if open_section else 0
        if open_n:
            item["todo"].append({"what": "open", "note": f"{open_n} open punt(en)."})

        missing = [t["name"] for t in calllist.build(view, []) if t.get("phone_pending")]
        if missing:
            item["todo"].append({
                "what": "nummers",
                "note": f"Nummer ontbreekt: {', '.join(missing[:3])}"
                        + (" …" if len(missing) > 3 else "")})

        for note in self.svc.reminders_for(view):
            key = "triade" if "triade" in note.lower() else "let op"
            item["todo"].append({"what": key, "note": note})

        item["rows"] = len(rows)
        item["revision"] = view.revision_id
        return {}

    @staticmethod
    def _summary(rows: list[dict], urgent: list[dict], waiting: list[dict] = ()) -> str:
        if not rows:
            return ("Geen evenementen in dit venster."
                    if not waiting else
                    f"Geen evenementen in dit venster · {len(waiting)} voorstel(len) "
                    f"wachten op jou.")
        no_doc = sum(1 for r in rows if not r["doc_id"])
        # A draaiboek the system cannot open is not a draaiboek it has. Counting
        # only the events with no document at all said "6 zonder draaiboek" on a
        # morning when the other six were unreadable too, and read like a
        # half-full list rather than an empty one.
        locked = sum(1 for r in rows if any(t["what"] == "toegang" for t in r["todo"]))
        parts = [f"{len(rows)} evenement(en)"]
        if no_doc:
            parts.append(f"{no_doc} zonder draaiboek")
        if locked:
            parts.append(f"{locked} niet gedeeld")
        if waiting:
            parts.append(f"{len(waiting)} voorstel(len) wachten op jou")
        if urgent:
            first = urgent[0]
            parts.append(f"eerst: {first['title'][:48]} over {first['days']} dag(en)")
        return " · ".join(parts)
