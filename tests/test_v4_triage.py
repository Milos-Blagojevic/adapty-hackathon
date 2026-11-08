"""v4 Level 1 analysis quality (docs/agents.md, "v4: Level 1 analysis quality"): facts, schema order, checks."""

import importlib.util
import json
from datetime import datetime, timezone

import pytest
from pydantic import BaseModel

from resolve import config, db, llm_schema, triage
from resolve.llm import FakeLLM
from resolve.models import AgentConfig, TriageTicket
from tests.fakes_v2 import (
    CUID,
    EXPIRED,
    SUB_WEEKLY,
    call,
    grant_dict,
    mock_profile,
    new_db,
    option,
    paid_profile,
    profile,
    services,
    submit,
    triage_dict,
    triage_ticket,
    unpaid_profile,
)

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)
REPLY = option("reply_only", reply="Here is what happened.")


@pytest.fixture
def conn(tmp_path):
    return new_db(tmp_path)


def run(conn, steps, ticket_id="t-001"):
    fake = FakeLLM(steps)
    triage.run(services(conn, fake), ticket_id)
    return db.get_ticket(conn, ticket_id), fake


def facts_of(fake: FakeLLM) -> dict:
    return json.loads(fake.calls[0]["messages"][0]["content"].split("Facts loaded by the system:\n", 1)[1])


# --- facts: what the dates mean ------------------------------------------------------------------


def sub(**kw) -> dict:
    base = {"store": "app_store", "store_product_id": "lumen.weekly", "offer": None, "renewal_cancelled_at": None,
            "billing_issue_detected_at": None, "is_in_grace_period": False, "cancellation_reason": None}
    return {**base, **kw}


TRIAL = {"category": "introductory", "type": "free_trial", "id": None}


def test_state_trial_ended_with_a_failed_first_charge_was_never_paid():
    s = sub(offer=TRIAL, originally_purchased_at="2026-10-29T01:00:00+00:00", purchased_at="2026-10-29T01:00:00+00:00",
            expires_at="2026-11-01T01:00:00+00:00", billing_issue_detected_at="2026-11-01T01:00:00+00:00",
            renewal_cancelled_at="2026-11-01T01:00:00+00:00", cancellation_reason="billing_error")
    state = triage.subscription_state(s, NOW)
    assert "never paid" in state and "Nov 1, 2026 (7 days ago)" in state and "renewal off since" in state


def test_state_lapsed_after_failed_payment_says_the_store_will_not_retry():
    s = sub(originally_purchased_at="2026-08-13T22:00:00+00:00", purchased_at="2026-10-04T22:00:00+00:00",
            expires_at="2026-10-11T22:00:00+00:00", billing_issue_detected_at="2026-10-11T22:00:00+00:00",
            renewal_cancelled_at="2026-10-11T22:00:00+00:00", cancellation_reason="billing_error")
    state = triage.subscription_state(s, NOW)
    assert "will not retry" in state and "subscribes again" in state


def test_state_failing_payment_without_cancel_says_the_store_retries():
    s = sub(originally_purchased_at="2026-10-01T00:00:00+00:00", purchased_at="2026-10-25T00:00:00+00:00",
            expires_at="2026-11-01T00:00:00+00:00", billing_issue_detected_at="2026-11-01T00:00:00+00:00")
    assert "retries" in triage.subscription_state(s, NOW)


def test_state_names_a_converted_trial_with_both_dates():
    s = sub(store_product_id="lumen.annual_trial", originally_purchased_at="2026-10-13T15:00:00+00:00",
            purchased_at="2026-10-20T15:00:00+00:00", expires_at="2027-10-20T15:00:00+00:00")
    state = triage.subscription_state(s, NOW)
    assert "7-day free trial from Oct 13, 2026" in state and "first paid period on Oct 20, 2026" in state and "renews Oct 20, 2027" in state


def test_state_refund():
    s = sub(originally_purchased_at="2026-10-15T00:00:00+00:00", purchased_at="2026-10-18T00:00:00+00:00",
            expires_at="2026-10-25T00:00:00+00:00", cancellation_reason="refund")
    assert triage.subscription_state(s, NOW).startswith("weekly plan: refunded")


