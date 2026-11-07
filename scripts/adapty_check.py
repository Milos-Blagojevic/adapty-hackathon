"""Gate 1: prove getProfile and grantAccessLevel on a seeded profile.

    python scripts/adapty_check.py lumen_443814_00042            # read only
    python scripts/adapty_check.py lumen_443814_00042 --grant    # grant 7 days of premium (a real write)

Prints the profile's access levels, subscriptions and custom attributes; with --grant, computes
`max(now, current expiry) + COMPENSATION_DAYS`, grants, then re-reads the profile to confirm.
Never prints the key.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from resolve import config  # noqa: E402
from resolve.adapty import AdaptyError, Client, access_level, compensation_expiry, format_datetime  # noqa: E402


def show(profile: dict) -> None:
    print(f"  profile_id        {profile['profile_id']}")
    print(f"  customer_user_id  {profile['customer_user_id']}")
    print(f"  total_revenue_usd {profile['total_revenue_usd']}")
    print("  access_levels:")
    for level in profile["access_levels"] or [None]:
        if level is None:
            print("    (none)")
            continue
        print(
            f"    {level['access_level_id']}: store={level.get('store')} product={level.get('store_product_id')} "
            f"starts_at={level.get('starts_at')} expires_at={level.get('expires_at')} "
            f"billing_issue={level.get('billing_issue_detected_at')} cancelled={level.get('renewal_cancelled_at')}"
        )
    print(f"  subscriptions ({len(profile['subscriptions'])}):")
    for sub in profile["subscriptions"][-3:]:
        print(
            f"    {sub.get('store_product_id')} purchased_at={sub.get('purchased_at')} expires_at={sub.get('expires_at')} "
            f"offer={sub.get('offer')} billing_issue={sub.get('billing_issue_detected_at')} reason={sub.get('cancellation_reason')}"
        )
    print(f"  non_subscriptions ({len(profile['non_subscriptions'])})")
    attrs = {a["key"]: a["value"] for a in profile["custom_attributes"]}
    print(f"  custom_attributes {attrs}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("customer_user_id")
    parser.add_argument("--grant", action="store_true", help="grant COMPENSATION_DAYS of the access level (real write)")
    args = parser.parse_args()

    client = Client(config.adapty_secret())
    now = datetime.now(timezone.utc)
    try:
        print(f"getProfile {args.customer_user_id}")
        before = client.get_profile(args.customer_user_id)
        show(before)
        if not args.grant:
            return 0
        expires_at = compensation_expiry(before, config.ACCESS_LEVEL, config.COMPENSATION_DAYS, now)
        print(f"\ngrantAccessLevel {config.ACCESS_LEVEL} expires_at={format_datetime(expires_at)} (now={format_datetime(now)})")
        granted = client.grant_access_level(args.customer_user_id, config.ACCESS_LEVEL, expires_at)
        print("response:")
        show(granted)
        print("\ngetProfile again")
        after = client.get_profile(args.customer_user_id)
        show(after)
        level = access_level(after, config.ACCESS_LEVEL)
        ok = level is not None and level.get("expires_at") and level["expires_at"].startswith(format_datetime(expires_at)[:16])
        print("\nRESULT:", "premium expires_at matches the requested expiry" if ok else "MISMATCH: see access_levels above")
        return 0 if ok else 2
    except (AdaptyError, ValueError) as exc:
        print(f"FAILED: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
