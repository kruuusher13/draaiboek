"""The workspace: Hermes proposes with reasons; Larissa answers, edits, deploys."""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest
from fake_google import FakeGoogle
from fixtures import SCHEDULE

from draaiboek import proposals as P
from draaiboek.config import Config
from draaiboek.ops import AddRow, EditRequest, Source, UpdateRow
from draaiboek.service import Draaiboek, GuardRefusal, RevisionConflict
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


@pytest.fixture
def svc(tmp_path):
    cfg = Config(home=tmp_path, rules_dir=ROOT / "rules",
                 client_secret=tmp_path / "cs.json", token=tmp_path / "t.json",
                 template_doc_id="TPL", sandbox_doc_id="DOC1", drive_folder_id=None,
                 public_url="https://draaiboek.example", ui_key="sesame-sesame-sesame")
    cfg.ensure_dirs()
    fake = FakeGoogle([SCHEDULE, OPEN], spans=[{0: 3, 5: 3}, {0: 3}])
    s = Draaiboek(cfg, fake)  # type: ignore[arg-type]
    s.fake = fake
    s.evidence.put(MAIL)
    return s


def flat(svc, t=0):
    return [c for r in svc.fake.tables[t] for c in r]


def hermes_edits():
    return [
        AddRow(section="Tijdschema", values=["19:15", "Garderobe open", ""],
               reason="Het bruidspaar wil de garderobe vanaf 19:15 open.",
               source=Source(kind="email", ref="gmail:abc", quote="garderobe graag open vanaf 19:15")),
        UpdateRow(row_id="t0r3", expect_contains="Aanvang show", set_values={"Opmerkingen": "deurbel uit"},
                  reason="Vaste regel bij voorstellingen.",
                  source=Source(kind="house_rule", ref="§3", quote="Deurbel + koffiemachines uit")),
    ]


def propose(svc, edits=None, **kw):
    view, _ = svc.read("DOC1")
    return svc.propose(EditRequest(doc_id="DOC1", expected_revision=view.revision_id,
                                   edits=edits or hermes_edits(), note="test"), **kw)


def deploy(svc, **kw):
    view, _ = svc.read("DOC1", record=False)
    return svc.deploy("DOC1", kw.pop("expected", view.revision_id), **kw)


# --- reading her layout --------------------------------------------------------
def test_every_chapter_header_of_the_blank_draaiboek_is_a_header():
    from draaiboek.local import LocalDocs
    from draaiboek.reader import parse_document
    from draaiboek.skeleton import blank
    import tempfile, pathlib
    ld = LocalDocs(pathlib.Path(tempfile.mkdtemp()))
    ld.create("x", "x", blank("x"))
    view = parse_document(ld.get_document("x"))
    assert all(t.rows[1].kind == "header" and t.headers for t in view.tables)


# --- proposing -------------------------------------------------------------------
def test_proposing_writes_nothing_and_links_to_the_workspace(svc):
    before = [list(r) for r in svc.fake.tables[0]]
    out = propose(svc, title="Bruiloft")
    assert svc.fake.tables[0] == before and svc.fake.batches == []
    assert out["workspace_url"] == f"https://draaiboek.example/?proposal={out['proposal_id']}"
    rec = svc.proposals.get(out["proposal_id"])
    assert rec["status"] == P.OPEN and rec["refs"] == ["gmail:abc"]
    assert rec["display"][0]["reason"] == "Het bruidspaar wil de garderobe vanaf 19:15 open."
    assert rec["display"][0]["context"] == ["22:30", "Einde", ""]


def test_a_proposal_without_reasons_is_refused(svc):
    edits = hermes_edits()
    edits[1].reason = ""
    with pytest.raises(ValueError):
        propose(svc, edits)
    assert svc.proposals.all() == []


