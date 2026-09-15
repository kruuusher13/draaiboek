from draaiboek.reader import parse_document
from fixtures import make_doc


def test_classifies_bands_headers_and_data(view):
    kinds = {r.row_id: r.kind for r in view.tables[0].rows}
    assert kinds["t0r0"] == "band"
    assert kinds["t0r1"] == "header"
    assert kinds["t0r2"] == "data"
    assert kinds["t0r5"] == "band"


def test_sections_own_their_rows(view):
    tijd = view.section_by_name("Tijdschema")
    assert tijd is not None
    assert tijd.row_ids == ["t0r2", "t0r3", "t0r4"]
    bijz = view.section_by_name("Bijzonderheden")
    assert bijz.row_ids == ["t0r6"]


def test_rows_carry_their_section(view):
    assert view.row("t0r3").section == "Tijdschema"
    assert view.row("t0r6").section == "Bijzonderheden"


def test_indexes_every_table_not_just_the_first():
    """The old DocMap only saw the main schedule table; extra overview tables
    raised UnknownSection. Here they are ordinary tables."""
    d = make_doc([
        [["Tijdschema", "", ""], ["Tijd", "Activiteit", "Opmerkingen"], ["19:30", "Inloop", ""]],
        [["Catering Overzicht", "", ""], ["Aantal", "Wat", "Opmerkingen"], ["40", "Broodjes", ""]],
    ], spans=[{0: 3}, {0: 3}])
    v = parse_document(d)
    assert len(v.tables) == 2
    assert v.section_by_name("Catering Overzicht") is not None
    assert v.row("t1r2").values == ["40", "Broodjes", ""]


def test_call_sheet_role_detected():
    d = make_doc([[["Contacten", "", ""], ["Naam", "Telefoon", "Overige info"],
                   ["Friso", "0612", "vanaf 17:00"]]], spans=[{0: 3}])
    v = parse_document(d)
    assert v.tables[0].role == "call_sheet"


def test_revision_is_surfaced(view):
    assert view.revision_id == "rev-1"


def test_dates_are_found_however_they_are_typed():
    from datetime import date
    from draaiboek.sources.dates import find_date, mentions
    today = date(2026, 9, 15)
    for q in ["26th september", "26 september", "26e september", "september 26", "Sept 26th",
              "26 sep 2026", "26/9", "26-09-2026", "2026/09/26", "bruiloft 26th of september"]:
        assert find_date(q, today)[0] == date(2026, 9, 26), q
    assert find_date("Lisa-Lynde 26th september", today)[1] == "Lisa-Lynde"
    assert find_date("QU-1204", today)[0] is None
    assert find_date("19:30", today)[0] is None
    assert mentions("2026/09/26 · Lisa-Lynde & Ivar Leeuwen", date(2026, 9, 26))
    assert not mentions("2026/09/27 · Lotte", date(2026, 9, 26))


def test_gmail_searches_every_way_a_date_is_written():
    from draaiboek.sources.gmail import Gmail
    subject, anywhere = Gmail._queries("Lisa 26th september")
    assert subject.startswith("subject:(") and '"26 september"' in anywhere
    assert '"2026/09/26"' in anywhere and anywhere.endswith("Lisa")
    assert Gmail._queries("Lisa-Lynde") == ["Lisa-Lynde"]