def test_access_from_a_server_side_grant_is_not_a_purchase():
    level = {"access_level_id": "premium", "store": "adapty", "store_product_id": "adapty_server_side_product", "expires_at": "2026-11-20T00:00:00+00:00"}
    assert "granted server-side" in triage.access_state(level, NOW)


@pytest.mark.parametrize(
    "prof,grants,within,why",
    [
        (paid_profile(), [], True, None),
        (unpaid_profile(), [], False, "no paid subscription is active now"),
        (profile(access_levels=[EXPIRED], subscriptions=[dict(SUB_WEEKLY, expires_at="2026-10-01T00:00:00+00:00")]), [], False, "no paid subscription is active now"),
        (paid_profile(), [{"days": 7}], False, "we already gave this user free days"),
    ],
    ids=["paying", "never-paid", "lapsed", "granted-in-30-days"],
)
def test_free_days_use_the_level_2_policy_check(prof, grants, within, why):
    out = triage.free_days(prof, AgentConfig(), grants, NOW)
    assert out["within_policy"] is within
    assert (out["why_not"] is None) if why is None else out["why_not"].startswith(why)


def test_prefetched_profile_has_states_free_days_and_the_users_store_steps(http, conn):
    mock_profile(http, paid_profile())
    _, fake = run(conn, [submit(triage_ticket())])
    prof = facts_of(fake)["profile"]
    assert "compensation" not in prof and prof["free_days"]["within_policy"] is True
    assert prof["subscriptions"][0]["state"] and prof["access_levels"][0]["state"]
    assert set(prof["store_steps"]) == {"play_store"} and "Payments & subscriptions" in prof["store_steps"]["play_store"]["cancel"]


def test_grant_in_the_last_30_days_shows_in_free_days(http, conn):
    db.record_grant(conn, CUID, "premium", 7, None, "l2", "s-1", {})
    mock_profile(http, paid_profile())
    _, fake = run(conn, [submit(triage_ticket())])
    assert facts_of(fake)["profile"]["free_days"]["within_policy"] is False


# --- the chat is shown once, as the conversation the reply continues --------------------------------


def test_handed_off_chat_is_shown_once_with_who_wrote_it(http, conn):
    db.ensure_conversation(conn, "s-9", CUID)
    db.append_message(conn, "s-9", "user", "UNIQUE-USER-LINE")
    db.append_message(conn, "s-9", "agent", "UNIQUE-BOT-LINE")
    body = "Handoff reason: other\nSummary: x\n\nConversation:\nuser: UNIQUE-USER-LINE\nagent: UNIQUE-BOT-LINE\n\nTools tried:\n- none"
    tid = db.insert_feedback(conn, body, source="chat_handoff", customer_user_id=CUID, session_id="s-9")
    mock_profile(http, paid_profile())
    _, fake = run(conn, [submit(triage_ticket())], ticket_id=tid)
    first = fake.calls[0]["messages"][0]["content"]
    assert first.count("UNIQUE-USER-LINE") == 1 and first.count("UNIQUE-BOT-LINE") == 1
    assert "assistant (first-line AI" in first and "sent by the support teammate" in first and "Tools tried" in first
    assert facts_of(fake)["history"]["sessions"] == [], "the ticket's own chat is not repeated as history"


# --- schema order ---------------------------------------------------------------------------------


def test_submit_schema_asks_for_evidence_before_the_summary_and_risk_before_the_reply():
    spec = next(t for t in triage.TOOLS if t.name == "submit_ticket")
    schema = llm_schema.strict_schema(spec.params)
    order = list(schema["properties"])
    assert set(order) == set(TriageTicket.model_fields)
    assert order.index("evidence") < order.index("root_cause") < order.index("summary") < order.index("category")
    opt = list(schema["properties"]["options"]["items"]["properties"])
    assert opt.index("risk_reason") < opt.index("reply")


