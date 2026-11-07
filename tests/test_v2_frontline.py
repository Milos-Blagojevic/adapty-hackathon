"""frontline.chat: the Level 2 loop, scripted with FakeLLM (docs/agents.md, Level 2 + Internal interfaces)."""

import pytest

from resolve import db, frontline
from resolve.llm import FakeLLM, LLMError
from resolve.models import ChatResult
from tests.fakes_v2 import (
    CUID,
    STRANGER,
    all_grants,
    call,
    customer_headers,
    http_calls,
    lifetime_profile,
    mock_grant_ok,
    mock_profile,
    new_db,
    paid_profile,
    say,
    services,
    set_config,
    tool_results,
)

SESSION = "s-1"
ALWAYS_ON = {"get_profile", "store_instructions", "handoff", "mark_resolved"}


@pytest.fixture
def conn(tmp_path):
    return new_db(tmp_path, seed=False)


def chat(conn, steps, message="I paid but premium is locked", session=SESSION, customer=CUID):
    fake = steps if isinstance(steps, FakeLLM) else FakeLLM(steps)
    result = frontline.chat(services(conn, fake), customer, session, message)
    return result, fake


def action(result: ChatResult, tool: str):
    found = [a for a in result.actions if a.tool == tool]
    assert found, f"no {tool} in {[a.tool for a in result.actions]}"
    return found


def grant_steps(days=3, reply="Done: I added a few days of premium."):
    return [call("get_profile"), call("grant_access", days=days, reason="paid but locked"), say(reply)]


def test_grant_within_limits_is_allowed_executed_and_logged(http, conn):
    mock_profile(http, paid_profile())
    mock_grant_ok(http)
    result, fake = chat(conn, grant_steps())

    assert isinstance(result, ChatResult)
    assert result.reply == "Done: I added a few days of premium."
    assert result.handed_off is False and result.ticket_id is None
    (grant,) = action(result, "grant_access")
    assert grant.allowed is True and grant.blocked_reason is None
    assert grant.args == {"days": 3, "reason": "paid but locked"}

    assert len(http_calls(http, "POST")) == 1
    (row,) = all_grants(conn)
    assert (row["days"], row["source"], row["ref"]) == (3, "l2", SESSION)

    events = db.list_events(conn, SESSION)
    assert [e["tool"] for e in events] == ["get_profile", "grant_access"], "every tool call is logged, in order"
    assert events[1]["allowed"] is True and events[1]["blocked_reason"] is None
    assert events[1]["args"] == {"days": 3, "reason": "paid but locked"}


def test_tools_offered_when_grant_enabled(http, conn):
    mock_profile(http, paid_profile())
    _, fake = chat(conn, [say("Hi!")])
    offered = set(fake.calls[0]["tools"])
    assert ALWAYS_ON | {"grant_access"} <= offered


def test_grant_tool_not_offered_when_disabled(http, conn):
    set_config(conn, tools={"grant_access": False})
    mock_profile(http, paid_profile())
    _, fake = chat(conn, [say("Hi!")])
    offered = set(fake.calls[0]["tools"])
    assert "grant_access" not in offered
    assert ALWAYS_ON <= offered


def test_grant_called_anyway_while_disabled_is_blocked(http, conn):
    set_config(conn, tools={"grant_access": False})
    mock_profile(http, paid_profile())
    mock_grant_ok(http)
    result, _ = chat(conn, grant_steps())
    assert http_calls(http, "POST") == [] and all_grants(conn) == []
    assert all(not a.allowed for a in result.actions if a.tool == "grant_access")


def test_grant_over_max_days_is_blocked(http, conn):
    mock_profile(http, paid_profile())
    mock_grant_ok(http)
    result, fake = chat(conn, grant_steps(days=30, reply="I can't do 30 days."))

    (grant,) = action(result, "grant_access")
    assert grant.allowed is False and grant.blocked_reason == "over_max_days"
    assert http_calls(http, "POST") == [] and all_grants(conn) == []
    (shown,) = tool_results(fake, "grant_access")
    assert shown["content"].startswith("blocked:") and "over_max_days" in shown["content"]
    event = [e for e in db.list_events(conn, SESSION) if e["tool"] == "grant_access"][0]
    assert event["allowed"] is False and event["blocked_reason"] == "over_max_days"


