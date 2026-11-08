"""triage.run / triage.run_insights / triage.legacy_to_ticket (docs/agents.md, Level 1 + Internal interfaces)."""

import json

import pytest

from resolve import db, triage
from resolve.llm import FakeLLM
from resolve.models import TriageTicket
from tests.fakes import legacy_proposal
from tests.fakes_v2 import (
    CUID,
    PROFILE_URL,
    OTHER,
    STRANGER,
    call,
    customer_headers,
    mock_profile,
    new_db,
    option,
    paid_profile,
    services,
    submit,
    tool_results,
    triage_dict,
    triage_ticket,
    unidentified_ticket,
    grant_dict,
    grant_ticket,
    lifetime_profile,
    say,
)

L1_TOOLS = {"get_profile", "find_user_by_email", "get_user_history", "submit_ticket"}  # v3: get_agent_config removed


@pytest.fixture
def conn(tmp_path):
    return new_db(tmp_path)


def run(conn, steps, ticket_id="t-001", force=False, demo=False):
    fake = steps if isinstance(steps, FakeLLM) else FakeLLM(steps)
    triage.run(services(conn, fake, demo=demo), ticket_id, force=force)
    return db.get_ticket(conn, ticket_id), fake


def test_tools_then_submit_proposes_the_ticket(http, conn):
    prof = paid_profile()
    mock_profile(http, prof)
    submitted = triage_ticket(category="no_access_after_purchase")
    ticket, fake = run(conn, [call("get_profile", customer_user_id=CUID), submit(submitted)])

    assert set(fake.calls[0]["tools"]) == L1_TOOLS
    assert len(fake.calls) == 2, "submit_ticket ends the run"
    assert ticket["status"] == "proposed"
    assert TriageTicket.model_validate(ticket["triage"]) == submitted
    assert ticket["category"] == "no_access_after_purchase"
    assert ticket["profile"] == prof
    assert ticket["triage_at"] and not ticket["triage_error"]
    assert ticket["model_calls"] == 2
    assert customer_headers(http) == {CUID}


def test_triaged_status_also_moves_to_proposed(http, conn):
    db.update_ticket(conn, "t-001", status="triaged")
    mock_profile(http, paid_profile())
    ticket, _ = run(conn, [submit(triage_ticket())])
    assert ticket["status"] == "proposed"


@pytest.mark.parametrize(
    "bad",
    [triage_dict(options=[option("reply_only")]), triage_dict(category="angry"), triage_dict(confidence=1.5)],
    ids=["one-option", "category-outside-enum", "confidence>1"],
)
def test_invalid_submit_gets_one_corrective_call(http, conn, bad):
    mock_profile(http, paid_profile())
    ticket, fake = run(conn, [call("submit_ticket", **bad), submit(triage_ticket())])
    assert len(fake.calls) == 2
    assert ticket["status"] == "proposed"
    assert TriageTicket.model_validate(ticket["triage"]) == triage_ticket()


def test_still_invalid_after_the_retry_is_a_triage_error(http, conn):
    mock_profile(http, paid_profile())
    bad = triage_dict(options=[option("reply_only")])
    fake = FakeLLM([call("submit_ticket", **bad), call("submit_ticket", **bad), submit(triage_ticket())])
    ticket, _ = run(conn, fake)
    assert len(fake.calls) == 2, "only one corrective retry"
    assert ticket["status"] == "new"
    assert ticket["triage"] is None and ticket["triage_error"]


def test_get_profile_for_a_foreign_id_is_not_allowed(http, conn):
    mock_profile(http, paid_profile(STRANGER))
    ticket, fake = run(conn, [call("get_profile", customer_user_id=STRANGER), submit(triage_ticket())])
    assert customer_headers(http) == {CUID}, "no Adapty call for an id the run did not earn (v3: the ticket's own id is prefetched)"
    (shown,) = tool_results(fake, "get_profile")
    assert "not allowed" in shown["content"]
    assert ticket["status"] == "proposed"