def test_hermes_cannot_propose_into_her_section(svc):
    bad = [AddRow(section="Bijzonderheden", values=["", "notitie", ""], reason="x",
                  source=Source(kind="larissa", ref="15 sep", quote="zet dit erbij"))]
    with pytest.raises(GuardRefusal):
        propose(svc, bad)


def test_a_new_proposal_replaces_the_open_one(svc):
    first = propose(svc)["proposal_id"]
    second = propose(svc)["proposal_id"]
    assert svc.proposals.get(first)["status"] == P.SUPERSEDED
    assert svc.proposals.open_for("DOC1")["id"] == second


# --- deploying from the workspace ------------------------------------------------------
def test_she_keeps_some_changes_edits_answers_and_deploys_in_one_go(svc):
    pid = propose(svc)["proposal_id"]
    res = deploy(svc, proposal_id=pid, include=[1],
                 own=[{"type": "cell", "row_id": "t0r2", "values": {"2": "foyer, jassen aannemen"},
                       "expect": "19:30"},
                      {"type": "add", "after_row_id": "t0r4", "values": ["23:00", "Schoonmakers", ""]}],
                 answers=[{"question": "Plattegrond", "answer": "Komt vrijdag"}])
    assert res["applied"] == 3
    rows = svc.fake.tables[0]
    assert rows[2] == ["19:30", "Inloop gasten", "foyer, jassen aannemen"]
    assert rows[3][2] == "deurbel uit"
    assert "Garderobe open" not in flat(svc) and "Schoonmakers" in flat(svc)
    rec = svc.proposals.get(pid)
    assert rec["status"] == P.APPLIED and rec["included"] == [1] and rec["own_edits"] == 2
    assert svc.answers_for("DOC1")[0]["answer"] == "Komt vrijdag"


def test_her_answer_to_an_open_question_is_written_into_the_row(svc):
    deploy(svc, own=[{"type": "cell", "row_id": "t1r2", "values": {"2": "Antwoord: komt vrijdag"},
                      "expect": "Plattegrond"}])
    assert svc.fake.tables[1][2][2] == "Antwoord: komt vrijdag"


def test_she_may_write_in_her_own_section(svc):
    deploy(svc, own=[{"type": "cell", "row_id": "t0r6", "values": {"2": "bellen met DJ"},
                      "expect": "notities"}])
    assert svc.fake.tables[0][6][2] == "bellen met DJ"


def test_rows_she_deletes_in_the_workspace_are_protected_from_hermes(svc):
    deploy(svc, own=[{"type": "remove", "row_id": "t0r3", "expect": "Aanvang show"}])
    assert "Aanvang show" not in flat(svc)
    again = [AddRow(section="Tijdschema", values=["20:30", "Aanvang show", ""], reason="x",
                    source=Source(kind="larissa", ref="x", quote="aanvang show"))]
    with pytest.raises(GuardRefusal):
        propose(svc, again)


def test_house_rules_apply_to_her_edits_too(svc):
    view, _ = svc.read("DOC1", record=False)
    check = svc.check("DOC1", view.revision_id,
                      own=[{"type": "cell", "row_id": "t0r2", "values": {"2": "borg € 250"}, "expect": "19:30"}])
    assert not check["ok"]
    assert check["items"][0]["violations"][0]["rule"] == "financials"


def test_a_row_that_moved_under_her_is_refused_not_overwritten(svc):
    view, _ = svc.read("DOC1", record=False)
    check = svc.check("DOC1", view.revision_id,
                      own=[{"type": "cell", "row_id": "t0r2", "values": {"1": "x"}, "expect": "Einde"}])
    assert check["items"][0]["violations"][0]["rule"] == "expectation_failed"


