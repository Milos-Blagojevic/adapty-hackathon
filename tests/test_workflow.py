"""The workflow functions kept for the old pages (open_ticket, propose, approve, dismiss), on v2 Services.

v2 accept/reject live in test_v2_workflow.py. Migrated from the v1 tests: FakeAnthropic/Proposal replaced by
scripted FakeLLM steps and a stored Level 1 ticket; v1-only behaviour (explain stamps the profile, the
3-call MAX_CALLS cap with threads and sleeps) removed.
"""

import pytest

from resolve import db, workflow
from resolve.llm import FakeLLM
from tests.fakes import ACTIVE_WEEKLY, GRANT_URL, LIFETIME, PROFILE_URL, profile
from tests.fakes_v2 import CUID, call, http_calls, make_proposed, new_db, services, submit, triage_ticket


@pytest.fixture
def conn(tmp_path):
    return new_db(tmp_path)


def live(conn, steps=()):
    return services(conn, FakeLLM(steps))


def test_open_fetches_profile(http, conn):
    http.get(PROFILE_URL, json={"data": profile(access_levels=[ACTIVE_WEEKLY])})
    ticket = workflow.open_ticket(live(conn), "t-001")
    assert ticket["status"] in ("new", "triaged")
    assert ticket["profile"]["access_levels"][0]["store_product_id"] == "lumen_weekly"
    assert http.calls[0].request.headers["adapty-customer-user-id"] == "lumen_443814_00549"


def test_open_keeps_ticket_usable_when_adapty_fails(http, conn):
    http.get(PROFILE_URL, status=500, json={"errors": [], "error_code": "boom", "status_code": 500})
    ticket = workflow.open_ticket(live(conn), "t-001")
    assert ticket["status"] == "new" and ticket["profile"] is None
    assert "500" in ticket["profile_error"]


def test_propose_is_triage_and_caches(http, conn):
    http.get(PROFILE_URL, json={"data": profile(access_levels=[ACTIVE_WEEKLY])})
    fake = FakeLLM([call("get_profile", customer_user_id=CUID), submit(triage_ticket())])
    svc = services(conn, fake)
    workflow.propose(svc, "t-001")
    first = db.get_ticket(conn, "t-001")
    assert first["status"] == "proposed" and first["category"] == "charged_after_trial"
    made = len(fake.calls)
    workflow.propose(svc, "t-001")
    assert len(fake.calls) == made, "cached: no second call"


def test_approve_grants_once_and_marks_done(http, conn):
    http.get(PROFILE_URL, json={"data": profile(access_levels=[ACTIVE_WEEKLY])})
    http.post(GRANT_URL, json={"data": profile(access_levels=[dict(ACTIVE_WEEKLY, store="adapty", expires_at="2026-11-16T05:08:30+00:00")])})
    http.patch(PROFILE_URL, json={"data": profile(access_levels=[ACTIVE_WEEKLY], attrs={"last_support_ticket": "t-001"})})
    make_proposed(conn)  # option 0: grant_access 3 days
    svc = live(conn)

    workflow.approve(svc, "t-001")
    done = db.get_ticket(conn, "t-001")
    assert done["status"] == "done" and done["action"]["type"] == "grant_access" and done["reply_sent_at"]
    assert len(http_calls(http, "POST")) == 1

    try:
        workflow.approve(svc, "t-001")
    except Exception:
        pass  # wrong status; the error type is not part of the contract
    assert db.get_ticket(conn, "t-001")["status"] == "done"
    assert len(http_calls(http, "POST")) == 1, "second approve never grants again"


def test_adapty_failure_never_marks_done(http, conn):
    http.get(PROFILE_URL, json={"data": profile(access_levels=[ACTIVE_WEEKLY])})
    http.post(GRANT_URL, status=400, json={"errors": [{"source": None, "errors": ["access level not found"]}], "error_code": "bad", "status_code": 400})
    make_proposed(conn)
    workflow.approve(live(conn), "t-001")
    ticket = db.get_ticket(conn, "t-001")
    assert ticket["status"] == "proposed"
    assert ticket["action"] is None and ticket["reply_sent_at"] is None
    assert "access level not found" in ticket["action_error"]
    assert http_calls(http, "PATCH") == [], "no stamp without a grant"