def test_find_user_by_email_uses_the_index_and_unlocks_get_profile(http, conn):
    db.index_email(conn, "anna@example.com", OTHER)
    tid = db.insert_feedback(conn, "Charged twice, please help", source="email", email="Anna@Example.com")
    prof = paid_profile(OTHER)
    mock_profile(http, prof)
    ticket, fake = run(conn, [
        call("find_user_by_email", email="anna@example.com"),
        call("get_profile", customer_user_id=OTHER),
        submit(triage_ticket(customer_user_id=OTHER, lookup="email in index")),
    ], ticket_id=tid)

    (found,) = tool_results(fake, "find_user_by_email")
    assert OTHER in found["content"] and "not_found" not in found["content"]
    assert customer_headers(http) == {OTHER}
    assert ticket["status"] == "proposed" and ticket["profile"] == prof
    assert ticket["customer_user_id"] == OTHER, "an id found by email is written to a ticket that had none"


def test_id_found_by_email_does_not_overwrite_the_tickets_own_id(http, conn):
    db.index_email(conn, "anna@example.com", OTHER)
    mock_profile(http, paid_profile(OTHER))
    ticket, _ = run(conn, [call("find_user_by_email", email="anna@example.com"), submit(triage_ticket(customer_user_id=OTHER, lookup="email in index"))])
    assert ticket["status"] == "proposed"
    assert ticket["customer_user_id"] == CUID


def test_unknown_email_is_not_found_and_unlocks_nothing(http, conn):
    tid = db.insert_feedback(conn, "Where is my premium?", source="email", email="ghost@example.com")
    mock_profile(http, paid_profile(STRANGER))
    ticket, fake = run(conn, [
        call("find_user_by_email", email="ghost@example.com"),
        call("get_profile", customer_user_id=STRANGER),
        submit(unidentified_ticket()),
    ], ticket_id=tid)
    assert "not_found" in tool_results(fake, "find_user_by_email")[0]["content"]
    assert "not allowed" in tool_results(fake, "get_profile")[0]["content"]
    assert len(http.calls) == 0
    assert ticket["status"] == "proposed"


def test_get_user_history_shows_earlier_tickets_and_grants(http, conn):
    db.update_ticket(conn, "t-001", status="done", category="charged_after_trial")
    db.record_grant(conn, CUID, "premium", 7, None, "l1", "t-001", {})
    tid = db.insert_feedback(conn, "Locked again", source="form", customer_user_id=CUID)
    mock_profile(http, paid_profile())
    _, fake = run(conn, [call("get_user_history", customer_user_id=CUID), submit(triage_ticket())], ticket_id=tid)
    content = tool_results(fake, "get_user_history")[0]["content"]
    assert "t-001" in content


def test_config_is_in_the_prefetched_facts(http, conn):
    mock_profile(http, paid_profile())
    _, fake = run(conn, [submit(triage_ticket())])
    assert "up to 7 days of premium" in fake.calls[0]["messages"][0]["content"]


def test_call_cap_counts_model_calls(http, conn):
    mock_profile(http, paid_profile())
    fake = FakeLLM([call("get_user_history", customer_user_id=CUID) for _ in range(12)] + [submit(triage_ticket())])
    ticket, _ = run(conn, fake)
    assert 1 <= len(fake.calls) <= 8
    assert ticket["model_calls"] == len(fake.calls), "every model call is reserved in model_calls"
    assert ticket["status"] != "proposed" and ticket["triage_error"]


def test_spent_budget_means_no_model_call(http, conn):
    db.update_ticket(conn, "t-001", model_calls=8)
    fake = FakeLLM([submit(triage_ticket())])
    ticket, _ = run(conn, fake)
    assert fake.calls == []
    assert ticket["status"] == "new" and ticket["triage_error"]


def test_demo_mode_makes_no_model_call(http, conn):
    fake = FakeLLM([submit(triage_ticket())])
    ticket, _ = run(conn, fake, demo=True)
    assert fake.calls == [] and len(http.calls) == 0
    assert ticket["triage_error"] and ticket["status"] == "new"


def test_cached_unless_force(http, conn):
    mock_profile(http, paid_profile())
    fake = FakeLLM([submit(triage_ticket(summary="first"))])
    first, _ = run(conn, fake)
    assert first["status"] == "proposed"
    again, _ = run(conn, fake)
    assert len(fake.calls) == 1 and again["triage"] == first["triage"], "cached: no second call"

    fake.steps.append(submit(triage_ticket(summary="second")))
    forced, _ = run(conn, fake, force=True)
    assert len(fake.calls) == 2
    assert forced["triage"]["summary"] == "second" and forced["status"] == "proposed"
    assert forced["model_calls"] == 2


