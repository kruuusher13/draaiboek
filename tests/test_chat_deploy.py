"""Approving in the conversation instead of the workspace.

The workspace is one place a person can say yes. It is not what protects the
document -- the revision lock, the house rules and the required quote are. So a
yes given on Telegram writes the same way, under the same checks, and the
approval itself has to be quotable.
"""

import pytest
from fake_google import FakeGoogle
from fixtures import SCHEDULE

from draaiboek import proposals as P
from draaiboek.config import Config
from draaiboek.ops import AddRow, EditRequest, Source
from draaiboek.service import Draaiboek, RevisionConflict
from draaiboek.sources.base import Evidence

ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent
OPEN = [
    ["Open Punten", "", ""],
    ["ONDERWERP", "VRAAG", "OPMERKINGEN"],
    ["Plattegrond", "Ingetekende plattegrond ontvangen?", "Gevraagd op 13 sep"],
    ["", "", ""],
]
MAIL = Evidence(kind="email", ref="gmail:abc", title="Re: bruiloft", when="12 sep",
                body="Wij komen met 140 gasten. De garderobe graag open vanaf 19:15.",
                meta={"attachments": []})


def build(tmp_path, *, chat_deploy=True):
    cfg = Config(home=tmp_path, rules_dir=ROOT / "rules",
                 client_secret=tmp_path / "cs.json", token=tmp_path / "t.json",
                 template_doc_id="TPL", sandbox_doc_id="DOC1", drive_folder_id=None,
                 public_url="https://draaiboek.example", ui_key="sesame-sesame-sesame",
                 chat_deploy=chat_deploy)
    cfg.ensure_dirs()
    fake = FakeGoogle([SCHEDULE, OPEN], spans=[{0: 3, 5: 3}, {0: 3}])
    s = Draaiboek(cfg, fake)  # type: ignore[arg-type]
    s.fake = fake
    s.evidence.put(MAIL)
    return s


def two_edits():
    return [
        AddRow(section="Tijdschema", values=["19:15", "Garderobe open", ""],
               reason="Het bruidspaar wil de garderobe vanaf 19:15 open.",
               source=Source(kind="email", ref="gmail:abc",
                             quote="garderobe graag open vanaf 19:15")),
        AddRow(section="Tijdschema", values=["", "140 gasten verwacht", ""],
               reason="Aantal gasten uit de mail van 12 september.",
               source=Source(kind="email", ref="gmail:abc",
                             quote="Wij komen met 140 gasten")),
    ]


def propose(svc):
    view, _ = svc.read("DOC1")
    return svc.propose(EditRequest(doc_id="DOC1", expected_revision=view.revision_id,
                                   edits=two_edits(), note="test"), title="Bruiloft")


def rows(svc):
    return [c for r in svc.fake.tables[0] for c in r]


# -- the happy path --------------------------------------------------------

def test_a_yes_in_chat_writes_the_document(tmp_path):
    svc = build(tmp_path)
    pid = propose(svc)["proposal_id"]
    assert "Garderobe open" not in rows(svc), "nothing is written by proposing"

    out = svc.deploy_from_chat(pid, "Romir Malik (Telegram)", "ja doe maar, zet er maar in")

    assert out["applied"] == 2
    assert "Garderobe open" in rows(svc)
    assert svc.proposals.get(pid)["status"] == P.APPLIED


def test_part_of_a_proposal_can_be_deployed(tmp_path):
    svc = build(tmp_path)
    pid = propose(svc)["proposal_id"]

    svc.deploy_from_chat(pid, "Romir", "alleen de garderobe-regel graag", include=[0])

    flat = rows(svc)
    assert "Garderobe open" in flat
    assert "140 gasten verwacht" not in flat
    assert svc.proposals.get(pid)["included"] == [0]


def test_the_approval_is_recorded_with_who_and_their_words(tmp_path):
    svc = build(tmp_path)
    pid = propose(svc)["proposal_id"]
    svc.deploy_from_chat(pid, "Romir Malik (Telegram)", "ja doe maar")

    approved = [e for e in svc.ledger.entries("DOC1", 100) if e.get("event") == "approved"]
    assert len(approved) == 1
    assert approved[0]["by"] == "Romir Malik (Telegram)"
    assert approved[0]["quote"] == "ja doe maar"
    assert approved[0]["channel"] == "chat"


# -- what it refuses -------------------------------------------------------

def test_it_is_off_unless_switched_on(tmp_path):
    svc = build(tmp_path, chat_deploy=False)
    pid = propose(svc)["proposal_id"]
    with pytest.raises(PermissionError):
        svc.deploy_from_chat(pid, "Romir", "ja doe maar")
    assert "Garderobe open" not in rows(svc)


def test_an_approval_that_cannot_be_quoted_is_not_an_approval(tmp_path):
    svc = build(tmp_path)
    pid = propose(svc)["proposal_id"]
    with pytest.raises(ValueError, match="approval_quote"):
        svc.deploy_from_chat(pid, "Romir", "")
    assert "Garderobe open" not in rows(svc)


def test_a_nameless_approver_is_refused(tmp_path):
    svc = build(tmp_path)
    pid = propose(svc)["proposal_id"]
    with pytest.raises(ValueError, match="approved_by"):
        svc.deploy_from_chat(pid, "   ", "ja doe maar")


def test_a_document_edited_since_the_proposal_is_a_conflict_not_an_overwrite(tmp_path):
    """Her edits win by mechanism, on this path too."""
    svc = build(tmp_path)
    pid = propose(svc)["proposal_id"]
    svc.fake.rev += 1  # somebody edited the document in Google Docs

    with pytest.raises(RevisionConflict):
        svc.deploy_from_chat(pid, "Romir", "ja doe maar")
    assert "Garderobe open" not in rows(svc)


def test_a_proposal_cannot_be_deployed_twice(tmp_path):
    svc = build(tmp_path)
    pid = propose(svc)["proposal_id"]
    svc.deploy_from_chat(pid, "Romir", "ja prima")
    with pytest.raises(ValueError, match="not open"):
        svc.deploy_from_chat(pid, "Romir", "ja nog een keer")


def test_a_rejected_proposal_cannot_be_deployed(tmp_path):
    svc = build(tmp_path)
    pid = propose(svc)["proposal_id"]
    svc.reject_proposal(pid, "catering klopt niet")
    with pytest.raises(ValueError, match="not open"):
        svc.deploy_from_chat(pid, "Romir", "ja doe maar")


def test_deploying_nothing_is_refused(tmp_path):
    svc = build(tmp_path)
    pid = propose(svc)["proposal_id"]
    with pytest.raises(ValueError, match="nothing to deploy"):
        svc.deploy_from_chat(pid, "Romir", "ja doe maar", include=[])


def test_an_unknown_proposal_is_refused(tmp_path):
    svc = build(tmp_path)
    with pytest.raises(ValueError, match="No proposal"):
        svc.deploy_from_chat("deadbeefcafe", "Romir", "ja doe maar")
