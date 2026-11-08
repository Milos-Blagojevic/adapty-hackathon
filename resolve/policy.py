"""Level 2 limits, checked in code. The model can say anything; these functions decide.

`load_config` reads the owner's agent settings (or the defaults). `check_grant` is pure: profile,
config and our own grant rows in, (allowed, reason) out. Reasons are fixed strings (docs/agents.md).
"""

from __future__ import annotations

from datetime import datetime, timezone

from resolve import db
from resolve.adapty import access_level, parse_datetime
from resolve.models import AgentConfig


def load_config(conn: db.Database) -> AgentConfig:
    """The stored config, validated; the defaults when none is stored.

    An invalid stored config raises (pydantic.ValidationError) instead of falling back to the
    defaults: the defaults could switch on a tool the owner switched off.
    """
    stored = db.get_config(conn)
    if stored is None:
        return AgentConfig()
    return AgentConfig.model_validate(stored)


def is_trial(sub: dict) -> bool:
    """The current period is a free trial (Adapty `offer` type or category `free_trial`)."""
    offer = sub.get("offer")
    if isinstance(offer, dict):
        return "free_trial" in (offer.get("type"), offer.get("category"), offer.get("offer_type"))
    return offer == "free_trial"


def has_paid(profile: dict, now: datetime | None = None) -> bool:
    """Paying right now: a store subscription that is not refunded, not in a free trial and not expired.

    Users who paid in the past but whose subscription has ended do not count (owner's call: safer).
    A non-refunded one-time purchase counts (it never expires; a lifetime user is then blocked as `lifetime`).
    """
    now = now or datetime.now(timezone.utc)
    for sub in profile.get("subscriptions") or []:
        if sub.get("is_refund") or is_trial(sub):
            continue
        expires_at = parse_datetime(sub.get("expires_at"))
        if expires_at is not None and expires_at > now:
            return True
    return any(not item.get("is_refund") for item in profile.get("non_subscriptions") or [])


def is_lifetime(profile: dict, access_level_id: str) -> bool:
    level = access_level(profile, access_level_id)
    return level is not None and level.get("expires_at") is None


def check_grant(
    profile: dict,
    cfg: AgentConfig,
    grants_30d: list[dict],
    days: int,
    granted_this_session: bool,
    now: datetime | None = None,
) -> tuple[bool, str | None]:
    """(True, None) when a grant of `days` is within the limits, else (False, reason). First failing check wins."""
    if not cfg.tools.get("grant_access"):
        return False, "tool_disabled"
    if days < 1 or days > cfg.grant.max_days:
        return False, "over_max_days"
    if cfg.grant.require_paid and not has_paid(profile, now):
        return False, "not_paid"
    if is_lifetime(profile, cfg.grant.access_level):
        return False, "lifetime"
    if len(grants_30d) >= cfg.grant.max_grants_per_user_30d:
        return False, "grant_limit_30d"
    if granted_this_session:
        return False, "already_granted_this_session"
    return True, None