def test_rerun_cannot_change_a_done_ticket(http, conn):
    mock_profile(http, paid_profile())
    first, _ = run(conn, [submit(triage_ticket(summary="first"))])
    db.update_ticket(conn, "t-001", status="done")
    after, _ = run(conn, [submit(triage_ticket(summary="late"))], force=True)
    assert after["status"] == "done"
    assert after["triage"]["summary"] == "first", "the accepted ticket's options stay what the person accepted"


# --- binding submit checks (docs/agents.md, "Rules added after the first build") --------------------

GP = call("get_profile", customer_user_id=CUID)
GRANT = option("grant_access", days=3, reply="I added 3 days.")
REFUND = option("refund_to_human", reply="I asked the store team about a refund.", store="play_store")
REPLY = option("reply_only", reply="Here is what happened.")
STORE = option("store_instructions", reply="Here is how to cancel.", kind="cancel", store="play_store")

BAD_SUBMITS = {
    "unknown-customer-id": ([], triage_dict(customer_user_id=STRANGER)),
    "grant-profile-not-loaded": ([], grant_dict()),  # v3: the prefetch counts as get_profile; here it fails
    "grant-for-lifetime": ([GP], grant_dict()),
    "grant-0-days": ([GP], triage_dict(options=[option("grant_access", days=0, reply="x"), REPLY])),
    "grant-400-days": ([GP], triage_dict(options=[option("grant_access", days=400, reply="x"), REPLY])),
    "best-grant-no-safe-option": ([GP], triage_dict(options=[GRANT, REFUND])),
    "best-refund-no-safe-option": ([], triage_dict(options=[REFUND, REFUND])),
    "unidentified-without-ask_user": ([], triage_dict(customer_user_id=None, options=[REPLY, STORE])),
    "ask_user-without-question": ([], triage_dict(options=[option("ask_user", reply="Which store?"), REPLY])),
    "empty-reply": ([], triage_dict(options=[option("reply_only", reply=""), STORE])),
    "reply-over-160-words": ([], triage_dict(options=[option("reply_only", reply="word " * 161), STORE])),
}


@pytest.mark.parametrize("case", list(BAD_SUBMITS))
def test_failed_submit_check_gets_one_corrective_retry(http, conn, case):
    prefix, bad = BAD_SUBMITS[case]
    if case == "grant-profile-not-loaded":
        http.get(PROFILE_URL, status=500, json={"errors": [{"detail": "down"}]})
    else:
        mock_profile(http, lifetime_profile() if case == "grant-for-lifetime" else paid_profile())
    ticket, fake = run(conn, [*prefix, call("submit_ticket", **bad), submit(triage_ticket())])
    assert len(fake.calls) == len(prefix) + 2
    assert ticket["status"] == "proposed"
    assert TriageTicket.model_validate(ticket["triage"]) == triage_ticket()


@pytest.mark.parametrize("case", list(BAD_SUBMITS))
def test_failed_submit_check_twice_is_a_triage_error(http, conn, case):
    prefix, bad = BAD_SUBMITS[case]
    if case == "grant-profile-not-loaded":
        http.get(PROFILE_URL, status=500, json={"errors": [{"detail": "down"}]})
    else:
        mock_profile(http, lifetime_profile() if case == "grant-for-lifetime" else paid_profile())
    fake = FakeLLM([*prefix, call("submit_ticket", **bad), call("submit_ticket", **bad), submit(triage_ticket())])
    ticket, _ = run(conn, fake)
    assert len(fake.calls) == len(prefix) + 2, "only one corrective retry"
    assert ticket["status"] == "new" and ticket["triage"] is None and ticket["triage_error"]


