import pathlib

import pytest
from pydantic import ValidationError

from draaiboek.guard import Guard, blocking
from draaiboek.ops import AddRow, RemoveRow, ReplaceText, Source, UpdateRow

GUARDS = pathlib.Path(__file__).resolve().parent.parent / "rules" / "guards.yaml"
SRC = Source(kind="larissa", ref="15 sep 2026", quote="deurbel en koffiemachines")


@pytest.fixture
def guard():
    return Guard(GUARDS)


def rules_hit(guard, op, view=None, tombs=None):
    return {v.rule for v in guard.check([op], view, tombs)}


def add(section="Tijdschema", **kw):
    kw.setdefault("values", ["19:30", "Inloop gasten", ""])
    return AddRow(section=section, source=SRC, **kw)


# --- the prime directive is a type error, not a prompt ----------------------
def test_a_fact_without_a_source_cannot_be_expressed():
    with pytest.raises(ValidationError):
        AddRow(section="Tijdschema", values=["19:30", "Inloop"])


def test_a_source_without_a_quote_cannot_be_expressed():
    with pytest.raises(ValidationError):
        Source(kind="email", ref="msg-1", quote="")


# --- financials -------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "Borrelplank €12,50 p.p.", "aanbetaling voldaan", "excl. btw",
    "uurtarief technicus", "Minimum 4 uur · facturatie", "borg 250,00",
])
def test_amounts_are_blocked(guard, text):
    assert "financials" in rules_hit(guard, add(values=["", text, ""]))


@pytest.mark.parametrize("text", [
    "Offerte bedrag 1.250,00", "Factuurnummer noteren 1.100,50",
])
def test_financial_words_block_when_an_amount_is_present(guard, text):
    assert blocking(guard.check([add(values=["", text, ""])]))


@pytest.mark.parametrize("text", [
    "Akkoord op de offerte ontvangen — 15 sep",
    "40 barkrukken incl. transport",
    "18.30 uur dinerbuffet",
    "Levering vrijdag 25 september om 17:00",
])
def test_ordinary_wording_is_not_mistaken_for_money(guard, text):
    """Over-blocking is its own failure: a guard that refuses legitimate rows
    teaches the agent to route around it."""
    assert not blocking(guard.check([add(values=["", text, ""])]))


@pytest.mark.parametrize("text", [
    "Drankjes op nacalculatie",
    "Drankjes in arrangement",
    "Gasten rekenen zelf af met muntjes",
])
def test_settlement_wording_she_requires_is_not_blocked(guard, text):
    """'Lock THIS IN: schrijf altijd of drankjes op nacalculatie zijn...' (11 sep).
    A financial guard that blocks this would break a rule to enforce a rule."""
    assert not blocking(guard.check([add(values=["", text, ""])]))


# --- citations --------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "Statafels gehuurd (Xero QU-0975)", "(bron: mail Sandra)", "zie mail van 15 aug",
])
def test_source_citations_are_blocked(guard, text):
    assert "source_citation" in rules_hit(guard, add(values=["", text, ""]))


# --- lexicon ----------------------------------------------------------------
def test_hal_is_refused(guard):
    assert any(r.startswith("lexicon") for r in rules_hit(guard, add(values=["19:30", "Inloop in de hal", ""])))


def test_event_titles_containing_hal_are_untouched(guard):
    """'Halve Oudejaarsconference' is a real event title in their calendar.
    A naive substring check would make it unwritable."""
    assert not blocking(guard.check([add(values=["20:30", "Aanvang Halve Oudejaarsconference", ""])]))


def test_genodigden_is_refused(guard):
    assert any(r.startswith("lexicon") for r in rules_hit(guard, add(values=["", "Ontvangst genodigden", ""])))


# --- Larissa's section ------------------------------------------------------
def test_bijzonderheden_is_untouchable_by_section_name(guard, view):
    assert "locked_section" in rules_hit(guard, add(section="Bijzonderheden"), view)


def test_bijzonderheden_is_untouchable_by_row_id(guard, view):
    """Addressing the row directly must not be a way around the lock."""
    op = UpdateRow(row_id="t0r6", expect_contains="Larissa", set_values={"1": "x"}, source=SRC)
    assert "locked_section" in rules_hit(guard, op, view)


# --- structural safety ------------------------------------------------------
def test_anchoring_on_a_band_is_refused(guard, view):
    """The 'coffee break on the header' bug: inserting after a band row
    collapses the text into cell 0."""
    assert "bad_anchor" in rules_hit(guard, add(after_row_id="t0r0"), view)


def test_anchoring_on_a_column_header_is_allowed(guard, view):
    """That is how you insert a row first in a section. Only the merged
    section band is a dangerous anchor."""
    assert not blocking(guard.check([add(after_row_id="t0r1")], view))


def test_anchoring_on_a_data_row_is_fine(guard, view):
    assert not blocking(guard.check([add(after_row_id="t0r3")], view))


def test_editing_a_row_you_misidentified_is_refused(guard, view):
    op = UpdateRow(row_id="t0r3", expect_contains="Inloop gasten",
                   set_values={"0": "20:45"}, source=SRC)
    assert "expectation_failed" in rules_hit(guard, op, view)


def test_editing_a_vanished_row_is_refused(guard, view):
    op = UpdateRow(row_id="t0r99", expect_contains="x", set_values={"0": "y"}, source=SRC)
    assert "unknown_row" in rules_hit(guard, op, view)


# --- her deletions are permanent -------------------------------------------
def test_a_row_she_deleted_cannot_come_back(guard, view):
    tombs = {"abc123": {
        "key": "21:30 hervatting show (nog opvragen bij rogier)",
        "longest": "hervatting show (nog opvragen bij rogier)",
        "removed_at": "2026-09-15T12:00:00+00:00", "section": "Tijdschema",
    }}
    op = add(values=["21:30", "Hervatting show (nog opvragen bij Rogier)", ""])
    assert "tombstone" in rules_hit(guard, op, view, tombs)


def test_tombstone_matches_even_if_the_time_column_differs(guard, view):
    """She deletes a row; the agent re-adds it with a different time. Still no."""
    tombs = {"abc123": {"key": "23:00 na afloop / schoonmaak",
                        "longest": "na afloop / schoonmaak",
                        "removed_at": "2026-09-15T12:00:00+00:00", "section": "Tijdschema"}}
    op = add(values=["23:30", "Na afloop / schoonmaak", ""])
    assert "tombstone" in rules_hit(guard, op, view, tombs)


# --- advisory ---------------------------------------------------------------
def test_quote_boilerplate_warns_but_does_not_block(guard):
    v = guard.check([add(values=["", "Gehele verduistering mogelijk", ""])])
    assert [x.severity for x in v] == ["warn"]


def test_hedging_warns(guard):
    v = guard.check([add(values=["", "Technicus komt waarschijnlijk om 17:00", ""])])
    assert any(x.rule == "invented_hedging" for x in v)
