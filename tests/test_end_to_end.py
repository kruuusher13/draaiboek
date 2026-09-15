"""The whole loop, against a simulated Google that enforces real index rules."""

import pytest
from fake_google import FakeGoogle
from fixtures import SCHEDULE

from draaiboek.config import Config
from draaiboek.ops import AddRow, EditRequest, RemoveRow, ReplaceText, Source, UpdateRow
from draaiboek.service import Draaiboek, GuardRefusal, RevisionConflict

SRC = Source(kind="larissa", ref="15 sep 2026", quote="deurbel en koffiemachines")
ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent


@pytest.fixture
def svc(tmp_path):
    cfg = Config(home=tmp_path, rules_dir=ROOT / "rules",
                 client_secret=tmp_path / "cs.json", token=tmp_path / "t.json",
                 template_doc_id="TPL", sandbox_doc_id="DOC1", drive_folder_id=None)
    cfg.ensure_dirs()
    fake = FakeGoogle([SCHEDULE], spans=[{0: 3, 5: 3}])
    s = Draaiboek(cfg, fake)  # type: ignore[arg-type]
    s.fake = fake
    return s


def rows(svc):
    return [r for r in svc.fake.tables[0]]


def apply(svc, edits, note=""):
    view, _ = svc.read("DOC1")
    return svc.apply(EditRequest(doc_id="DOC1", expected_revision=view.revision_id,
                                 edits=edits, note=note))


def add(values, **kw):
    return AddRow(section=kw.pop("section", "Tijdschema"), values=values, source=SRC, **kw)


# --- the happy path ---------------------------------------------------------
def test_appending_a_row_writes_it_in_the_right_place(svc):
    apply(svc, [add(["23:00", "Schoonmaak", ""])])
    assert rows(svc)[5] == ["23:00", "Schoonmaak", ""]
    assert rows(svc)[6][0] == "Bijzonderheden"   # her section did not move content


def test_three_rows_land_in_the_order_they_were_given(svc):
    apply(svc, [add(["21:00", "Pauze", ""]),
                add(["21:30", "Hervatting", ""]),
                add(["22:45", "Einde", ""])])
    assert [r[0] for r in rows(svc)[5:8]] == ["21:00", "21:30", "22:45"]


def test_updating_a_row_changes_only_that_row(svc):
    before = [list(r) for r in rows(svc)]
    apply(svc, [UpdateRow(row_id="t0r2", expect_contains="Inloop gasten",
                          set_values={"Tijd": "19:15", "Opmerkingen": "foyer open"},
                          source=SRC)])
    after = rows(svc)
    assert after[2] == ["19:15", "Inloop gasten", "foyer open"]
    assert after[3:] == before[3:]


def test_removing_a_row(svc):
    apply(svc, [RemoveRow(row_id="t0r3", expect_contains="Aanvang show",
                          reason="Larissa: staat dubbel")])
    assert not any("Aanvang show" in c for r in rows(svc) for c in r)


def test_insert_and_delete_in_one_batch(svc):
    apply(svc, [add(["23:00", "Schoonmaak", ""]),
                RemoveRow(row_id="t0r2", expect_contains="Inloop", reason="test")])
    flat = [c for r in rows(svc) for c in r]
    assert "Schoonmaak" in flat and "Inloop gasten" not in flat


def test_guest_count_is_changed_everywhere_not_once(svc):
    """'Ga door het hele draaiboek en pas het gasten aantal aan! Niet alleen
    1x maar consequent.' -- 13 sep 2026"""
    svc.fake.tables[0][2][2] = "191 gasten"
    svc.fake.tables[0][4][2] = "191 gasten vertrekken"
    apply(svc, [ReplaceText(find="191 gasten", replace="204 gasten", source=SRC)])
    assert rows(svc)[2][2] == "204 gasten"
    assert rows(svc)[4][2] == "204 gasten vertrekken"


# --- her edits win ----------------------------------------------------------
def test_a_write_against_a_stale_revision_is_refused(svc):
    view, _ = svc.read("DOC1")
    stale = view.revision_id
    svc.fake.hand_edit(0, set_cell=(2, 1, "Inloop gasten in foyer"))   # Larissa
    with pytest.raises(RevisionConflict):
        svc.apply(EditRequest(doc_id="DOC1", expected_revision=stale,
                              edits=[add(["23:00", "Schoonmaak", ""])]))
    assert rows(svc)[2][1] == "Inloop gasten in foyer"    # untouched


def test_a_refused_write_changes_nothing_at_all(svc):
    before = [list(r) for r in rows(svc)]
    with pytest.raises(GuardRefusal):
        apply(svc, [add(["", "Borrelplank € 12,50 p.p.", ""])])
    assert rows(svc) == before
    assert svc.fake.batches == []