@pytest.mark.parametrize(
    "prefix,options",
    [
        ([GP], [GRANT, REPLY]),
        ([GP], [GRANT, STORE]),
        ([], [REFUND, REPLY]),
        ([], [REPLY, option("no_reply", reply="")]),
        ([GP], [REPLY, GRANT]),
        ([], [REPLY, option("ask_user", reply="Which store did you buy in?", question="App Store or Google Play?")]),
    ],
    ids=["grant+reply", "grant+store", "refund+reply", "no_reply-empty-reply", "grant-second", "ask_user-with-question"],
)
def test_valid_submits_pass_the_checks(http, conn, prefix, options):
    mock_profile(http, paid_profile())
    ticket, fake = run(conn, [*prefix, submit(triage_dict(options=options))])
    assert len(fake.calls) == len(prefix) + 1
    assert ticket["status"] == "proposed" and [o["action"] for o in ticket["triage"]["options"]] == [o["action"] for o in options]


def test_grant_ticket_after_get_profile_is_proposed(http, conn):
    mock_profile(http, paid_profile())
    ticket, _ = run(conn, [GP, submit(grant_ticket())])
    assert ticket["status"] == "proposed" and ticket["triage"]["options"][0]["action"] == "grant_access"


def test_text_answer_gets_one_nudge(http, conn):
    mock_profile(http, paid_profile())
    ticket, fake = run(conn, [say("The user was charged after the trial."), submit(triage_ticket())])
    assert len(fake.calls) == 2 and ticket["status"] == "proposed"


def test_second_text_answer_is_a_triage_error(http, conn):
    mock_profile(http, paid_profile())
    fake = FakeLLM([say("thinking"), say("still thinking"), submit(triage_ticket())])
    ticket, _ = run(conn, fake)
    assert len(fake.calls) == 2, "one nudge only"
    assert ticket["status"] == "new" and ticket["triage_error"]


def test_get_user_history_for_a_foreign_id_is_not_allowed(http, conn):
    ticket, fake = run(conn, [call("get_user_history", customer_user_id=STRANGER), submit(triage_ticket())])
    assert "not allowed" in tool_results(fake, "get_user_history")[0]["content"]
    assert ticket["status"] == "proposed"


# --- legacy proposals -------------------------------------------------------------------------------


def as_dict(ticket) -> dict:
    return ticket.model_dump() if hasattr(ticket, "model_dump") else ticket


@pytest.mark.parametrize(
    "v1,v2",
    [("grant_access", "grant_access"), ("explain", "reply_only"), ("escalate_refund", "refund_to_human"), ("no_action", "no_reply")],
)
def test_legacy_to_ticket_maps_the_action(v1, v2):
    proposal = legacy_proposal(action=v1)
    ticket = as_dict(triage.legacy_to_ticket(proposal))
    assert len(ticket["options"]) == 1
    (only,) = ticket["options"]
    assert only["action"] == v2
    if v2 == "grant_access":
        assert only["params"]["days"] == 7
    assert ticket["category"] == proposal["category"]
    assert ticket["root_cause"] == proposal["root_cause"]
    assert ticket["confidence"] == proposal["confidence"]
    assert ticket["evidence"] == proposal["evidence"]


def test_legacy_to_ticket_keeps_the_reply():
    proposal = legacy_proposal(action="explain", reply="Your trial ended on Oct 30.")
    assert as_dict(triage.legacy_to_ticket(proposal))["options"][0]["reply"] == "Your trial ended on Oct 30."


# --- insights ---------------------------------------------------------------------------------------

INSIGHT = {
    "cited_cells": [{"category": "charged_after_trial", "flow": "onb_weekly", "tickets": 1, "users": 1}],
    "ticket_ids": ["t-001"],
    "evidence": ["charged_after_trial × onb_weekly: 1"],
    "headline": "1 of 1 tickets is charged_after_trial; it saw Onboarding - Weekly first.",
    "placement": "onboarding",
    "control_flow": "Onboarding - Weekly first",
    "variant_source": "edited_copy",
    "variant_flow": None,
    "variant": "Add the trial end date next to the CTA.",
    "hypothesis": "Disclose the 3-day trial end on Onboarding - Weekly first.",
    "expected_effect": "Fewer charged_after_trial tickets.",
    "caveat": "One ticket.",
    "what_changed": "First analysis.",
}


