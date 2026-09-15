import pytest
from fixtures import make_doc
from draaiboek.reader import parse_document
from draaiboek.config import Config
from draaiboek.service import Draaiboek
import pathlib
ROOT = pathlib.Path(__file__).resolve().parent.parent

def svc(tmp):
    cfg = Config(home=tmp, rules_dir=ROOT/"rules", client_secret=tmp/"c.json",
                 token=tmp/"t.json", template_doc_id=None, sandbox_doc_id=None,
                 drive_folder_id=None)
    cfg.ensure_dirs()
    return Draaiboek(cfg, object())

def doc(text):
    return parse_document(make_doc([[["Inrichting","",""],["RUIMTE","WAT","AANTAL"],
                                     ["Grote zaal", text, ""]]], spans=[{0:3}]))

def test_over_capacity_is_flagged(tmp_path):
    notes = svc(tmp_path)._capacity_notes(doc("300 gasten in theateropstelling"))
    assert any("meer dan Grote zaal aankan" in n or "225" in n for n in notes)

def test_threshold_triggers_extra_chairs(tmp_path):
    notes = svc(tmp_path)._capacity_notes(doc("180 gasten plenair"))
    assert any("Triade" in n for n in notes)

def test_already_ordered_is_not_nagged(tmp_path):
    notes = svc(tmp_path)._capacity_notes(doc("180 gasten plenair — 40 extra stoelen Triade besteld"))
    assert not any("Triade" in n for n in notes)

def test_a_normal_house_is_quiet(tmp_path):
    assert svc(tmp_path)._capacity_notes(doc("120 gasten")) == []

def test_times_are_not_read_as_guest_counts(tmp_path):
    assert svc(tmp_path)._capacity_notes(doc("19:30 inloop, 20:30 aanvang")) == []