def test_filling_a_blank_row_needs_it_to_still_be_blank(svc):
    deploy(svc, own=[{"type": "cell", "row_id": "t1r3", "values": {"0": "Parkeren", "1": "Spoorwegmuseum?"},
                      "expect": ""}])
    assert svc.fake.tables[1][3][:2] == ["Parkeren", "Spoorwegmuseum?"]
    view, _ = svc.read("DOC1", record=False)
    check = svc.check("DOC1", view.revision_id,
                      own=[{"type": "cell", "row_id": "t1r3", "values": {"2": "x"}, "expect": ""}])
    assert not check["ok"]


def test_two_changes_to_one_row_are_refused(svc):
    pid = propose(svc)["proposal_id"]
    view, _ = svc.read("DOC1", record=False)
    check = svc.check("DOC1", view.revision_id, proposal_id=pid, include=[1],
                      own=[{"type": "cell", "row_id": "t0r3", "values": {"1": "Show"}, "expect": "Aanvang"}])
    assert not check["ok"] and "Two changes target row t0r3" in check["errors"][0]


def test_an_edit_in_google_docs_meanwhile_wins(svc):
    pid = propose(svc)["proposal_id"]
    stale = svc.read("DOC1", record=False)[0].revision_id
    svc.fake.tables[0][2][2] = "foyer — Larissa"
    svc.fake.rev += 1
    with pytest.raises(RevisionConflict):
        svc.deploy("DOC1", stale, proposal_id=pid, include=[0, 1])
    assert "Garderobe open" not in flat(svc)
    assert svc.proposals.get(pid)["status"] == P.OPEN


def test_sending_back_keeps_her_note_for_hermes(svc):
    pid = propose(svc)["proposal_id"]
    rec = svc.reject_proposal(pid, "pauze is om 21:15")
    assert rec["status"] == P.REJECTED and svc.fake.batches == []
    assert svc.proposals.get(pid)["comment"] == "pauze is om 21:15"


# --- over HTTP -----------------------------------------------------------------------
@pytest.fixture
def server(svc):
    from draaiboek import ui
    handler = type("H", (ui.Handler,), {"svc": svc, "cache": ui._Cache()})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def call(url, body=None, headers=None, form=None):
    data = (json.dumps(body).encode() if body is not None else form.encode() if form else None)
    req = urllib.request.Request(url, data=data, headers=headers or {}, method="POST" if data else "GET")
    try:
        with urllib.request.build_opener(NoRedirect).open(req) as r:
            return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


POST = {"Content-Type": "application/json", "X-Draaiboek": "1"}


def test_the_workspace_opens_the_doc_with_the_proposal(server, svc):
    pid = propose(svc)["proposal_id"]
    assert call(f"{server}/")[0] == 200
    doc = json.loads(call(f"{server}/api/doc?doc=DOC1")[1])
    assert doc["proposal"]["id"] == pid and doc["proposal"]["stale"] is False
    assert doc["proposal"]["edits"][0]["quote_found"] is True
    assert doc["questions"][0]["row_id"] == "t1r2" and doc["questions"][0]["answer_col"] == 2
    assert [t["headers"] for t in doc["tables"]][1] == ["ONDERWERP", "VRAAG", "OPMERKINGEN"]


def test_deploy_over_http(server, svc):
    pid = propose(svc)["proposal_id"]
    rev = svc.read("DOC1", record=False)[0].revision_id
    body = {"doc_id": "DOC1", "expected_revision": rev, "proposal_id": pid, "include": [0, 1]}
    check = json.loads(call(f"{server}/api/check", body, POST)[1])
    assert check["ok"] is True
    code, out, _ = call(f"{server}/api/deploy", body, POST)
    assert code == 200 and json.loads(out)["applied"] == 2


def test_a_post_without_the_header_is_refused(server, svc):
    code, _, _ = call(f"{server}/api/deploy", {"doc_id": "DOC1"}, {"Content-Type": "application/json"})
    assert code == 400


