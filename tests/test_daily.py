"""The daily brief, and the one question underneath it: when is this event?

Every bug these cover was the same bug in a different place -- something read
the ClickUp due date and called it the event. It is not. The task name is.
"""

import datetime

from draaiboek.daily import Brief, event_date
from draaiboek.sources.base import Evidence
from draaiboek.sources.dates import from_epoch_ms, from_task_name

TODAY = datetime.date(2026, 9, 16)


def ev(title, *, due=None, event=None, cancelled=False, doc_id=None):
    meta = {"due_date": due, "event_date": event}
    if cancelled:
        meta["CANCELLED"] = "cancelled"
    if doc_id:
        meta["draaiboek_doc_id"] = doc_id
    return Evidence(kind="clickup", ref="clickup:t1", title=title, body="", meta=meta)


def ms(y, m, d):
    return int(datetime.datetime(y, m, d).timestamp() * 1000)


# -- the date itself -------------------------------------------------------

def test_the_name_beats_the_due_date():
    """Marron Festival: name says 4 October, ClickUp said 20 September."""
    e = ev("2026/10/04 · Marron Festival", due=ms(2026, 9, 20))
    assert event_date(e) == datetime.date(2026, 10, 4)


def test_a_multi_day_event_starts_on_its_first_day():
    e = ev("2026/09/27 + 28 + 29 + 30 · Lotte Meijerink")
    assert event_date(e) == datetime.date(2026, 9, 27)


def test_the_due_date_is_used_when_the_name_has_none():
    e = ev("INV-1061 | An Idiot's Guide to Wine", due=ms(2026, 9, 20))
    assert event_date(e) == datetime.date(2026, 9, 20)


def test_an_impossible_date_in_the_name_is_not_a_date():
    e = ev("2026/13/45 · nonsense", due=ms(2026, 9, 20))
    assert event_date(e) == datetime.date(2026, 9, 20)


def test_no_date_anywhere_is_none():
    assert event_date(ev("Something with no date")) is None


def test_the_source_decides_and_everything_else_reads_it():
    """meta['event_date'] is canonical: the parse is only a fallback."""
    e = ev("2026/10/04 · Marron Festival", due=ms(2026, 9, 20), event="2026-10-05")
    assert event_date(e) == datetime.date(2026, 10, 5)


def test_a_corrupt_canonical_value_falls_back_rather_than_raising():
    e = ev("2026/10/04 · Marron Festival", event="not-a-date")
    assert event_date(e) == datetime.date(2026, 10, 4)


def test_from_task_name_and_from_epoch_ms():
    assert from_task_name("2026/09/18 · FEVER") == datetime.date(2026, 9, 18)
    assert from_task_name("2026/9/8 · single digits") == datetime.date(2026, 9, 8)
    assert from_task_name("no date here") is None
    assert from_epoch_ms(None) is None
    assert from_epoch_ms("rubbish") is None
    assert from_epoch_ms(ms(2026, 9, 20)) == datetime.date(2026, 9, 20)


# -- the brief -------------------------------------------------------------

class FakeClickUp:
    def __init__(self, events):
        self.events = events
        self.asked = None

    def upcoming(self, days=60, limit=40):
        self.asked = (days, limit)
        return self.events


class FakeSources:
    def __init__(self, events):
        self.clickup = FakeClickUp(events)


class FakeProposals:
    def __init__(self, recs=()):
        self.recs = list(recs)

    def all(self, limit=50):
        return self.recs[:limit]


class FakeSvc:
    """Reads always fail -- the interesting case is what the brief says then."""

    def __init__(self, events, error=None, proposals=()):
        self.sources = FakeSources(events)
        self.proposals = FakeProposals(proposals)
        self.error = error

    def read(self, doc_id, record=True):
        raise self.error or RuntimeError("boom")

    def reminders_for(self, view):
        return []


def brief(events, error=None, days=21, proposals=()):
    return Brief(FakeSvc(events, error, proposals)).today(days=days, today=TODAY)