def test_a_row_she_deletes_by_hand_becomes_permanently_protected(svc):
    svc.read("DOC1")                                   # baseline
    svc.fake.hand_edit(0, delete=3)                    # she deletes "Aanvang show"
    view, drift = svc.read("DOC1")                     # we notice
    assert {r["values"][1] for r in drift["removed"]} == {"Aanvang show"}

    with pytest.raises(GuardRefusal, match="edits are law"):
        svc.apply(EditRequest(doc_id="DOC1", expected_revision=view.revision_id,
                              edits=[add(["20:30", "Aanvang show", ""])]))


def test_a_row_we_removed_ourselves_is_not_tombstoned(svc):
    """Only her deletions are permanent. Ours were instructed."""
    apply(svc, [RemoveRow(row_id="t0r3", expect_contains="Aanvang show",
                          reason="Larissa vroeg dit")])
    view, _ = svc.read("DOC1")
    svc.apply(EditRequest(doc_id="DOC1", expected_revision=view.revision_id,
                          edits=[add(["20:45", "Aanvang show", ""])]))
    assert any("Aanvang show" in c for r in rows(svc) for c in r)


# --- the audit trail --------------------------------------------------------
def test_every_write_records_its_provenance(svc):
    apply(svc, [add(["23:00", "Schoonmaak", ""])], note="crew toevoegen")
    entry = [e for e in svc.ledger.entries("DOC1") if e["event"] == "applied"][-1]
    assert entry["note"] == "crew toevoegen"
    assert entry["provenance"][0]["source"]["quote"] == SRC.quote
    assert entry["revision_before"] != entry["revision_after"]


def test_refusals_are_recorded_too(svc):
    with pytest.raises(GuardRefusal):
        apply(svc, [add(["", "borrelplank € 12,50 p.p.", ""])])
    entry = svc.ledger.entries("DOC1")[-1]
    assert entry["event"] == "refused" and entry["reason"] == "guard"


def test_dry_run_writes_nothing_but_still_checks(svc):
    view, _ = svc.read("DOC1")
    res = svc.apply(EditRequest(doc_id="DOC1", expected_revision=view.revision_id,
                                edits=[add(["23:00", "Schoonmaak", ""])]), dry_run=True)
    assert res.applied == 0 and svc.fake.batches == []


# --- a fact must be supported by something we actually retrieved -------------
EMAIL = """From: Rogier Kalma <roos@voorbeeld.nl>
Date: Mon, 14 Sep 2026 09:12:00 +0200
Subject: Halve Oudejaarsconference 15 september

Hoi Larissa,

De zaal gaat om 19:30 open voor het publiek. De show begint om 20:30.
Onze host Sanne is er vanaf 19:00 voor de kaartjescontrole, 06-10000004.

Groet, Rogier
"""


def _gathered(svc):
    from draaiboek.sources.base import Evidence
    svc.evidence.put(Evidence(kind="email", ref="gmail:abc123",
                              title="Halve Oudejaarsconference", body=EMAIL))
    return Source(kind="email", ref="gmail:abc123",
                  quote="Onze host Sanne is er vanaf 19:00 voor de kaartjescontrole")


def test_a_quote_that_is_really_in_the_email_is_accepted(svc):
    src = _gathered(svc)
    view, _ = svc.read("DOC1")
    svc.apply(EditRequest(doc_id="DOC1", expected_revision=view.revision_id, edits=[
        AddRow(section="Tijdschema", values=["19:00", "Host Sanne — kaartjescontrole", ""],
               source=src)]))
    assert rows(svc)[5][1] == "Host Sanne — kaartjescontrole"


def test_a_quote_the_email_does_not_contain_is_refused(svc):
    """The exact failure mode from 15 Sep: '21:30 hervatting show' was written
    as fact and Larissa asked where it came from. It was invented."""
    from draaiboek.service import UnsupportedClaim
    _gathered(svc)
    invented = Source(kind="email", ref="gmail:abc123",
                      quote="de show hervat om 21:30 na de pauze")
    view, _ = svc.read("DOC1")
    with pytest.raises(UnsupportedClaim, match="does not appear"):
        svc.apply(EditRequest(doc_id="DOC1", expected_revision=view.revision_id, edits=[
            AddRow(section="Tijdschema", values=["21:30", "Hervatting show", ""],
                   source=invented)]))
    assert not any("Hervatting" in c for r in rows(svc) for c in r)


def test_citing_a_source_that_was_never_gathered_is_refused(svc):
    from draaiboek.service import UnsupportedClaim
    view, _ = svc.read("DOC1")
    with pytest.raises(UnsupportedClaim, match="No gathered evidence"):
        svc.apply(EditRequest(doc_id="DOC1", expected_revision=view.revision_id, edits=[
            AddRow(section="Tijdschema", values=["21:30", "Hervatting show", ""],
                   source=Source(kind="email", ref="gmail:made-up",
                                 quote="de show hervat om 21:30"))]))