def test_from_elsewhere_it_asks_for_the_key(server, svc):
    away = {"Host": "draaiboek.example"}
    code, _, headers = call(f"{server}/", headers=away)
    assert code == 303 and headers["Location"].startswith("/login")
    assert call(f"{server}/api/doc?doc=DOC1", headers=away)[0] == 401
    assert call(f"{server}/login", headers=away, form="key=wrong&next=/")[0] == 401
    code, _, headers = call(f"{server}/login", headers=away, form="key=sesame-sesame-sesame&next=/")
    assert code == 303
    cookie = headers["Set-Cookie"].split(";", 1)[0]
    assert call(f"{server}/api/doc?doc=DOC1", headers={**away, "Cookie": cookie})[0] == 200


def test_login_never_redirects_off_site(server, svc):
    code, _, headers = call(f"{server}/login", form="key=sesame-sesame-sesame&next=//evil.example")
    assert headers["Location"] == "/"


# --- sources ------------------------------------------------------------------------------
def test_gmail_separates_attached_files_from_signature_images():
    from draaiboek.sources.gmail import _is_inline, _walk
    payload = {"mimeType": "multipart/mixed", "parts": [
        {"mimeType": "text/plain", "body": {"data": "SGFsbG8="}},
        {"mimeType": "image/png", "filename": "logo.png", "body": {"attachmentId": "a", "size": 10},
         "headers": [{"name": "Content-ID", "value": "<x>"},
                     {"name": "Content-Disposition", "value": "inline"}]},
        {"mimeType": "application/pdf", "filename": "plattegrond.pdf",
         "body": {"attachmentId": "b", "size": 99},
         "headers": [{"name": "Content-Disposition", "value": "attachment; filename=x"}]},
    ]}
    plain, html, files = [], [], []
    _walk(payload, plain, html, files)
    assert plain == ["Hallo"]
    assert [_is_inline(f) for f in files] == [True, False]


def test_clickup_carries_attachments_and_comments_from_the_task_endpoint():
    from draaiboek.sources.clickup import ClickUp
    listed = ClickUp._to_evidence({"id": "1", "name": "t"})
    full = ClickUp._to_evidence({"id": "1", "name": "t", "_comments": [
        {"user": {"username": "Gavriel"}, "date": "1757940000000", "comment_text": "40 barkrukken"}],
        "attachments": [{"title": "rider.pdf", "mimetype": "application/pdf", "size": 5, "url": "https://x"},
                        {"title": "old.pdf", "deleted": True, "url": "https://x"}]})
    assert "attachments" not in listed.meta
    assert [a["filename"] for a in full.meta["attachments"]] == ["rider.pdf"]
    assert full.meta["comments"][0]["author"] == "Gavriel" and "40 barkrukken" in full.body


def test_missive_labels_name_the_event():
    from draaiboek.sources.missive import _labels
    conv = {"shared_label_names": "a, b", "shared_labels": [{"name": "2026/09/21 ·\xa0Rabobank lunch"}]}
    assert _labels(conv) == ["2026/09/21 · Rabobank lunch"]


def test_downloads_only_go_to_the_source_hosts():
    from draaiboek.sources.base import SourceError, download
    with pytest.raises(SourceError):
        download("https://evil.example/x.pdf", ("clickup-attachments.com",))
    with pytest.raises(SourceError):
        download("http://t1.p.clickup-attachments.com/x", ("clickup-attachments.com",))


def test_she_can_edit_the_rules_and_the_old_version_is_kept(svc, tmp_path):
    import shutil
    rules = tmp_path / "rules"
    shutil.copytree(ROOT / "rules", rules)
    object.__setattr__(svc.cfg, "rules_dir", rules)
    before = svc.house_rules()
    svc.save_house_rules(before + "\n- Altijd de garderobe noemen.\n")
    assert "Altijd de garderobe noemen." in svc.house_rules()
    kept = list((svc.cfg.home / "rules_history").glob("house_rules-*.md"))
    assert len(kept) == 1 and kept[0].read_text() == before
    with pytest.raises(ValueError):
        svc.save_house_rules("   ")
