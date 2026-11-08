"""v3 Level 1: code resolves the user and loads the facts before the first call (docs/agents.md, v3)."""

import json

import pytest

from resolve import db, triage
from resolve.llm import FakeLLM
from resolve.models import TriageTicket
from tests.fakes_v2 import CUID, OTHER, call, customer_headers, grant_ticket, mock_profile, new_db, option, paid_profile, services, submit, triage_dict, triage_ticket


@pytest.fixture
def conn(tmp_path):
    return new_db(tmp_path)


def run(conn, steps, ticket_id="t-001"):
    fake = FakeLLM(steps)
    triage.run(services(conn, fake), ticket_id)
    return db.get_ticket(conn, ticket_id), fake


def facts_of(fake: FakeLLM) -> dict:
    return json.loads(fake.calls[0]["messages"][0]["content"].split("Facts loaded by the system:\n", 1)[1])


def test_one_call_with_prefetched_profile_history_and_config(http, conn):
    mock_profile(http, paid_profile())
    ticket, fake = run(conn, [submit(grant_ticket())])  # grant allowed: the prefetch counts as get_profile
    assert len(fake.calls) == 1 and ticket["status"] == "proposed"
    facts = facts_of(fake)
    assert facts["lookup"] == "by customer_user_id" and facts["customer_user_id"] == CUID
    assert "access_levels" in json.dumps(facts["profile"]) and set(facts["history"]) == {"tickets", "grants", "sessions"}
    assert facts["config"]["grants"].startswith("up to 7 days of premium")


def test_email_lookup_happens_in_code_and_unlocks_the_id(http, conn):
    db.index_email(conn, "anna@example.com", OTHER)
    tid = db.insert_feedback(conn, "Locked", source="email", email="anna@example.com")
    mock_profile(http, paid_profile(OTHER))
    ticket, fake = run(conn, [submit(triage_ticket(customer_user_id=OTHER))], ticket_id=tid)
    facts = facts_of(fake)
    assert facts["lookup"] == "by email (unverified sender)" and facts["customer_user_id"] == OTHER
    assert customer_headers(http) == {OTHER}
    assert ticket["status"] == "proposed" and ticket["customer_user_id"] == OTHER
    assert "unverified" in fake.calls[0]["system"]


def test_unknown_email_is_not_identified(http, conn):
    tid = db.insert_feedback(conn, "Locked", source="email", email="nobody@example.com")
    _, fake = run(conn, [submit(triage_ticket(customer_user_id=None, lookup="not in index", options=[option("ask_user", reply="Which email?", question="Which email?"), option("reply_only", reply="Steps.")]))], ticket_id=tid)
    facts = facts_of(fake)
    assert facts["customer_user_id"] is None and facts["lookup"].startswith("not identified")
    assert "profile" not in facts and len(http.calls) == 0


def test_repeated_action_gets_one_correction(http, conn):
    mock_profile(http, paid_profile())
    same = triage_dict(options=[option("reply_only", reply="A."), option("reply_only", reply="B.")])
    ticket, fake = run(conn, [call("submit_ticket", **same), submit(triage_ticket())])
    assert len(fake.calls) == 2 and ticket["status"] == "proposed"
    rejected = [m for m in fake.calls[1]["messages"] if m.get("role") == "tool"]
    assert "repeats the action" in json.dumps(rejected)


def test_ungrounded_evidence():
    t = TriageTicket.model_validate(triage_dict(evidence=["product: lumen_weekly", "expires_at: 2030-01-01", "no colon here"]))
    facts = '{"product": "Lumen_Weekly", "note": "nothing"}'
    assert triage.ungrounded_evidence(t, facts) == ["expires_at: 2030-01-01"]


def test_ungrounded_evidence_checks_numbers_dates_and_ids():
    t = TriageTicket.model_validate(triage_dict(evidence=[
        "subscriptions: lumen_weekly bought 2026-10-19T14:09:53Z, $6.99, no billing issue",
        "access: premium active until 2027-01-01",
        "history: ticket x-999 on Nov 1",
    ]))
    facts = '{"product": "lumen_weekly", "purchased_at": "2026-10-19T14:09:53+00:00", "price": "6.99", "expires_at": "2027-01-01T00:00:00+00:00"}'
    assert triage.ungrounded_evidence(t, facts) == ["history: ticket x-999 on Nov 1"]
