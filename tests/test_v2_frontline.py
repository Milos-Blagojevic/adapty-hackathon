"""frontline.chat: the Level 2 loop, scripted with FakeLLM (docs/agents.md, Level 2 + Internal interfaces)."""

import pytest

from resolve import db, frontline
from resolve.llm import FakeLLM, LLMError
from resolve.models import HANDOFF_RULES, ChatResult
from tests.fakes_v2 import (
    CUID,
    STRANGER,
    all_grants,
    call,
    calls,
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
    unpaid_profile,
)

SESSION = "s-1"
FIXED_REPLY = "I've passed this to a teammate who can help with this; they will reply here."
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


def handed_off_in_code(conn, result, fake, model_calls):
    """Binding rule: a blocked grant that maps to an always_handoff rule ends the turn in code."""
    assert result.handed_off is True and result.ticket_id
    assert result.reply == FIXED_REPLY
    assert len(fake.calls) == model_calls, "no model call after the blocked grant"
    ticket = db.get_ticket(conn, result.ticket_id)
    assert ticket["source"] == "chat_handoff" and ticket["session_id"] == result_session(conn, result)
    assert db.get_conversation(conn, ticket["session_id"])["outcome"] == "handed_off"


def result_session(conn, result):
    return db.get_ticket(conn, result.ticket_id)["session_id"]


def without(rule):
    return [r for r in HANDOFF_RULES if r != rule]


def blocked_event(conn, session=SESSION):
    events = [e for e in db.list_events(conn, session) if e["tool"] == "grant_access"]
    assert events and events[-1]["allowed"] is False
    return events[-1]


def test_grant_called_anyway_while_disabled_hands_off(http, conn):
    set_config(conn, tools={"grant_access": False})
    mock_profile(http, paid_profile())
    mock_grant_ok(http)
    result, fake = chat(conn, grant_steps())
    assert http_calls(http, "POST") == [] and all_grants(conn) == []
    assert all(not a.allowed for a in result.actions if a.tool == "grant_access")
    assert blocked_event(conn)["blocked_reason"] == "tool_disabled"
    handed_off_in_code(conn, result, fake, model_calls=2)


def test_tool_disabled_hands_off_even_with_no_always_handoff_rules(http, conn):
    set_config(conn, tools={"grant_access": False}, always_handoff=[])
    mock_profile(http, paid_profile())
    result, fake = chat(conn, grant_steps())
    assert blocked_event(conn)["blocked_reason"] == "tool_disabled"
    handed_off_in_code(conn, result, fake, model_calls=2)


def test_grant_over_max_days_is_blocked_once_and_the_model_may_answer(http, conn):
    mock_profile(http, paid_profile())
    mock_grant_ok(http)
    result, fake = chat(conn, grant_steps(days=30, reply="I can't do 30 days."))

    (grant,) = action(result, "grant_access")
    assert grant.allowed is False and grant.blocked_reason == "over_max_days"
    assert http_calls(http, "POST") == [] and all_grants(conn) == []
    (shown,) = tool_results(fake, "grant_access")
    assert shown["content"].startswith("blocked: over_max_days") and "max 7 days" in shown["content"]
    assert blocked_event(conn)["blocked_reason"] == "over_max_days"
    assert result.handed_off is False and result.reply == "I can't do 30 days."


def test_over_max_days_retry_within_the_limit_is_allowed(http, conn):
    mock_profile(http, paid_profile())
    mock_grant_ok(http)
    result, _ = chat(conn, [call("get_profile"), call("grant_access", days=30, reason="x"), call("grant_access", days=5, reason="x"), say("Added 5 days.")])
    assert [(a.allowed, a.blocked_reason) for a in action(result, "grant_access")] == [(False, "over_max_days"), (True, None)]
    assert len(http_calls(http, "POST")) == 1 and all_grants(conn)[0]["days"] == 5
    assert result.handed_off is False and result.reply == "Added 5 days."


def test_second_over_max_days_in_a_turn_hands_off(http, conn):
    mock_profile(http, paid_profile())
    mock_grant_ok(http)
    result, fake = chat(conn, [call("get_profile"), call("grant_access", days=30, reason="x"), call("grant_access", days=14, reason="x"), say("unused")])
    assert [a.blocked_reason for a in action(result, "grant_access")] == ["over_max_days", "over_max_days"]
    assert http_calls(http, "POST") == []
    handed_off_in_code(conn, result, fake, model_calls=3)


def test_over_max_days_counts_per_turn(http, conn):
    mock_profile(http, paid_profile())
    chat(conn, [call("grant_access", days=30, reason="x"), say("No, sorry.")])
    second, _ = chat(conn, [call("grant_access", days=30, reason="x"), say("Still no.")], message="please, 30 days")
    assert second.handed_off is False and second.reply == "Still no."


def test_max_days_follows_the_stored_config(http, conn):
    set_config(conn, grant={"max_days": 3})
    mock_profile(http, paid_profile())
    mock_grant_ok(http)
    result, fake = chat(conn, grant_steps(days=4))
    assert action(result, "grant_access")[0].blocked_reason == "over_max_days"
    assert "max 3 days" in tool_results(fake, "grant_access")[0]["content"]
    assert http_calls(http, "POST") == []