def proposal(pid="abc123", *, days_left=14, edits=42, status="open"):
    return {"id": pid, "status": status, "title": "Bruiloft 26 sep",
            "edits": [{}] * edits,
            "expires_at": (TODAY + datetime.timedelta(days=days_left)).isoformat()}


def test_events_are_ordered_by_the_date_in_the_name():
    out = brief([ev("2026/10/04 · Marron", due=ms(2026, 9, 20)),
                 ev("2026/09/18 · FEVER", due=ms(2026, 9, 18))])
    assert [r["date"] for r in out["events"]] == ["2026-09-18", "2026-10-04"]


def test_a_cancelled_event_is_not_a_task():
    out = brief([ev("2026/09/18 · FEVER"), ev("2026/09/19 · Gone", cancelled=True)])
    assert [r["title"] for r in out["events"]] == ["2026/09/18 · FEVER"]


def test_events_outside_the_window_are_left_out():
    out = brief([ev("2026/09/18 · soon"), ev("2027/01/26 · far off"),
                 ev("2026/09/01 · past")])
    assert [r["date"] for r in out["events"]] == ["2026-09-18"]


def test_the_brief_asks_for_the_window_it_reports_on():
    svc = FakeSvc([])
    Brief(svc).today(days=21, today=TODAY)
    days, limit = svc.sources.clickup.asked
    assert days == 21
    assert limit >= 100, "a fifty-events-a-month venue must not be silently truncated"


# -- a document that cannot be opened --------------------------------------

def test_a_403_is_reported_as_a_sharing_problem_not_a_stack_trace():
    out = brief([ev("2026/09/26 · Wedding", doc_id="DOC1")],
                error=RuntimeError("<HttpError 403 insufficient permissions>"))
    todo = out["events"][0]["todo"]
    assert [t["what"] for t in todo] == ["toegang"]
    assert "niet gedeeld" in todo[0]["note"]
    assert out["events"][0]["urgent"] is True


def test_an_ordinary_failure_still_says_what_went_wrong():
    out = brief([ev("2026/09/26 · Wedding", doc_id="DOC1")],
                error=RuntimeError("connection reset"))
    todo = out["events"][0]["todo"]
    assert [t["what"] for t in todo] == ["doc"]
    assert "connection reset" in todo[0]["note"]


def test_the_summary_counts_unreadable_separately_from_missing():
    out = brief([ev("2026/09/26 · Wedding", doc_id="DOC1"),
                 ev("2026/09/18 · FEVER")],
                error=RuntimeError("<HttpError 403 insufficient permissions>"))
    assert "1 zonder draaiboek" in out["summary"]
    assert "1 niet gedeeld" in out["summary"]


def test_an_empty_window_says_so():
    assert brief([])["summary"] == "Geen evenementen in dit venster."


# -- what is waiting on her ------------------------------------------------

def test_an_unanswered_proposal_appears_in_the_brief():
    """It expires in a fortnight and takes its rows with it, silently."""
    out = brief([ev("2026/09/18 · FEVER")], proposals=[proposal(days_left=3)])
    assert [w["proposal_id"] for w in out["waiting"]] == ["abc123"]
    assert out["waiting"][0]["edits"] == 42
    assert out["waiting"][0]["expires_in_days"] == 3
    assert "1 voorstel(len) wachten op jou" in out["summary"]


def test_decided_proposals_are_not_waiting():
    out = brief([ev("2026/09/18 · FEVER")],
                proposals=[proposal("a", status="applied"),
                           proposal("b", status="rejected"),
                           proposal("c", status="open")])
    assert [w["proposal_id"] for w in out["waiting"]] == ["c"]


def test_the_one_running_out_first_is_listed_first():
    out = brief([ev("2026/09/18 · FEVER")],
                proposals=[proposal("later", days_left=9),
                           proposal("sooner", days_left=1)])
    assert [w["proposal_id"] for w in out["waiting"]] == ["sooner", "later"]


def test_a_quiet_window_still_reports_what_is_waiting():
    out = brief([], proposals=[proposal()])
    assert out["events"] == []
    assert "wachten op jou" in out["summary"]