# --- checks ---------------------------------------------------------------------------------------


def outside_policy_grant(i: int, risk: str) -> dict:
    grant = option("grant_access", days=3, reply="I added 3 days.", risk=risk)
    return triage_dict(options=[grant, REPLY] if i == 0 else [REPLY, grant])


@pytest.mark.parametrize("submitted", [outside_policy_grant(0, "low"), outside_policy_grant(1, "low"), outside_policy_grant(1, "high")], ids=["first", "second-low-risk", "second-high-risk"])
def test_grant_outside_the_policy_is_moved_last_with_high_risk_by_code(http, conn, submitted):
    mock_profile(http, unpaid_profile())
    ticket, fake = run(conn, [submit(submitted)])
    assert len(fake.calls) == 1, "no correction needed: the limit is code's"
    options = ticket["triage"]["options"]
    assert [o["action"] for o in options] == ["reply_only", "grant_access"]
    assert options[1]["risk"] == "high" and options[1]["risk_reason"].startswith("Outside the policy: no paid subscription")


def test_check_rejects_a_blocked_grant_first_if_it_ever_gets_there():
    t = TriageTicket.model_validate(outside_policy_grant(0, "high"))
    problem = triage.check_ticket(t, {CUID}, {CUID: unpaid_profile()}, AgentConfig(), [], NOW)
    assert problem and "outside the policy" in problem


def test_invalid_json_arguments_get_a_precise_correction(http, conn):
    mock_profile(http, paid_profile())
    ticket, fake = run(conn, [call("submit_ticket", _invalid_json='{"summary": "says "Pro+ only" here"}'), submit(triage_ticket())])
    correction = fake.calls[1]["messages"][-1]["content"]
    assert "not valid JSON" in correction and "single quotes" in correction and "Pro+" not in correction
    assert ticket["status"] == "proposed"


def test_grant_within_the_policy_may_be_first(http, conn):
    mock_profile(http, paid_profile())
    ticket, fake = run(conn, [submit(grant_dict())])
    assert len(fake.calls) == 1 and ticket["triage"]["options"][0]["action"] == "grant_access"


def test_pronouns_get_one_correction_but_never_fail_the_run(http, conn):
    mock_profile(http, paid_profile())
    she = triage_dict(summary="She wants access back.")
    ticket, fake = run(conn, [call("submit_ticket", **she), call("submit_ticket", **she)])
    assert len(fake.calls) == 2 and "'she'" in fake.calls[1]["messages"][-1]["content"]
    assert ticket["status"] == "proposed" and ticket["triage"]["summary"] == "She wants access back."


def test_reply_over_120_words_is_rejected(http, conn):
    mock_profile(http, paid_profile())
    long = triage_dict(options=[option("reply_only", reply="word " * 121), option("ask_user", reply="Which store?", question="Which store?")])
    _, fake = run(conn, [call("submit_ticket", **long), submit(triage_ticket())])
    assert "121 words" in fake.calls[1]["messages"][-1]["content"]


# --- eval: hard cases with their own feedback -------------------------------------------------------


def test_eval_inserts_cases_with_feedback_chat_and_prior_grant(tmp_path):
    spec = importlib.util.spec_from_file_location("eval_script", config.ROOT / "scripts" / "eval.py")
    eval_script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(eval_script)
    conn = new_db(tmp_path)
    cases = [
        {"ticket_id": "h-a", "feedback": {"source": "email", "email": "x@example.com", "subject": "S", "text": "T"}},
        {"ticket_id": "h-b", "feedback": {"source": "chat_handoff", "customer_user_id": CUID},
         "chat": {"session_id": "eval-h-b", "reason": "other", "summary": "sum", "messages": [["user", "hi"], ["agent", "hello"]]},
         "setup": {"prior_grant": {"days": 7, "source": "l2", "ref": "eval-h-b"}}},
        {"ticket_id": "t-001"},
    ]
    assert eval_script.insert_cases(conn, cases, "premium") == 2
    a, b = db.get_ticket(conn, "h-a"), db.get_ticket(conn, "h-b")
    assert a["source"] == "email" and not a["customer_user_id"] and a["email"] == "x@example.com"
    assert b["session_id"] == "eval-h-b" and [m["role"] for m in db.list_messages(conn, "eval-h-b")] == ["user", "agent"]
    assert "Conversation:\nuser: hi" in b["body"] and db.grants_since(conn, CUID, "1970")[0]["ref"] == "eval-h-b"
    assert eval_script.insert_cases(conn, cases, "premium") == 0, "idempotent"


