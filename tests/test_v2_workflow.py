"""workflow.accept / workflow.reject on Level 1 tickets (docs/agents.md, Internal interfaces)."""

from datetime import datetime, timezone

import pytest

from resolve import db, workflow
from resolve.adapty import parse_datetime
from resolve.models import Decision, OptionEdits, Rejection
from tests.fakes_v2 import (
    CUID,
    GRANT_URL,
    PROFILE_URL,
    all_grants,
    body,
    http_calls,
    lifetime_profile,
    mock_grant_ok,
    mock_profile,
    new_db,
    option,
    make_proposed,
    paid_profile,
    services,
    triage_ticket,
)

ERROR_400 = {"errors": [{"source": None, "errors": ["access level not found"]}], "error_code": "bad", "status_code": 400}


@pytest.fixture
def conn(tmp_path):
    connection = new_db(tmp_path)
    make_proposed(connection, "t-001", prof=paid_profile())
    return connection


def accept(conn, decision, demo=False):
    return workflow.accept(services(conn, demo=demo), "t-001", decision)


def ticket(conn):
    return db.get_ticket(conn, "t-001")


def test_accept_option_0_grants_and_marks_done(http, conn):
    mock_profile(http, paid_profile())
    mock_grant_ok(http)
    accept(conn, Decision(option=0))

    t = ticket(conn)
    assert t["status"] == "done"
    assert len(http_calls(http, "POST")) == 1
    assert t["decision"]["option"] == 0
    assert t["action"]["type"] == "grant_access" and t["action"]["days"] == 3
    assert t["reply_sent_at"] and not t["action_error"]
    (row,) = all_grants(conn)
    assert (row["source"], row["ref"], row["days"]) == ("l1", "t-001", 3)
    assert body(http_calls(http, "PATCH")[0])["custom_attributes"] == [{"key": "last_support_ticket", "value": "t-001"}]


def test_accept_twice_grants_once(http, conn):
    mock_profile(http, paid_profile())
    mock_grant_ok(http)
    accept(conn, Decision(option=0))
    try:
        accept(conn, Decision(option=0))
    except Exception:
        pass  # the API turns this into 409; the error type is not part of the contract
    assert ticket(conn)["status"] == "done"
    assert len(http_calls(http, "POST")) == 1 and len(all_grants(conn)) == 1


def test_accept_only_from_proposed(http, conn):
    db.update_ticket(conn, "t-001", status="new")
    mock_profile(http, paid_profile())
    mock_grant_ok(http)
    try:
        accept(conn, Decision(option=0))
    except Exception:
        pass
    assert ticket(conn)["status"] == "new"
    assert http_calls(http, "POST") == []


def test_accept_switching_to_reply_only_makes_no_adapty_call(http, conn):
    accept(conn, Decision(option=0, edits=OptionEdits(action="reply_only", reply="Here is what happened.")))
    t = ticket(conn)
    assert t["status"] == "done" and t["action"]["type"] == "reply_only"
    assert len(http.calls) == 0 and all_grants(conn) == []
    assert t["decision"]["edits"]["action"] == "reply_only"
    assert t["decision"]["edits"]["reply"] == "Here is what happened."


def test_accept_with_days_edit_grants_the_edited_days(http, conn):
    mock_profile(http, paid_profile())  # premium until 2030-01-01
    mock_grant_ok(http)
    accept(conn, Decision(option=0, edits=OptionEdits(days=5)))
    t = ticket(conn)
    assert t["status"] == "done" and t["action"]["days"] == 5
    assert parse_datetime(body(http_calls(http, "POST")[0])["expires_at"]) == datetime(2030, 1, 6, tzinfo=timezone.utc)
    assert all_grants(conn)[0]["days"] == 5


def test_accept_second_option(http, conn):
    accept(conn, Decision(option=1))
    t = ticket(conn)
    assert t["status"] == "done" and t["action"]["type"] == "reply_only" and t["decision"]["option"] == 1
    assert len(http.calls) == 0


def test_adapty_error_returns_to_proposed(http, conn):
    mock_profile(http, paid_profile())
    http.post(GRANT_URL, status=400, json=ERROR_400)
    accept(conn, Decision(option=0))
    t = ticket(conn)
    assert t["status"] == "proposed"
    assert "access level not found" in t["action_error"]
    assert t["reply_sent_at"] is None and all_grants(conn) == []


def test_lifetime_at_accept_time_returns_to_proposed(http, conn):
    mock_profile(http, lifetime_profile())
    accept(conn, Decision(option=0))
    t = ticket(conn)
    assert t["status"] == "proposed" and t["action_error"]
    assert http_calls(http, "POST") == []


def test_retry_after_adapty_error_can_succeed(http, conn):
    mock_profile(http, paid_profile())
    http.post(GRANT_URL, status=400, json=ERROR_400)
    accept(conn, Decision(option=0))
    http.replace(http.POST, GRANT_URL, json={"data": paid_profile()})
    http.patch(PROFILE_URL, json={"data": paid_profile()})
    accept(conn, Decision(option=0))
    assert ticket(conn)["status"] == "done" and len(all_grants(conn)) == 1


def test_option_out_of_range_is_an_error(http, conn):
    """The ticket has two options. Raise or error result: not pinned; nothing may run or change."""
    try:
        accept(conn, Decision(option=2))
    except Exception:
        pass
    t = ticket(conn)
    assert t["status"] == "proposed" and t["action"] is None and t["decision"] is None
    assert len(http.calls) == 0


def test_three_options_accept_the_third(http, conn):
    make_proposed(conn, "t-001", ticket=triage_ticket(options=[option("grant_access", days=3), option("reply_only"), option("ask_user", question="Which store?")]))
    accept(conn, Decision(option=2))
    assert ticket(conn)["action"]["type"] == "ask_user" and ticket(conn)["status"] == "done"


def test_reject_with_reply_is_done(http, conn):
    workflow.reject(services(conn), "t-001", Rejection(note="wrong diagnosis", reply="We looked into it: you were not charged."))
    t = ticket(conn)
    assert t["status"] == "done" and t["reply_sent_at"]
    assert len(http.calls) == 0


def test_reject_without_reply_is_dismissed(http, conn):
    workflow.reject(services(conn), "t-001", Rejection(note="spam"))
    t = ticket(conn)
    assert t["status"] == "dismissed" and not t["reply_sent_at"]
    assert len(http.calls) == 0


def test_demo_mode_accept_touches_nothing(http, conn):
    accept(conn, Decision(option=0), demo=True)
    t = ticket(conn)
    assert t["status"] == "done" and t["action"]["demo"] is True
    assert len(http.calls) == 0 and all_grants(conn) == []


def test_approve_is_accept_option_0(http, conn):
    mock_profile(http, paid_profile())
    mock_grant_ok(http)
    workflow.approve(services(conn), "t-001")
    t = ticket(conn)
    assert t["status"] == "done" and t["decision"]["option"] == 0 and t["action"]["type"] == "grant_access"
    assert all_grants(conn)[0]["customer_user_id"] == CUID
