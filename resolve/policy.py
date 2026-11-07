"""Level 2 limits, checked in code. The model can say anything; these functions decide.

`load_config` reads the owner's agent settings (or the defaults). `check_grant` is pure: profile,
config and our own grant rows in, (allowed, reason) out. Reasons are fixed strings (docs/agents.md).
"""

from __future__ import annotations

from resolve import db
from resolve.adapty import access_level
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


def has_paid(profile: dict) -> bool:
    """Any subscription, any non-refunded one-time purchase, or revenue above zero."""
    if profile.get("subscriptions"):
        return True
    if any(not item.get("is_refund") for item in profile.get("non_subscriptions") or []):
        return True
    return float(profile.get("total_revenue_usd") or 0) > 0


def is_lifetime(profile: dict, access_level_id: str) -> bool:
    level = access_level(profile, access_level_id)
    return level is not None and level.get("expires_at") is None


def check_grant(
    profile: dict,
    cfg: AgentConfig,
    grants_30d: list[dict],
    days: int,
    granted_this_session: bool,
) -> tuple[bool, str | None]:
    """(True, None) when a grant of `days` is within the limits, else (False, reason). First failing check wins."""
    if not cfg.tools.get("grant_access"):
        return False, "tool_disabled"
    if days < 1 or days > cfg.grant.max_days:
        return False, "over_max_days"
    if cfg.grant.require_paid and not has_paid(profile):
        return False, "not_paid"
    if is_lifetime(profile, cfg.grant.access_level):
        return False, "lifetime"
    if len(grants_30d) >= cfg.grant.max_grants_per_user_30d:
        return False, "grant_limit_30d"
    if granted_this_session:
        return False, "already_granted_this_session"
    return True, None