def test_eval_json_hard_cases_are_well_formed():
    data = json.loads((config.ROOT / "data" / "eval.json").read_text())
    new = [c for c in data["hard_cases"] if c["ticket_id"].startswith("h-")]
    assert 4 <= len(new) <= 6
    sources = {c["feedback"]["source"] for c in new}
    assert {"email", "form", "chat_handoff"} <= sources
    assert any(c["feedback"].get("customer_user_id") is None and not c.get("chat") for c in new), "an unidentified user"
    for c in new:
        assert set(c["acceptable_actions"]) <= {"grant_access", "reply_only", "store_instructions", "refund_to_human", "ask_user", "no_reply"}
        assert c["expected_action"] in c["acceptable_actions"]

def test_invalid_json_cut_at_the_end_is_called_unbalanced():
    msg = triage._json_error('{"summary": "x", "options": [{"action": "reply_only"}')
    assert "do not close" in msg and "single quotes" not in msg
    assert "single quotes" in triage._json_error('{"summary": "says "Pro+" here"}')


def test_invalid_json_does_not_spend_the_one_correction(http, conn):
    mock_profile(http, paid_profile())
    same = triage_dict(options=[option("reply_only", reply="A."), option("reply_only", reply="B.")])
    ticket, fake = run(conn, [call("submit_ticket", _invalid_json='{"a": 1}}'), call("submit_ticket", **same), submit(triage_ticket())])
    assert len(fake.calls) == 3 and ticket["status"] == "proposed"


def test_invalid_json_twice_fails(http, conn):
    mock_profile(http, paid_profile())
    bad = call("submit_ticket", _invalid_json='{"a": 1}}')
    same = call("submit_ticket", **triage_dict(options=[option("reply_only", reply="A."), option("reply_only", reply="B.")]))
    ticket, fake = run(conn, [bad, same, bad])
    assert len(fake.calls) == 3 and "no valid ticket" in ticket["triage_error"]


def test_server_side_grant_hides_its_renewal_stamp():
    """Adapty stamps renewal_cancelled_at on every grant; the store plan under it still renews (f-0018)."""
    now = datetime(2026, 11, 8, tzinfo=timezone.utc)
    grant = {"access_level_id": "premium", "store": "adapty", "store_product_id": "adapty_server_side_product", "purchased_at": "2026-11-08T07:41:20+00:00", "expires_at": "2027-10-29T15:39:36+00:00", "renewal_cancelled_at": "2026-11-08T07:41:20+00:00"}
    sub = {"store": "app_store", "store_product_id": "lumen.annual_trial", "originally_purchased_at": "2026-10-15T15:39:36+00:00", "purchased_at": "2026-10-22T15:39:36+00:00", "expires_at": "2027-10-22T15:39:36+00:00", "renewal_cancelled_at": None}
    out = triage.model_profile({"access_levels": [grant], "subscriptions": [sub], "non_subscriptions": [], "total_revenue_usd": 53.3}, now, AgentConfig(), [])
    assert "renewal_cancelled_at" not in out["access_levels"][0]
    assert "renews Oct 22, 2027" in out["subscriptions"][0]["state"]
    assert "7-day free trial" in out["subscriptions"][0]["state"]


def test_a_field_added_to_the_model_reaches_the_tool_schema():
    class Base(BaseModel):
        a: str
        b: str
        added: str | None = None

    assert list(triage._reordered(Base, "Draft", ["b", "a"]).model_fields) == ["b", "a", "added"]
