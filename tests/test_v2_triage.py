"""triage.run / triage.run_insights / triage.legacy_to_ticket (docs/agents.md, Level 1 + Internal interfaces)."""

import json

import pytest

from resolve import db, triage
from resolve.llm import FakeLLM
from resolve.models import TriageTicket
from tests.fakes import legacy_proposal
from tests.fakes_v2 import (
    CUID,
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
)

L1_TOOLS = {"get_profile", "find_user_by_email", "get_user_history", "get_agent_config", "submit_ticket"}


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
    assert len(http.calls) == 0, "no Adapty call for an id the run did not earn"
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


def test_unknown_email_is_not_found_and_unlocks_nothing(http, conn):
    tid = db.insert_feedback(conn, "Where is my premium?", source="email", email="ghost@example.com")
    mock_profile(http, paid_profile(STRANGER))
    ticket, fake = run(conn, [
        call("find_user_by_email", email="ghost@example.com"),
        call("get_profile", customer_user_id=STRANGER),
        submit(triage_ticket(customer_user_id=None, lookup="email not in index")),
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


def test_get_agent_config_returns_the_config(http, conn):
    mock_profile(http, paid_profile())
    _, fake = run(conn, [call("get_agent_config"), submit(triage_ticket())])
    assert "Lumen" in tool_results(fake, "get_agent_config")[0]["content"]


def test_call_cap_counts_model_calls(http, conn):
    mock_profile(http, paid_profile())
    fake = FakeLLM([call("get_agent_config") for _ in range(12)] + [submit(triage_ticket())])
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
    "headline": "16 of 60 tickets are charged_after_trial; 12 saw Onboarding - Weekly first.",
    "hypothesis": "Disclose the 3-day trial end on Onboarding - Weekly first.",
    "placement": "onboarding",
    "control_flow": "Onboarding - Weekly first",
    "variant": "Add the trial end date next to the CTA.",
    "expected_effect": "Fewer charged_after_trial tickets.",
    "evidence": ["charged_after_trial: 16"],
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
