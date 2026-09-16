"""Recurring formats and supplier pack sizes — the arithmetic she does by hand."""

import pathlib

import pytest

from draaiboek.formats import Formats, identify, pack

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def f():
    return Formats(ROOT / "rules")


def test_eleven_metres_of_table(f):
    """'Bij Triade heb je 120 en 2 meter tafels. Ik heb 11 meter tafel nodig.
    Hoeveel van welke moet ik dan bestellen?' -- 13 sep 2026."""
    r = f.tables_for(11)
    assert r["lengths_available_m"] == [1.2, 2.0]
    best = r["options"][0]
    assert best["total_m"] >= 11          # never short
    assert best["pieces"] <= 6            # fewest pieces to carry


def test_an_order_is_never_short(f):
    for metres in (3, 7.5, 11, 24):
        for option in f.tables_for(metres)["options"]:
            assert option["total_m"] >= metres


def test_the_format_is_recognised_from_the_event_name(f):
    assert identify("2026/09/18 · FEVER An Idiot's Guide to Wine", f.formats) == "aigtw"
    assert identify("Bruiloft Lisa-Lynde & Ivan", f.formats) == "bruiloft"
    assert identify("Rabobank lunch + vergadering", f.formats) is None


def test_an_unknown_format_says_so_rather_than_inventing_a_standard(f):
    s = f.standard_for("Rabobank lunch + vergadering", 40)
    assert s["known"] is False and s["order"] == []
    assert "no standard" in s["note"].lower()


def test_quantities_follow_the_head_count(f):
    small = f.standard_for("An Idiot's Guide to Wine", 100)
    big = f.standard_for("An Idiot's Guide to Wine", 300)
    per_head = lambda s: [o for o in s["order"] if "borrelplank" in str(o["items"])]
    assert per_head(small)[0]["items"] != per_head(big)[0]["items"]


def test_thresholds_fire_on_the_head_count(f):
    r = f.standard_for("An Idiot's Guide to Wine", 319)["reminders"]
    assert any("Triade" in x for x in r)          # over 160
    assert any("koffiepunt" in x for x in r)      # over 175
    assert not f.standard_for("An Idiot's Guide to Wine", 80)["reminders"]


def test_the_standing_routine_travels_with_the_format(f):
    s = f.standard_for("An Idiot's Guide to Wine", 200)
    assert len(s["standard"]["interval_routine"]) == 7
    assert any("Glassex" in x for x in s["standard"]["interval_routine"])


def test_jazz_carries_its_prohibition(f):
    s = f.standard_for("FEVER Jazz · Sinatra", 150)
    assert "stoelen" in s["standard"]["never"].lower()
