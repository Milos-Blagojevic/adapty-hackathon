"""policy.check_grant / policy.load_config, from the Internal interfaces table in docs/agents.md."""

import pytest

from resolve import db, policy
from resolve.models import AgentConfig
from tests.fakes_v2 import CUID, EXPIRED, lifetime_profile, paid_profile, set_config, unpaid_profile


def cfg(**changes) -> AgentConfig:
    base = AgentConfig().model_dump()
    for key, value in changes.items():
        base[key] = {**base[key], **value} if isinstance(value, dict) else value
    return AgentConfig.model_validate(base)


def grant_row(days=3, source="l2", ref="s-old"):
    return {"customer_user_id": CUID, "access_level": "premium", "days": days, "expires_at": None, "source": source, "ref": ref, "response_json": None, "ts": "2026-11-01T00:00:00Z"}


DISABLED = {"tools": {"grant_access": False}}


def test_within_limits_is_allowed():
    assert policy.check_grant(paid_profile(), cfg(), [], 3, False) == (True, None)


def test_max_days_is_inclusive():
    assert policy.check_grant(paid_profile(), cfg(), [], 7, False) == (True, None)
    assert policy.check_grant(paid_profile(), cfg(), [], 8, False) == (False, "over_max_days")
    assert policy.check_grant(paid_profile(), cfg(grant={"max_days": 3}), [], 4, False) == (False, "over_max_days")


@pytest.mark.parametrize(
    "prof,config,grants,days,session,reason",
    [
        (paid_profile(), cfg(**DISABLED), [], 1, False, "tool_disabled"),
        (paid_profile(), cfg(), [], 30, False, "over_max_days"),
        (unpaid_profile(), cfg(), [], 3, False, "not_paid"),
        (lifetime_profile(), cfg(), [], 3, False, "lifetime"),
        (paid_profile(), cfg(), [grant_row()], 3, False, "grant_limit_30d"),
        (paid_profile(), cfg(grant={"max_grants_per_user_30d": 2}), [], 3, True, "already_granted_this_session"),
    ],
    ids=["tool_disabled", "over_max_days", "not_paid", "lifetime", "grant_limit_30d", "already_granted_this_session"],
)
def test_each_reason_alone(prof, config, grants, days, session, reason):
    assert policy.check_grant(prof, config, grants, days, session) == (False, reason)


def test_reasons_come_in_the_documented_order():
    """Several limits fail at once: the first in the table's order is the reason."""
    never_paid_lifetime = lifetime_profile()
    never_paid_lifetime["total_revenue_usd"] = 0
    never_paid_lifetime["non_subscriptions"] = []
    grants = [grant_row()]
    assert policy.check_grant(never_paid_lifetime, cfg(**DISABLED), grants, 30, True) == (False, "tool_disabled")
    assert policy.check_grant(never_paid_lifetime, cfg(), grants, 30, True) == (False, "over_max_days")
    assert policy.check_grant(never_paid_lifetime, cfg(), grants, 3, True) == (False, "not_paid")
    assert policy.check_grant(lifetime_profile(), cfg(), grants, 3, True) == (False, "lifetime")
    assert policy.check_grant(paid_profile(), cfg(), grants, 3, True) == (False, "grant_limit_30d")
    assert policy.check_grant(paid_profile(), cfg(), [], 3, True) == (False, "already_granted_this_session")


def test_grant_limit_counts_the_given_grants():
    two = cfg(grant={"max_grants_per_user_30d": 2})
    assert policy.check_grant(paid_profile(), two, [grant_row()], 3, False) == (True, None)
    assert policy.check_grant(paid_profile(), two, [grant_row(), grant_row(source="l1", ref="t-001")], 3, False) == (False, "grant_limit_30d")
    assert policy.check_grant(paid_profile(), cfg(grant={"max_grants_per_user_30d": 0}), [], 3, False) == (False, "grant_limit_30d")


def test_require_paid_off_lets_an_unpaid_user_through():
    assert policy.check_grant(unpaid_profile(), cfg(grant={"require_paid": False}), [], 3, False) == (True, None)


def test_paid_user_with_expired_access_is_allowed():
    """The eval's happy path: paid but locked → grant within the limit."""
    assert policy.check_grant(paid_profile(access_levels=[EXPIRED]), cfg(), [], 3, False) == (True, None)


def test_check_grant_is_pure(http):
    prof, grants = paid_profile(), [grant_row()]
    before = (repr(prof), repr(grants))
    policy.check_grant(prof, cfg(), grants, 3, False)
    assert (repr(prof), repr(grants)) == before
    assert len(http.calls) == 0


# --- load_config ------------------------------------------------------------------------------------


def test_load_config_defaults_when_nothing_stored(tmp_path):
    conn = db.connect(tmp_path / "c.sqlite")
    loaded = policy.load_config(conn)
    assert isinstance(loaded, AgentConfig)
    assert loaded == AgentConfig()
    assert loaded.grant.max_days == 7 and loaded.tools == {"grant_access": True} and loaded.max_turns == 8


def test_load_config_returns_the_stored_config(tmp_path):
    conn = db.connect(tmp_path / "c.sqlite")
    set_config(conn, tone="formal", tools={"grant_access": False}, grant={"max_days": 3, "max_grants_per_user_30d": 2}, max_turns=4)
    loaded = policy.load_config(conn)
    assert loaded.tone == "formal"
    assert loaded.tools["grant_access"] is False
    assert (loaded.grant.max_days, loaded.grant.max_grants_per_user_30d, loaded.grant.access_level) == (3, 2, "premium")
    assert loaded.max_turns == 4


# "Paying users only" means paying now (owner's call, Sun morning): a past payer whose subscription ended is not eligible.
def _with_sub(**changes):
    p = paid_profile()
    p["subscriptions"] = [{**p["subscriptions"][0], **changes}]
    return p


@pytest.mark.parametrize(
    "changes",
    [
        {"expires_at": "2026-10-01T00:00:00+00:00", "is_active": False},
        {"is_refund": True},
        {"offer": {"category": "introductory", "type": "free_trial", "id": None}},
    ],
    ids=["expired", "refunded", "free_trial"],
)
def test_past_or_trial_subscription_is_not_paying(changes):
    assert policy.check_grant(_with_sub(**changes), cfg(), [], 3, False) == (False, "not_paid")


def test_revenue_alone_is_not_paying():
    p = paid_profile()
    p["subscriptions"] = []
    assert p["total_revenue_usd"] > 0
    assert policy.check_grant(p, cfg(), [], 3, False) == (False, "not_paid")


def test_paid_offer_after_trial_is_paying():
    assert policy.check_grant(_with_sub(offer={"category": "promotional", "type": "pay_as_you_go"}), cfg(), [], 3, False) == (True, None)


def test_require_paid_off_lets_past_payers_through():
    expired = _with_sub(expires_at="2026-10-01T00:00:00+00:00")
    assert policy.check_grant(expired, cfg(grant={"require_paid": False}), [], 3, False) == (True, None)
