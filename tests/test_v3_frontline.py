"""v3 Level 2: code prefetches the account and hides grant_access when no grant is possible (docs/agents.md, v3)."""

import json

import pytest

from resolve import db, frontline
from resolve.llm import FakeLLM
from tests.fakes_v2 import CUID, PROFILE_URL, lifetime_profile, mock_profile, new_db, paid_profile, say, services, unpaid_profile

SESSION = "s-v3"


@pytest.fixture
def conn(tmp_path):
    return new_db(tmp_path, seed=False)


def run(conn, steps, adapty="default"):
    fake = FakeLLM(steps)
    result = frontline.chat(services(conn, fake, adapty=adapty), CUID, SESSION, "I paid but premium is locked")
    return result, fake


def facts_of(fake: FakeLLM) -> dict | str:
    tail = fake.calls[0]["system"].split("Account facts (loaded by the system just now):\n", 1)[1]
    return tail if tail == "account: not found" else json.loads(tail)


def test_profile_is_in_the_prompt_before_the_first_call(http, conn):
    mock_profile(http, paid_profile())
    result, fake = run(conn, [say("Hi, I see your weekly plan.")])
    assert result.reply == "Hi, I see your weekly plan."
    facts = facts_of(fake)
    assert facts["free_days_available"] is True and facts["free_days_blocked_reason"] is None
    assert "grant_access" in fake.calls[0]["tools"]
    assert result.actions == [] and db.list_events(conn, SESSION) == []  # the prefetch is not a tool call


@pytest.mark.parametrize("prof, reason", [(unpaid_profile(), "not_paid"), (lifetime_profile(), "lifetime")])
def test_grant_tool_hidden_when_not_possible(http, conn, prof, reason):
    mock_profile(http, prof)
    _, fake = run(conn, [say("Let me help.")])
    assert "grant_access" not in fake.calls[0]["tools"]
    assert facts_of(fake)["free_days_blocked_reason"] == reason


def test_grant_tool_hidden_after_a_recent_grant(http, conn):
    mock_profile(http, paid_profile())
    db.record_grant(conn, CUID, "premium", 7, "2026-12-31T00:00:00Z", "l2", "older-session", {})
    _, fake = run(conn, [say("Let me help.")])
    assert "grant_access" not in fake.calls[0]["tools"]
    assert facts_of(fake)["free_days_blocked_reason"] == "grant_limit_30d"


def test_not_found_goes_to_the_model(http, conn):
    http.get(PROFILE_URL, status=404, json={"errors": [{"detail": "not found"}]})
    result, fake = run(conn, [say("I can't find your account.")])
    assert facts_of(fake) == "account: not found"
    assert "grant_access" not in fake.calls[0]["tools"]
    assert not result.handed_off


def test_account_down_hands_off_without_a_model_call(http, conn):
    http.get(PROFILE_URL, status=500, json={"errors": [{"detail": "boom"}]})
    result, fake = run(conn, [])
    assert fake.calls == []
    assert result.reply == frontline.ACCOUNT_DOWN_REPLY
    assert result.handed_off and result.ticket_id
    assert db.get_ticket(conn, result.ticket_id)["source"] == "chat_handoff"


def test_no_adapty_client_is_account_down(conn):
    result, fake = run(conn, [], adapty=None)
    assert fake.calls == [] and result.handed_off
