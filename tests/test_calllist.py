"""The call list. "Ik wil ze kunnen bellen, met deze punten in het draaiboek
als to do lijst" -- Larissa, 15 Sep 2026."""

import pytest
from fixtures import make_doc

from draaiboek import calllist
from draaiboek.reader import parse_document

SCHEDULE = [
    ["Open Punten", "", ""],
    ["ONDERWERP", "VRAAG", "OPMERKINGEN"],
    ["Cateraar", "Neemt de cateraar zelf bestek en borden mee?", "nog geen antwoord"],
    ["Plattegrond", "Ingetekende plattegrond nog ontvangen van bruidspaar", ""],
    ["Offerte", "Akkoord ontvangen op 15 sep", "afgehandeld"],
]
CALLSHEET = [
    ["Call Sheet", "", "", ""],
    ["NAAM", "ROL", "TELEFOON", "OVERIGE INFO"],
    ["Marcel Smit", "Cateraar", "06-10000001", "levering vr 17:00"],
    ["Lisa de Vries", "Bruid", "nog opvragen", ""],
    ["Ivar Leeuwen", "Bruidegom", "nog opvragen", "ivar@voorbeeld.nl"],
]


@pytest.fixture
def view():
    return parse_document(make_doc([SCHEDULE, CALLSHEET],
                                   spans=[{0: 3}, {0: 4}]))


def by_name(out, name):
    return next((t for t in out if t["name"] == name), None)


def test_a_question_reaches_the_person_who_can_answer_it(view):
    out = calllist.build(view)
    cateraar = by_name(out, "Marcel Smit")
    assert cateraar["phone"] == "06-10000001"
    assert any("bestek en borden" in t["text"] for t in cateraar["tasks"])


def test_a_missing_number_is_itself_a_task(view):
    """She chases numbers more than anything else."""
    out = calllist.build(view)
    bride = by_name(out, "Lisa de Vries")
    assert bride["phone_pending"] is True
    assert any(t["kind"] == "nummer" for t in bride["tasks"])


def test_a_question_for_a_couple_goes_to_both_not_a_guess(view):
    out = calllist.build(view)
    pair = by_name(out, "Bruidspaar")
    assert pair is not None
    assert {m["name"] for m in pair["members"]} == {"Lisa de Vries", "Ivar Leeuwen"}
    assert any("plattegrond" in t["text"].lower() for t in pair["tasks"])


def test_an_answered_question_is_not_a_call_to_make(view):
    out = calllist.build(view)
    assert not any("Akkoord ontvangen" in t["text"]
                   for tgt in out for t in tgt["tasks"])


def test_contacts_are_not_mistaken_for_questions(view):
    """A call-sheet row whose phone says "nog opvragen" must not also surface
    as an open point -- it is already a number-chasing task."""
    out = calllist.build(view)
    for tgt in out:
        for t in tgt["tasks"]:
            assert "ivar@voorbeeld.nl" not in t["text"]


def test_two_people_in_one_role_do_not_get_each_others_questions():
    """A ceremony band and an evening band both match "band". Guessing puts the
    wrong question in front of the wrong person."""
    sheet = [
        ["Call Sheet", "", "", ""],
        ["NAAM", "ROL", "TELEFOON", "OVERIGE INFO"],
        ["Daan Dekker", "Bandleider ceremonie", "06-10000002", ""],
        ["Petra Molenaar", "Avondband Invictus", "06-10000003", ""],
    ]
    punten = [
        ["Open Punten", "", ""],
        ["ONDERWERP", "VRAAG", "OPMERKINGEN"],
        ["Rider", "Technische rider band nog ontvangen", ""],
    ]
    v = parse_document(make_doc([punten, sheet], spans=[{0: 3}, {0: 4}]))
    out = calllist.build(v)
    assert by_name(out, "Band / muziek") is not None
    assert by_name(out, "Daan Dekker") is None


def test_reminders_are_routed_to_the_supplier_they_concern(view):
    out = calllist.build(view, ["Er is sprake van een externe cateraar. Neem contact op."])
    assert any("externe cateraar" in t["text"]
               for t in by_name(out, "Marcel Smit")["tasks"])


def test_unplaceable_items_are_shown_not_dropped(view):
    """Silence about a loose end reads as 'nothing to do'."""
    out = calllist.build(view, ["Geen Triade-levering in het hoofdstuk Leveringen."])
    bucket = by_name(out, calllist.UNASSIGNED)
    assert bucket is not None and bucket["bucket"] is True
    assert any("Triade" in t["text"] for t in bucket["tasks"])


def test_callable_people_come_first(view):
    """She works the list top to bottom; the people she can actually ring now
    belong at the top, and the bucket she cannot ring at all at the bottom."""
    names = [t["name"] for t in calllist.build(view)]
    assert names[0] == "Marcel Smit"

    with_bucket = [t["name"] for t in
                   calllist.build(view, ["Geen Triade-levering in Leveringen."])]
    assert with_bucket[-1] == calllist.UNASSIGNED
    assert with_bucket[0] == "Marcel Smit"