def test_run_insights_uses_the_submit_insight_tool(http, conn):
    from resolve.insights import group_tickets
    from tests.fakes import ACTIVE_WEEKLY, profile, ticket_row

    groups = group_tickets([dict(ticket_row(), category="charged_after_trial", profile=profile(access_levels=[ACTIVE_WEEKLY]))])
    fake = FakeLLM([call("submit_insight", **INSIGHT)])
    result = triage.run_insights(services(conn, fake), groups)
    assert len(fake.calls) == 1 and "submit_insight" in fake.calls[0]["tools"]
    assert INSIGHT["headline"] in json.dumps(result)
    assert len(http.calls) == 0


def _one_ticket_groups():
    from resolve.insights import group_tickets
    from tests.fakes import ACTIVE_WEEKLY, profile, ticket_row

    return group_tickets([dict(ticket_row(), category="charged_after_trial", profile=profile(access_levels=[ACTIVE_WEEKLY]), triage={"summary": "Charged after the trial."})], seed={})


def test_run_insights_returns_a_snapshot_and_retries_a_wrong_count(http, conn):
    groups = _one_ticket_groups()
    wrong = dict(INSIGHT, cited_cells=[dict(INSIGHT["cited_cells"][0], tickets=12)])
    fake = FakeLLM([call("submit_insight", **wrong), call("submit_insight", **INSIGHT)])
    result = triage.run_insights(services(conn, fake), groups)
    assert len(fake.calls) == 2
    retry = fake.calls[1]["messages"][-1]
    assert retry["role"] == "tool" and "has tickets 1, users 1 in the facts" in retry["content"]
    assert result["snapshot"]["main_cell"] == {"category": "charged_after_trial", "flow": "onb_weekly", "tickets": 1, "users": 1}
    assert result["snapshot"]["categories"] == {"charged_after_trial": 1}


def test_run_insights_stops_after_two_bad_insights(conn):
    fake = FakeLLM([call("submit_insight", **dict(INSIGHT, control_flow="Main - 3 plans"))] * 2)
    with pytest.raises(triage.TriageError, match="control_flow"):
        triage.run_insights(services(conn, fake), _one_ticket_groups())
    assert len(fake.calls) == 2


def test_run_insights_gives_the_stored_insight_as_previous_with_changes(conn):
    groups = _one_ticket_groups()
    stored = dict(INSIGHT, headline="OLD HEADLINE 99 of 99", snapshot={"triaged": 3, "categories": {"refund_request": 3}, "main_cell": {"category": "refund_request", "flow": "main", "tickets": 3, "users": 2}})
    db.save_insights(conn, stored)
    fake = FakeLLM([call("submit_insight", **dict(INSIGHT, what_changed="Shifted."))])
    triage.run_insights(services(conn, fake), groups)
    facts = json.loads(fake.calls[0]["messages"][0]["content"].split("<inbox_facts>")[1].split("</inbox_facts>")[0])
    assert facts["previous"]["hypothesis"] == INSIGHT["hypothesis"] and facts["previous"]["main_cell"] == "charged_after_trial × onb_weekly"
    assert "OLD HEADLINE" not in json.dumps(facts), "old counts in the headline would be copied as current"
    assert facts["changes"]["triaged"] == "3 → 1"
    assert facts["changes"]["categories_changed"] == {"charged_after_trial": "0 → 1", "refund_request": "3 → 0"}
    assert facts["changes"]["previous_main_cell"] == "refund_request × Main - 3 plans: 2 users, 3 tickets → 0 users, 0 tickets"
    assert facts["changes"]["cells_now_larger_than_it"] == ["charged_after_trial × Onboarding - Weekly first: 1 users, 1 tickets"]


def test_changes_list_no_larger_cell_on_a_tie():
    from resolve import insights
    from tests.fakes import ticket_row

    rows = [dict(ticket_row(id=f"r{i}", customer=f"u{i}"), category="refund_request", triage={"summary": "s"}) for i in range(2)]
    rows += [dict(ticket_row(id=f"n{i}", customer=f"v{i}"), category="no_access_after_purchase", context={"flow": "onb_trial", "placement": "onboarding"}, triage={"summary": "s"}) for i in range(2)]
    groups = insights.group_tickets(rows, seed={})
    snap = insights.snapshot(groups, {"category": "refund_request", "flow": "onb_weekly"})
    assert snap["main_cell"] == {"category": "refund_request", "flow": "onb_weekly", "tickets": 2, "users": 2}
    assert insights.changes(snap, groups)["cells_now_larger_than_it"] == "none"