def test_max_days_follows_the_stored_config(http, conn):
    set_config(conn, grant={"max_days": 3})
    mock_profile(http, paid_profile())
    mock_grant_ok(http)
    result, _ = chat(conn, grant_steps(days=4))
    assert action(result, "grant_access")[0].blocked_reason == "over_max_days"
    assert http_calls(http, "POST") == []


def test_lifetime_user_is_blocked(http, conn):
    mock_profile(http, lifetime_profile())
    mock_grant_ok(http)
    result, fake = chat(conn, grant_steps())
    (grant,) = action(result, "grant_access")
    assert grant.allowed is False and grant.blocked_reason == "lifetime"
    assert http_calls(http, "POST") == []
    assert tool_results(fake, "grant_access")[0]["content"].startswith("blocked:")


def test_complaining_again_does_not_give_more_days(http, conn):
    """Default config: one grant per user per 30 days; the second message in the session is blocked."""
    mock_profile(http, paid_profile())
    mock_grant_ok(http)
    first, _ = chat(conn, grant_steps())
    assert action(first, "grant_access")[0].allowed is True
    second, _ = chat(conn, grant_steps(), message="still locked, give me more")
    (grant,) = action(second, "grant_access")
    assert grant.allowed is False and grant.blocked_reason in ("grant_limit_30d", "already_granted_this_session")
    assert len(http_calls(http, "POST")) == 1 and len(all_grants(conn)) == 1


def test_second_grant_in_the_same_session_is_blocked_even_under_the_30d_limit(http, conn):
    set_config(conn, grant={"max_grants_per_user_30d": 2})
    mock_profile(http, paid_profile())
    mock_grant_ok(http)
    chat(conn, grant_steps())
    second, _ = chat(conn, grant_steps(), message="one more please")
    assert action(second, "grant_access")[0].blocked_reason == "already_granted_this_session"
    assert len(http_calls(http, "POST")) == 1


def test_grant_from_another_channel_within_30_days_blocks(http, conn):
    db.record_grant(conn, CUID, "premium", 3, None, "l1", "t-001", {})
    mock_profile(http, paid_profile())
    mock_grant_ok(http)
    result, _ = chat(conn, grant_steps(), session="s-new")
    assert action(result, "grant_access")[0].blocked_reason == "grant_limit_30d"
    assert http_calls(http, "POST") == []


def test_grant_older_than_30_days_does_not_block(http, conn):
    db.record_grant(conn, CUID, "premium", 3, None, "l2", "s-old", {})
    with conn:
        conn.execute("UPDATE grants SET ts = '2026-01-01T00:00:00Z'")
    mock_profile(http, paid_profile())
    mock_grant_ok(http)
    result, _ = chat(conn, grant_steps(), session="s-new")
    assert action(result, "grant_access")[0].allowed is True
    assert len(http_calls(http, "POST")) == 1


def test_the_model_cannot_address_another_customer(http, conn):
    """The customer id is always the session's: extra arguments naming another user are ignored or refused."""
    mock_profile(http, paid_profile())
    mock_grant_ok(http)
    chat(conn, [
        call("get_profile", customer_user_id=STRANGER),
        call("grant_access", days=3, reason="x", customer_user_id=STRANGER),
        say("ok"),
    ])
    assert customer_headers(http) <= {CUID}
    assert db.grants_since(conn, STRANGER, "2000-01-01T00:00:00Z") == []


