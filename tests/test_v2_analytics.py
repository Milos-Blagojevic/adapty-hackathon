"""analytics.compute: the Analyze numbers (docs/agents.md, "Analyze, first version" + the /api/analytics shape)."""

import pytest

from resolve import analytics, db
from resolve.models import Decision, OptionEdits
from tests.fakes_v2 import CUID, OTHER, triage_ticket

TOP_KEYS = {"tickets", "level2", "triage", "eval"}
TICKET_KEYS = {"total", "by_status", "by_source", "by_category", "per_day"}
LEVEL2_KEYS = {"sessions", "resolved", "handed_off", "open", "handoff_reasons", "grants", "days_granted", "blocked"}
TRIAGE_KEYS = {"triaged", "confidence_buckets", "accepted_option", "top_root_causes"}
BUCKETS = {"0-0.5", "0.5-0.7", "0.7-0.9", "0.9-1"}
ACCEPTED = {"1", "2", "3", "edited", "rejected"}
EVAL_KEYS = {"model", "cases", "category_correct", "action_correct"}

ROOT_A = "The weekly trial converted and the user missed the date."
ROOT_B = "The card was declined at renewal."
ROOT_C = "The user wants money back for an unused year."


def ticket(conn, source, status, created, category=None, confidence=None, root_cause=None, decision=None):
    tid = db.insert_feedback(conn, "text", source=source, customer_user_id=CUID)
    fields = dict(status=status, created_at=created)
    if confidence is not None:
        tt = triage_ticket(category=category, confidence=confidence, root_cause=root_cause)
        fields.update(triage_json=tt.model_dump_json(), triage_at=created, category=category)
    if decision is not None:
        fields["decision_json"] = decision.model_dump_json()
    db.update_ticket(conn, tid, **fields)
    return tid


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "a.sqlite")
    ticket(c, "email", "proposed", "2026-11-06T09:00:00Z", "charged_after_trial", 0.95, ROOT_A)
    ticket(c, "form", "done", "2026-11-06T10:00:00Z", "charged_after_trial", 0.8, ROOT_A, Decision(option=0))
    handed = ticket(c, "chat_handoff", "done", "2026-11-06T11:00:00Z", "billing_issue", 0.6, ROOT_B, Decision(option=1))
    ticket(c, "email", "done", "2026-11-05T12:00:00Z", "refund_request", 0.3, ROOT_C, Decision(option=0, edits=OptionEdits(days=5)))
    ticket(c, "form", "new", "2026-11-07T08:00:00Z")

    db.ensure_conversation(c, "s1", CUID)
    db.set_outcome(c, "s1", "resolved")
    db.ensure_conversation(c, "s2", CUID)
    db.set_outcome(c, "s2", "handed_off", handed)
    db.ensure_conversation(c, "s3", OTHER)

    db.log_event(c, "s1", "get_profile", {}, True, None, {"ok": True})
    db.log_event(c, "s1", "grant_access", {"days": 30, "reason": "x"}, False, "over_max_days", "blocked: over_max_days")
    db.log_event(c, "s1", "grant_access", {"days": 3, "reason": "x"}, True, None, {"type": "grant_access", "days": 3})
    db.log_event(c, "s2", "handoff", {"reason": "refund", "summary": "wants money back"}, True, None, {"ticket_id": handed})
    db.log_event(c, "s3", "grant_access", {"days": 3, "reason": "x"}, False, "grant_limit_30d", "blocked: grant_limit_30d")

    db.record_grant(c, CUID, "premium", 3, None, "l2", "s1", {})
    db.record_grant(c, OTHER, "premium", 4, None, "l2", "s9", {})
    return c


def test_shape_on_an_empty_db(tmp_path):
    out = analytics.compute(db.connect(tmp_path / "empty.sqlite"))
    assert TOP_KEYS <= set(out)
    assert TICKET_KEYS <= set(out["tickets"]) and out["tickets"]["total"] == 0
    assert LEVEL2_KEYS <= set(out["level2"])
    assert all(out["level2"][k] == 0 for k in ("sessions", "resolved", "handed_off", "open", "grants", "days_granted"))
    assert TRIAGE_KEYS <= set(out["triage"]) and out["triage"]["triaged"] == 0
    assert out["triage"]["confidence_buckets"] == {k: 0 for k in BUCKETS}
    assert out["triage"]["accepted_option"] == {k: 0 for k in ACCEPTED}
    assert out["eval"] is None or EVAL_KEYS <= set(out["eval"])


def test_ticket_counts(conn):
    t = analytics.compute(conn)["tickets"]
    assert t["total"] == 5
    assert t["by_status"] == {"proposed": 1, "done": 3, "new": 1}
    assert t["by_source"] == {"email": 2, "form": 2, "chat_handoff": 1}
    for category, n in {"charged_after_trial": 2, "billing_issue": 1, "refund_request": 1}.items():
        assert t["by_category"][category] == n
    assert {d["day"]: d["n"] for d in t["per_day"]} == {"2026-11-05": 1, "2026-11-06": 3, "2026-11-07": 1}


def test_level2_counts(conn):
    l2 = analytics.compute(conn)["level2"]
    assert (l2["sessions"], l2["resolved"], l2["handed_off"], l2["open"]) == (3, 1, 1, 1)
    assert l2["handoff_reasons"].get("refund") == 1
    assert (l2["grants"], l2["days_granted"]) == (2, 7)
    assert l2["blocked"] == {"over_max_days": 1, "grant_limit_30d": 1}


def test_triage_counts(conn):
    tr = analytics.compute(conn)["triage"]
    assert tr["triaged"] == 4
    assert tr["confidence_buckets"] == {"0-0.5": 1, "0.5-0.7": 1, "0.7-0.9": 1, "0.9-1": 1}
    accepted = tr["accepted_option"]
    assert (accepted["1"], accepted["2"], accepted["3"], accepted["edited"]) == (1, 1, 0, 1)
    assert tr["top_root_causes"][0] == {"root_cause": ROOT_A, "n": 2}
    assert {r["root_cause"] for r in tr["top_root_causes"]} == {ROOT_A, ROOT_B, ROOT_C}


def test_eval_block_is_null_or_complete(conn):
    out = analytics.compute(conn)["eval"]
    assert out is None or EVAL_KEYS <= set(out)