def test_approve_refuses_grant_when_profile_turned_lifetime(http, conn):
    http.get(PROFILE_URL, json={"data": profile(access_levels=[LIFETIME])})
    make_proposed(conn)
    workflow.approve(live(conn), "t-001")
    ticket = db.get_ticket(conn, "t-001")
    assert ticket["status"] == "proposed" and ticket["action_error"]
    assert http_calls(http, "POST") == []


def test_stamp_failure_keeps_the_grant_and_marks_done(http, conn):
    http.get(PROFILE_URL, json={"data": profile(access_levels=[ACTIVE_WEEKLY])})
    http.post(GRANT_URL, json={"data": profile(access_levels=[ACTIVE_WEEKLY])})
    http.patch(PROFILE_URL, status=500, json={"errors": None, "error_code": "odd", "status_code": 500})
    make_proposed(conn)
    svc = live(conn)
    workflow.approve(svc, "t-001")
    assert db.get_ticket(conn, "t-001")["status"] == "done"
    try:
        workflow.approve(svc, "t-001")
    except Exception:
        pass
    assert len(http_calls(http, "POST")) == 1


def test_demo_mode_touches_nothing(http, conn):
    make_proposed(conn, prof=profile(access_levels=[ACTIVE_WEEKLY]))
    demo = services(conn, FakeLLM([]), demo=True, adapty=None)
    workflow.approve(demo, "t-001")
    ticket = db.get_ticket(conn, "t-001")
    assert ticket["status"] == "done" and ticket["action"]["demo"] is True
    assert len(http.calls) == 0
    workflow.propose(demo, "t-002")
    assert db.get_ticket(conn, "t-002")["triage_error"], "demo: no model call, the ticket says why"


def test_late_rerun_cannot_revive_an_approved_ticket(http, conn):
    http.get(PROFILE_URL, json={"data": profile(access_levels=[ACTIVE_WEEKLY])})
    http.post(GRANT_URL, json={"data": profile(access_levels=[ACTIVE_WEEKLY])})
    http.patch(PROFILE_URL, json={"data": profile(access_levels=[ACTIVE_WEEKLY])})
    make_proposed(conn)
    svc = live(conn, [submit(triage_ticket(summary="late"))])
    workflow.approve(svc, "t-001")
    workflow.propose(svc, "t-001", force=True)
    after = db.get_ticket(conn, "t-001")
    assert after["status"] == "done"
    assert after["triage"]["summary"] != "late"
    assert len(http_calls(http, "POST")) == 1


def test_dismiss_from_any_open_state(conn):
    demo = services(conn, None, demo=True, adapty=None)
    workflow.dismiss(demo, "t-001")
    assert db.get_ticket(conn, "t-001")["status"] == "dismissed"
    db.update_ticket(conn, "t-002", status="done")
    workflow.dismiss(demo, "t-002")
    assert db.get_ticket(conn, "t-002")["status"] == "done"


def test_open_does_not_untriage_a_dismissed_ticket_and_dismiss_waits_for_approve(http, conn):
    http.get(PROFILE_URL, json={"data": profile(access_levels=[ACTIVE_WEEKLY])})
    svc = live(conn)
    db.update_ticket(conn, "t-001", status="dismissed")
    workflow.open_ticket(svc, "t-001")
    assert db.get_ticket(conn, "t-001")["status"] == "dismissed"
    db.update_ticket(conn, "t-002", status="approved")
    workflow.dismiss(svc, "t-002")
    assert db.get_ticket(conn, "t-002")["status"] == "approved"
    with pytest.raises(ValueError):
        db.update_ticket(conn, "t-002", only_status=(), status="new")
