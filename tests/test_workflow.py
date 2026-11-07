import json

import pytest

from resolve import db, workflow
from resolve.adapty import Client
from resolve.workflow import Services
from tests.fakes import ACTIVE_WEEKLY, GRANT_URL, LIFETIME, PROFILE_URL, FakeAnthropic, profile, proposal, ticket_row, tickets_file


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "t.sqlite")
    db.seed(connection, tickets_file(tmp_path, [ticket_row(), ticket_row(id="t-002", customer="lumen_443814_00042")]))
    return connection


def live(conn, outputs=()):
    return Services(conn=conn, demo=False, adapty=Client("sk"), anthropic=FakeAnthropic(outputs), model="m")


def test_open_fetches_profile_and_triages(http, conn):
    http.get(PROFILE_URL, json={"data": profile(access_levels=[ACTIVE_WEEKLY])})
    ticket = workflow.open_ticket(live(conn), "t-001")
    assert ticket["status"] == "triaged"
    assert ticket["profile"]["access_levels"][0]["store_product_id"] == "lumen_weekly"
    assert http.calls[0].request.headers["adapty-customer-user-id"] == "lumen_443814_00549"


def test_open_keeps_ticket_usable_when_adapty_fails(http, conn):
    http.get(PROFILE_URL, status=500, json={"errors": [], "error_code": "boom", "status_code": 500})
    ticket = workflow.open_ticket(live(conn), "t-001")
    assert ticket["status"] == "new" and ticket["profile"] is None
    assert "500" in ticket["profile_error"]


def test_propose_caches_and_counts_calls(http, conn):
    http.get(PROFILE_URL, json={"data": profile(access_levels=[ACTIVE_WEEKLY])})
    services = live(conn, [proposal(), proposal(action="explain")])
    workflow.open_ticket(services, "t-001")
    first = workflow.propose(services, "t-001")
    assert first["status"] == "proposed" and first["category"] == "charged_after_trial" and first["model_calls"] == 1
    again = workflow.propose(services, "t-001")
    assert again["proposal"] == first["proposal"] and again["model_calls"] == 1, "cached: no second call"
    forced = workflow.propose(services, "t-001", force=True)
    assert forced["proposal"]["action"] == "explain" and forced["model_calls"] == 2


def test_approve_grants_once_and_marks_done(http, conn):
    http.get(PROFILE_URL, json={"data": profile(access_levels=[ACTIVE_WEEKLY])})
    http.post(GRANT_URL, json={"data": profile(access_levels=[dict(ACTIVE_WEEKLY, store="adapty", expires_at="2026-11-20T05:08:30+00:00")])})
    http.patch(PROFILE_URL, json={"data": profile(access_levels=[dict(ACTIVE_WEEKLY, store="adapty", expires_at="2026-11-20T05:08:30+00:00")], attrs={"last_support_ticket": "t-001"})})
    services = live(conn, [proposal()])
    workflow.open_ticket(services, "t-001")
    workflow.propose(services, "t-001")

    done = workflow.approve(services, "t-001")
    assert done["status"] == "done"
    assert done["action"]["type"] == "grant_access"
    assert done["action"]["expires_at"] == "2026-11-20T05:08:30Z", "current expiry + 7 days, from the fresh profile"
    assert done["action"]["stamped"] == {"last_support_ticket": "t-001"}
    assert done["reply_sent_at"]
    assert json.loads(http.calls[-2].request.body)["expires_at"] == "2026-11-20T05:08:30Z"
    grants = [c for c in http.calls if c.request.method == "POST"]
    assert len(grants) == 1

    again = workflow.approve(services, "t-001")
    assert again["status"] == "done"
    assert len([c for c in http.calls if c.request.method == "POST"]) == 1, "second approve never grants again"


def test_adapty_failure_never_marks_done(http, conn):
    http.get(PROFILE_URL, json={"data": profile(access_levels=[ACTIVE_WEEKLY])})
    http.post(GRANT_URL, status=400, json={"errors": [{"source": None, "errors": ["access level not found"]}], "error_code": "bad", "status_code": 400})
    services = live(conn, [proposal()])
    workflow.open_ticket(services, "t-001")
    workflow.propose(services, "t-001")
    ticket = workflow.approve(services, "t-001")
    assert ticket["status"] == "proposed"
    assert ticket["action"] is None and ticket["reply_sent_at"] is None
    assert "access level not found" in ticket["action_error"]
    assert len([c for c in http.calls if c.request.method == "PATCH"]) == 0, "no stamp without a grant"


def test_approve_refuses_grant_when_profile_turned_lifetime(http, conn):
    http.get(PROFILE_URL, json={"data": profile(access_levels=[ACTIVE_WEEKLY])})
    services = live(conn, [proposal()])
    workflow.open_ticket(services, "t-001")
    workflow.propose(services, "t-001")
    http.replace(http.GET, PROFILE_URL, json={"data": profile(access_levels=[LIFETIME])})
    ticket = workflow.approve(services, "t-001")
    assert ticket["status"] == "proposed" and "lifetime" in ticket["action_error"]
    assert not [c for c in http.calls if c.request.method == "POST"]


def test_explain_action_stamps_but_never_grants(http, conn):
    http.get(PROFILE_URL, json={"data": profile(access_levels=[ACTIVE_WEEKLY])})
    http.patch(PROFILE_URL, json={"data": profile(access_levels=[ACTIVE_WEEKLY], attrs={"last_support_ticket": "t-001"})})
    services = live(conn, [proposal(category="cancel_how", action="explain")])
    workflow.open_ticket(services, "t-001")
    workflow.propose(services, "t-001")
    ticket = workflow.approve(services, "t-001")
    assert ticket["status"] == "done" and ticket["action"]["type"] == "explain"
    assert [c.request.method for c in http.calls] == ["GET", "PATCH"]


def test_demo_mode_touches_nothing(http, conn):
    db.update_ticket(conn, "t-001", profile_json=json.dumps(profile(access_levels=[ACTIVE_WEEKLY])), proposal_json=proposal().model_dump_json(), category="charged_after_trial", status="proposed")
    services = Services(conn=conn, demo=True)
    ticket = workflow.approve(services, "t-001")
    assert ticket["status"] == "done" and ticket["action"]["demo"] is True
    assert len(http.calls) == 0
    assert workflow.propose(services, "t-002")["proposal_error"].startswith("Demo mode")


def test_dismiss_from_any_open_state(conn):
    services = Services(conn=conn, demo=True)
    assert workflow.dismiss(services, "t-001")["status"] == "dismissed"
    db.update_ticket(conn, "t-002", status="done")
    assert workflow.dismiss(services, "t-002")["status"] == "done"