def test_lifetime_user_hands_off_in_code(http, conn):
    """lifetime → profile_mismatch, which is in the default always_handoff."""
    mock_profile(http, lifetime_profile())
    mock_grant_ok(http)
    fake = FakeLLM([call("get_profile"), calls(("grant_access", {"days": 3, "reason": "x"}), ("store_instructions", {"kind": "restore", "store": "app_store"})), say("unused")])
    result, _ = chat(conn, fake)
    (grant,) = action(result, "grant_access")
    assert grant.allowed is False and grant.blocked_reason == "lifetime"
    assert http_calls(http, "POST") == []
    assert [e["tool"] for e in db.list_events(conn, SESSION)][:2] == ["get_profile", "grant_access"]
    after = [e for e in db.list_events(conn, SESSION) if e["tool"] == "store_instructions"]
    assert all(e["allowed"] is False for e in after), "no further tool calls this turn (a skipped call may be logged, never run)"
    assert blocked_event(conn)["blocked_reason"] == "lifetime"
    handed_off_in_code(conn, result, fake, model_calls=2)


def test_lifetime_without_profile_mismatch_rule_is_just_blocked(http, conn):
    set_config(conn, always_handoff=without("profile_mismatch"))
    mock_profile(http, lifetime_profile())
    result, fake = chat(conn, grant_steps(reply="Your plan is lifetime already."))
    (grant,) = action(result, "grant_access")
    assert grant.blocked_reason == "lifetime"
    assert tool_results(fake, "grant_access")[0]["content"] == "blocked: lifetime"
    assert result.handed_off is False and result.reply == "Your plan is lifetime already."


def test_not_paid_hands_off_in_code(http, conn):
    mock_profile(http, unpaid_profile())
    result, fake = chat(conn, grant_steps())
    assert blocked_event(conn)["blocked_reason"] == "not_paid"
    handed_off_in_code(conn, result, fake, model_calls=2)


def test_not_paid_without_profile_mismatch_rule_is_just_blocked(http, conn):
    set_config(conn, always_handoff=without("profile_mismatch"))
    mock_profile(http, unpaid_profile())
    result, fake = chat(conn, grant_steps(reply="You have no purchase yet."))
    assert tool_results(fake, "grant_access")[0]["content"] == "blocked: not_paid"
    assert result.handed_off is False


def test_complaining_again_does_not_give_more_days(http, conn):
    """Default config: one grant per user per 30 days; the second message is blocked and handed off."""
    mock_profile(http, paid_profile())
    mock_grant_ok(http)
    first, _ = chat(conn, grant_steps())
    assert action(first, "grant_access")[0].allowed is True
    fake = FakeLLM(grant_steps())
    second, _ = chat(conn, fake, message="still locked, give me more")
    (grant,) = action(second, "grant_access")
    assert grant.allowed is False and grant.blocked_reason in ("grant_limit_30d", "already_granted_this_session")
    assert len(http_calls(http, "POST")) == 1 and len(all_grants(conn)) == 1
    handed_off_in_code(conn, second, fake, model_calls=2)


def test_second_grant_in_the_same_session_is_blocked_even_under_the_30d_limit(http, conn):
    set_config(conn, grant={"max_grants_per_user_30d": 2})
    mock_profile(http, paid_profile())
    mock_grant_ok(http)
    chat(conn, grant_steps())
    fake = FakeLLM(grant_steps())
    second, _ = chat(conn, fake, message="one more please")
    assert action(second, "grant_access")[0].blocked_reason == "already_granted_this_session"
    assert len(http_calls(http, "POST")) == 1
    handed_off_in_code(conn, second, fake, model_calls=2)


def test_grant_from_another_channel_within_30_days_hands_off(http, conn):
    db.record_grant(conn, CUID, "premium", 3, None, "l1", "t-001", {})
    mock_profile(http, paid_profile())
    mock_grant_ok(http)
    result, fake = chat(conn, grant_steps(), session="s-new")
    assert action(result, "grant_access")[0].blocked_reason == "grant_limit_30d"
    assert http_calls(http, "POST") == []
    handed_off_in_code(conn, result, fake, model_calls=2)


def test_grant_limit_without_repeat_rule_is_just_blocked(http, conn):
    set_config(conn, always_handoff=without("repeat_after_grant"))
    db.record_grant(conn, CUID, "premium", 3, None, "l1", "t-001", {})
    mock_profile(http, paid_profile())
    result, fake = chat(conn, grant_steps(reply="You already got days this month."), session="s-new")
    assert tool_results(fake, "grant_access")[0]["content"] == "blocked: grant_limit_30d"
    assert result.handed_off is False and result.reply == "You already got days this month."
    assert db.get_conversation(conn, "s-new")["outcome"] != "handed_off"


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


@pytest.mark.parametrize(
    "setup,steps,mapped",
    [
        ("lifetime", [call("get_profile"), call("grant_access", days=3, reason="x"), say("unused")], "profile_mismatch"),
        ("unpaid", [call("get_profile"), call("grant_access", days=3, reason="x"), say("unused")], "profile_mismatch"),
        ("granted", [call("get_profile"), call("grant_access", days=3, reason="x"), say("unused")], "repeat_after_grant"),
        ("disabled", [call("grant_access", days=3, reason="x"), say("unused")], "other"),
        ("paid", [call("grant_access", days=30, reason="x"), call("grant_access", days=20, reason="x"), say("unused")], "other"),
    ],
    ids=["lifetime", "not_paid", "grant_limit_30d", "tool_disabled", "over_max_days-twice"],
)
def test_blocked_grant_handoff_reason_is_mapped(http, conn, setup, steps, mapped):
    """The mapped rule is what Analyze reports as the handoff reason."""
    from resolve import analytics

    mock_profile(http, {"lifetime": lifetime_profile(), "unpaid": unpaid_profile()}.get(setup, paid_profile()))
    if setup == "granted":
        db.record_grant(conn, CUID, "premium", 3, None, "l1", "t-001", {})
    if setup == "disabled":
        set_config(conn, tools={"grant_access": False})
    result, _ = chat(conn, steps)
    assert result.handed_off is True
    assert analytics.compute(conn)["level2"]["handoff_reasons"] == {mapped: 1}