def test_handoff_creates_a_chat_ticket(http, conn):
    mock_profile(http, paid_profile())
    result, _ = chat(conn, [call("handoff", reason="refund", summary="Wants a refund for the annual plan"), say("I passed this to our team.")], message="refund me now")

    assert result.handed_off is True and result.ticket_id
    assert result.reply, "the user always gets a reply"
    ticket = db.get_ticket(conn, result.ticket_id)
    assert ticket["source"] == "chat_handoff"
    assert ticket["session_id"] == SESSION and ticket["customer_user_id"] == CUID
    assert ticket["status"] == "new", "the caller schedules triage"
    conversation = db.get_conversation(conn, SESSION)
    assert conversation["outcome"] == "handed_off" and conversation["ticket_id"] == result.ticket_id
    assert [e["tool"] for e in db.list_events(conn, SESSION)] == ["handoff"]
    assert action(result, "handoff")[0].allowed is True


def test_after_handoff_the_next_message_makes_no_model_call(http, conn):
    mock_profile(http, paid_profile())
    fake = FakeLLM([call("handoff", reason="asks_for_human", summary="wants a person"), say("A person will reply."), say("unused"), say("unused")])
    first, _ = chat(conn, fake, message="I want a human")
    made = len(fake.calls)
    second, _ = chat(conn, fake, message="hello?")
    assert len(fake.calls) == made
    assert isinstance(second, ChatResult) and second.reply
    assert db.get_conversation(conn, SESSION)["outcome"] == "handed_off"


def test_more_than_max_turns_user_messages_hands_off(http, conn):
    set_config(conn, max_turns=2)
    mock_profile(http, paid_profile())
    fake = FakeLLM([say("one"), say("two"), say("three")])
    assert chat(conn, fake, message="m1")[0].handed_off is False
    assert chat(conn, fake, message="m2")[0].handed_off is False
    third, _ = chat(conn, fake, message="m3")
    assert third.handed_off is True and third.ticket_id
    assert db.get_ticket(conn, third.ticket_id)["source"] == "chat_handoff"
    assert db.get_conversation(conn, SESSION)["outcome"] == "handed_off"


def test_llm_error_hands_off_with_a_reply(http, conn):
    mock_profile(http, paid_profile())
    result, _ = chat(conn, [LLMError("provider down")])
    assert result.handed_off is True and result.ticket_id
    assert result.reply
    assert db.get_conversation(conn, SESSION)["outcome"] == "handed_off"
    assert db.get_ticket(conn, result.ticket_id)["source"] == "chat_handoff"


def test_at_most_four_model_calls_per_message(http, conn):
    mock_profile(http, paid_profile())
    fake = FakeLLM([call("get_profile") for _ in range(6)] + [say("done")])
    result, _ = chat(conn, fake)
    assert len(fake.calls) <= 4
    assert isinstance(result, ChatResult)


def test_mark_resolved_closes_the_session(http, conn):
    mock_profile(http, paid_profile())
    result, _ = chat(conn, [call("mark_resolved", note="explained the renewal date"), say("Glad I could help!")], message="when do I renew?")
    assert result.handed_off is False
    assert db.get_conversation(conn, SESSION)["outcome"] == "resolved"
    assert "mark_resolved" in [e["tool"] for e in db.list_events(conn, SESSION)]


def test_store_instructions_makes_no_adapty_write(http, conn):
    mock_profile(http, paid_profile())
    result, fake = chat(conn, [call("store_instructions", kind="cancel", store="play_store"), say("Here is how.")], message="how do I cancel?")
    assert action(result, "store_instructions")[0].allowed is True
    assert http_calls(http, "POST") == [] and http_calls(http, "PATCH") == []
    assert tool_results(fake, "store_instructions")[0]["content"], "canned steps text"


def test_messages_are_stored(http, conn):
    mock_profile(http, paid_profile())
    chat(conn, [say("Your plan renews on Nov 13.")], message="when do I renew?")
    messages = db.list_messages(conn, SESSION)
    assert {"role": "user", "content": "when do I renew?"}.items() <= messages[0].items()
    assert messages[-1]["role"] == "agent" and messages[-1]["content"] == "Your plan renews on Nov 13."
    assert db.get_conversation(conn, SESSION)["customer_user_id"] == CUID
