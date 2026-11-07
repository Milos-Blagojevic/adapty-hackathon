import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from resolve import config
from resolve.tickets import ACTIONS, CATEGORIES, CUSTOMER_PREFIX, LIVE_USERS, MIX, STATUSES, Ticket, customer_user_id, generate, offline_user_id

sys.path.insert(0, str(config.ROOT / "scripts"))
from generate_tickets import DEMO_TICKETS  # noqa: E402

OFFLINE = config.SEED_REPO / "data" / "offline"
NOW = datetime(2026, 11, 7, 15, 0, tzinfo=timezone.utc)

pytestmark = pytest.mark.skipif(not OFFLINE.exists(), reason="seed repo not present")


def test_enums_are_the_scope_lists():
    assert CATEGORIES == ("charged_after_trial", "no_access_after_purchase", "billing_issue", "refund_request", "cancel_how", "price_complaint", "other")
    assert ACTIONS == ("grant_access", "explain", "escalate_refund", "no_action")
    assert STATUSES == ("new", "triaged", "proposed", "approved", "done", "dismissed")
    assert set(MIX) == set(CATEGORIES)


def test_customer_id_round_trip():
    assert customer_user_id("u00549") == CUSTOMER_PREFIX + "00549"
    assert offline_user_id(CUSTOMER_PREFIX + "00549") == "u00549"


def test_generated_tickets_shape():
    tickets = generate(OFFLINE / "users.csv.gz", OFFLINE / "transactions.csv.gz", DEMO_TICKETS, now=NOW, seed=7)
    assert len(tickets) == 60
    assert sum(t.demo for t in tickets) == 3
    ids = [t.id for t in tickets]
    assert len(set(ids)) == 60 and {"demo-1", "demo-2", "demo-3"} <= set(ids)
    users = [t.customer_user_id for t in tickets]
    assert len(set(users)) == 60, "one ticket per user"
    assert all(u.startswith(CUSTOMER_PREFIX) and int(u[len(CUSTOMER_PREFIX):]) < LIVE_USERS for u in users)
    for rule, count in MIX.items():
        assert sum(t.rule == rule for t in tickets) == count
    for t in tickets:
        assert t.subject and t.body and "{" not in t.body and "{" not in t.subject
        created = datetime.fromisoformat(t.created_at.replace("Z", "+00:00"))
        assert created <= NOW
    assert tickets == sorted(tickets, key=lambda t: t.created_at)


def test_generation_is_deterministic():
    a = generate(OFFLINE / "users.csv.gz", OFFLINE / "transactions.csv.gz", DEMO_TICKETS, now=NOW, seed=7)
    b = generate(OFFLINE / "users.csv.gz", OFFLINE / "transactions.csv.gz", DEMO_TICKETS, now=NOW, seed=7)
    assert [t.as_dict() for t in a] == [t.as_dict() for t in b]


def test_seed_file_matches_model():
    rows = json.loads((config.ROOT / "data" / "tickets.json").read_text())
    tickets = [Ticket(**row) for row in rows]
    assert len(tickets) == 60
    assert all(t.rule in CATEGORIES for t in tickets)


def test_eval_labels_match_the_seed_file():
    """A regenerated tickets.json reshuffles ids; every label's category must still be that ticket's generator rule."""
    tickets = {row["id"]: row for row in json.loads((config.ROOT / "data" / "tickets.json").read_text())}
    cases = json.loads((config.ROOT / "data" / "eval.json").read_text())["cases"]
    assert len(cases) == 20 and len({c["ticket_id"] for c in cases}) == 20
    for case in cases:
        assert tickets[case["ticket_id"]]["rule"] == case["category"], case
        assert case["action"] in ACTIONS
