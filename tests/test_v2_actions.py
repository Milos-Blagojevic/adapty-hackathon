"""actions.execute: the only place that writes to Adapty for an action (docs/agents.md, Internal interfaces)."""

from datetime import datetime, timedelta, timezone

import pytest

from resolve import actions, adapty
from resolve.adapty import AdaptyError, parse_datetime
from resolve.models import ACTIONS
from tests.fakes_v2 import (
    CUID,
    EXPIRED,
    GRANT_URL,
    PROFILE_URL,
    all_grants,
    body,
    customer_headers,
    http_calls,
    lifetime_profile,
    mock_grant_ok,
    mock_profile,
    new_db,
    paid_profile,
    services,
)

ERROR_400 = {"errors": [{"source": None, "errors": ["access level not found"]}], "error_code": "bad", "status_code": 400}
ERROR_500 = {"errors": [], "error_code": "boom", "status_code": 500}


@pytest.fixture
def conn(tmp_path):
    return new_db(tmp_path)


def test_grant_reads_a_fresh_profile_and_extends_the_current_expiry(http, conn):
    mock_profile(http, paid_profile())  # premium until 2030-01-01
    mock_grant_ok(http)
    result = actions.execute(services(conn), CUID, "grant_access", {"days": 3}, source="l1", ref="t-001")

    assert [c.request.method for c in http.calls] == ["GET", "POST", "PATCH"], "fresh profile, grant, stamp"
    assert customer_headers(http) == {CUID}
    assert result["type"] == "grant_access" and result["days"] == 3
    assert parse_datetime(result["expires_at"]) == datetime(2030, 1, 4, tzinfo=timezone.utc)

    grant = body(http_calls(http, "POST")[0])
    assert grant["access_level_id"] == "premium"
    assert parse_datetime(grant["expires_at"]) == datetime(2030, 1, 4, tzinfo=timezone.utc)

    stamp = body(http_calls(http, "PATCH")[0])
    assert stamp["custom_attributes"] == [{"key": "last_support_ticket", "value": "t-001"}]


def test_grant_for_expired_access_starts_now(http, conn):
    mock_profile(http, paid_profile(access_levels=[EXPIRED]))
    mock_grant_ok(http)
    before = datetime.now(timezone.utc)
    result = actions.execute(services(conn), CUID, "grant_access", {"days": 5}, source="l2", ref="s-1")
    after = datetime.now(timezone.utc)
    expires = parse_datetime(result["expires_at"])
    assert before + timedelta(days=5) - timedelta(seconds=1) <= expires <= after + timedelta(days=5) + timedelta(seconds=1)


def test_grant_is_recorded_with_source_and_ref(http, conn):
    mock_profile(http, paid_profile())
    mock_grant_ok(http)
    actions.execute(services(conn), CUID, "grant_access", {"days": 3}, source="l2", ref="s-42")
    (row,) = all_grants(conn)
    assert (row["customer_user_id"], row["access_level"], row["days"], row["source"], row["ref"]) == (CUID, "premium", 3, "l2", "s-42")
    assert parse_datetime(row["expires_at"]) == datetime(2030, 1, 4, tzinfo=timezone.utc)


def test_stamp_failure_is_not_fatal(http, conn):
    mock_profile(http, paid_profile())
    http.post(GRANT_URL, json={"data": paid_profile()})
    http.patch(PROFILE_URL, status=500, json=ERROR_500)
    result = actions.execute(services(conn), CUID, "grant_access", {"days": 3}, source="l1", ref="t-001")
    assert result["type"] == "grant_access"
    assert parse_datetime(result["expires_at"]) == datetime(2030, 1, 4, tzinfo=timezone.utc)
    assert len(all_grants(conn)) == 1, "the grant happened, so it is recorded"
    assert len(http_calls(http, "POST")) == 1


def test_grant_failure_raises_and_records_nothing(http, conn):
    mock_profile(http, paid_profile())
    http.post(GRANT_URL, status=400, json=ERROR_400)
    with pytest.raises(AdaptyError):
        actions.execute(services(conn), CUID, "grant_access", {"days": 3}, source="l1", ref="t-001")
    assert all_grants(conn) == []
    assert http_calls(http, "PATCH") == [], "no stamp without a grant"


def test_profile_failure_raises_and_never_grants(http, conn):
    http.get(PROFILE_URL, status=500, json=ERROR_500)
    with pytest.raises(AdaptyError):
        actions.execute(services(conn), CUID, "grant_access", {"days": 3}, source="l1", ref="t-001")
    assert http_calls(http, "POST") == [] and all_grants(conn) == []


def test_lifetime_raises_adapty_error_and_never_grants(http, conn):
    mock_profile(http, lifetime_profile())
    with pytest.raises(AdaptyError):
        actions.execute(services(conn), CUID, "grant_access", {"days": 3}, source="l1", ref="t-001")
    assert http_calls(http, "POST") == [] and all_grants(conn) == []


@pytest.mark.parametrize("action", [a for a in ACTIONS if a != "grant_access"])
def test_other_actions_make_no_adapty_call(http, conn, action):
    result = actions.execute(services(conn), CUID, action, {}, source="l1", ref="t-001")
    assert result["type"] == action
    assert len(http.calls) == 0
    assert all_grants(conn) == []


@pytest.mark.parametrize("demo,client", [(True, adapty.Client("sk")), (True, None), (False, None)], ids=["demo", "demo-no-client", "no-client"])
def test_demo_or_no_client_makes_no_call(http, conn, demo, client):
    result = actions.execute(services(conn, demo=demo, adapty=client), CUID, "grant_access", {"days": 3}, source="l1", ref="t-001")
    assert result["type"] == "grant_access" and result["demo"] is True
    assert len(http.calls) == 0
    assert all_grants(conn) == []
