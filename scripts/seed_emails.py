"""Set fake emails on a few seeded Adapty profiles and fill our email → customer_user_id index.

    python scripts/seed_emails.py --yes               # updateProfile(email) on each, then index locally
    python scripts/seed_emails.py --yes --index-only  # only the local index, no Adapty call
    python scripts/seed_emails.py                     # prints the plan and exits; nothing runs without --yes

The public server API has no lookup by email (docs/agents.md), so Level 1 uses our own index; in
production webhooks would fill it. The Adapty write is `updateProfile` on seeded profiles only
(allowed by AGENTS.md). Emails are obviously fake (@example.com). The ids are the ones the hard
cases in data/feedback_extra.json use; x-007 arrives with the email only.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from resolve import config, db  # noqa: E402
from resolve.adapty import AdaptyError, Client  # noqa: E402

EMAILS = {
    "lumen_443814_04613": "sam.demo+04613@example.com",  # x-007: email only, paying but locked
    "lumen_443814_01656": "lee.demo+01656@example.com",  # x-009: refund + chargeback threat
    "lumen_443814_02343": "kim.demo+02343@example.com",  # x-005: says annual, profile weekly
    "lumen_443814_00772": "alex.demo+00772@example.com",  # x-001: lifetime asks for compensation
}


def index_only(conn: db.Database) -> int:
    for customer_user_id, email in EMAILS.items():
        db.index_email(conn, email, customer_user_id)
    return len(EMAILS)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", action="store_true", help="actually run (Adapty write unless --index-only)")
    parser.add_argument("--index-only", action="store_true", help="fill the local index only, no Adapty call")
    parser.add_argument("--db", type=Path, default=config.DB_PATH)
    args = parser.parse_args()

    for customer_user_id, email in EMAILS.items():
        print(f"{customer_user_id} → {email}")
    if not args.yes:
        what = "index them locally" if args.index_only else f"call Adapty updateProfile {len(EMAILS)} times, then index them locally"
        print(f"\nwould {what} ({args.db}); pass --yes to run")
        return 1

    conn = db.connect(args.db)
    if args.index_only:
        print(f"indexed {index_only(conn)} emails in {args.db}; Adapty not called")
        return 0

    client = Client(config.adapty_secret())
    failed = 0
    for customer_user_id, email in EMAILS.items():
        try:
            client.update_email(customer_user_id, email)
        except AdaptyError as exc:
            failed += 1
            print(f"{customer_user_id}: updateProfile failed: {exc}; not indexed")
            continue
        db.index_email(conn, email, customer_user_id)
        print(f"{customer_user_id}: email set and indexed")
        time.sleep(0.5)
    print(f"done: {len(EMAILS) - failed} set, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
