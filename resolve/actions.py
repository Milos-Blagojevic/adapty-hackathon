"""The only place that writes to Adapty for an action, for both agents.

Level 2 calls it after `policy.check_grant` allowed the grant (source "l2", ref = session_id);
Level 1 calls it when the support person accepts an option (source "l1", ref = ticket_id).
It does what it is told: the limits are checked by the callers, not here.
"""

from __future__ import annotations

from datetime import datetime, timezone

from resolve import db, policy
from resolve.adapty import AdaptyError, compensation_expiry, format_datetime
from resolve.models import ACTIONS

STAMP_KEY = "last_support_ticket"


def execute(services, customer_user_id: str, action: str, params: dict, source: str, ref: str) -> dict:
    """Run `action` for the user. Returns `{"type", ...}`; raises AdaptyError when the grant fails.

    grant_access: fresh profile → expiry (`compensation_expiry`) → grant → stamp `last_support_ticket`
    (a stamp failure is recorded as `stamp_error`, never fatal) → `db.record_grant`.
    Other actions make no Adapty call. Demo mode or no Adapty client: no call, `{"type", "demo": True}`,
    and no grant row.
    """
    if action not in ACTIONS:
        raise ValueError(f"unknown action: {action}")
    grant = policy.load_config(services.conn).grant
    days = params.get("days")
    if days is None:
        days = grant.max_days
    if action == "grant_access" and (not isinstance(days, int) or isinstance(days, bool) or days < 1):
        raise ValueError("grant_access needs days >= 1")

    if services.demo or services.adapty is None:
        result = {"type": action, "demo": True, "note": "Demo mode: no Adapty call was made."}
        if action == "grant_access":
            result.update(access_level=grant.access_level, days=days)
        return result
    if action != "grant_access":
        return {"type": action}

    now = datetime.now(timezone.utc)
    profile = services.adapty.get_profile(customer_user_id)
    try:
        expires_at = compensation_expiry(profile, grant.access_level, days, now)
    except ValueError as exc:
        raise AdaptyError(str(exc), code="lifetime") from None
    response = services.adapty.grant_access_level(customer_user_id, grant.access_level, expires_at)
    # From here on the grant happened in Adapty: record it whatever the stamp does.
    result = {
        "type": action,
        "access_level": grant.access_level,
        "days": days,
        "expires_at": format_datetime(expires_at),
        "profile_id": response.get("profile_id") if isinstance(response, dict) else None,
    }
    try:
        services.adapty.update_profile(customer_user_id, {STAMP_KEY: ref})
        result["stamped"] = {STAMP_KEY: ref}
    except Exception as exc:  # noqa: BLE001 - an AdaptyError or anything else: recorded, never fatal
        result["stamp_error"] = str(exc) if isinstance(exc, AdaptyError) else f"{exc.__class__.__name__}: {exc}"
    db.record_grant(services.conn, customer_user_id, grant.access_level, days, result["expires_at"], source, ref, response)
    return result