def test_quote_matching_survives_email_reflowing(svc):
    """Mail clients rewrap lines; a quote must still match across the break."""
    _gathered(svc)
    src = Source(kind="email", ref="gmail:abc123",
                 quote="De zaal gaat om 19:30 open voor het publiek. De show begint om 20:30.")
    view, _ = svc.read("DOC1")
    svc.apply(EditRequest(doc_id="DOC1", expected_revision=view.revision_id, edits=[
        AddRow(section="Tijdschema", values=["19:30", "Zaal open", ""], source=src)]))
    assert rows(svc)[5][1] == "Zaal open"


def test_her_own_words_need_no_retrieval(svc):
    """She says it in chat; there is no email to quote."""
    view, _ = svc.read("DOC1")
    svc.apply(EditRequest(doc_id="DOC1", expected_revision=view.revision_id, edits=[
        AddRow(section="Tijdschema", values=["20:25", "Deurbel + koffiemachines uit", ""],
               source=Source(kind="larissa", ref="15 sep 2026",
                             quote="deurbel is goed om erin te houden"))]))
    assert rows(svc)[5][1] == "Deurbel + koffiemachines uit"


def _real_skeleton(svc):
    """Larissa's actual layout: one table per chapter."""
    from draaiboek.skeleton import blank, chapter_names
    tables = blank("test")
    svc.fake.tables = [[list(r) for r in t["rows"]] for t in tables]
    svc.fake.spans = [{int(k): v for k, v in (t.get("spans") or {}).items()} for t in tables]
    return chapter_names().index("Programma")


def test_column_headers_stay_above_the_rows_added_to_a_chapter(svc):
    """Regression: appending to a chapter that only had a column-header row
    anchored on the band, which pushed the header underneath the new rows."""
    ti = _real_skeleton(svc)
    apply(svc, [add(["19:00", "Ontvangst gasten", ""], section="Programma"),
                add(["20:30", "Aanvang voorstelling", ""], section="Programma")])
    rows = svc.fake.tables[ti]
    assert rows[0][0] == "Programma"        # band
    assert rows[1][0] == "TIJD"             # header directly under it
    assert rows[2][0] == "19:00"            # then our rows, in order
    assert rows[3][0] == "20:30"


def test_the_time_is_not_repeated_on_consecutive_rows(svc):
    """'I want her not to repeat time in the column of time... she has to leave
    it open, when it\'s in the same time' -- 13 Sep 2026."""
    ti = _real_skeleton(svc)
    res = apply(svc, [
        add(["19:00", "Ontvangst gasten in de Foyer", ""], section="Programma"),
        add(["19:00", "Bar open in de Foyer", ""], section="Programma"),
        add(["20:30", "Aanvang voorstelling", ""], section="Programma"),
    ])
    rows = svc.fake.tables[ti]
    assert [r[0] for r in rows[2:5]] == ["19:00", "", "20:30"]
    assert res.times_collapsed == 1


def test_a_missing_triade_delivery_is_flagged(svc):
    """'She has to warn me, if there isn\'t a delivery yet from Triade. Because
    we always have deliveries from Triade for every event.'"""
    _real_skeleton(svc)
    res = apply(svc, [add(["19:00", "Ontvangst", ""], section="Programma")])
    assert any("Triade" in r for r in res.reminders)


def test_no_triade_warning_once_the_delivery_is_there(svc):
    _real_skeleton(svc)
    res = apply(svc, [add(["09:00", "Triade", "stoelen theateropstelling"],
                          section="Leveringen")])
    assert not any("Triade" in r for r in res.reminders)


def test_an_update_below_an_insert_lands_on_the_right_row(svc):
    """Row ids are positions. Inserting a row above shifts everything under it,
    so an update planned on the old position must follow its row -- otherwise it
    overwrites the row above (this destroyed a cell in testing, 15 Sep 2026)."""
    apply(svc, [add(["19:45", "Garderobe open", ""], after_row_id="t0r2"),
                UpdateRow(row_id="t0r3", expect_contains="Aanvang show",
                          set_values={"Opmerkingen": "deurbel uit"}, source=SRC),
                RemoveRow(row_id="t0r4", expect_contains="Einde", reason="test")])
    rows = {r[1]: r for r in svc.fake.tables[0]}
    assert rows["Aanvang show"][2] == "deurbel uit"
    assert rows["Inloop gasten"][2] == "foyer"          # the row above is untouched
    assert rows["Garderobe open"][0] == "19:45"
    assert "Einde" not in rows
