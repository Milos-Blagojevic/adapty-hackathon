"""Generate data/tickets.json: three hand-written demo tickets plus generated ones (60 in total).

    python scripts/generate_tickets.py [--seed 7] [--now 2026-11-07T15:00:00Z] [--out data/tickets.json] [--force]

Reads the offline CSVs from the seed repo (`SEED_REPO`, default ~/hackathon/adapty-seed-data-hack).
No network, no keys. Dates in the text are on the live profile's clock (see `tickets.LIVE_SHIFT`).

Deterministic for a given seed and `--now` (the clock decides which events count as recent and
how old each ticket is). The committed file was generated on Sat 7 Nov with the wall clock, before
`--now` existed; regenerating replaces ticket ids and text, so `data/eval.json` must be relabelled
(tests/test_tickets.py enforces it) and the live DB's cached proposals no longer match. The script
therefore refuses to overwrite an existing file without `--force`.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from resolve import config  # noqa: E402
from resolve.tickets import Ticket, generate  # noqa: E402

# Hand-written so they read well on stage. Profiles checked live on Sat 7 Nov:
#   02021  US, Play, meta_ads, onb_weekly: weekly 3-day trial from Nov 3, charged $6.99 on Nov 6, renewing to Nov 13
#          (00549, the Gate 1 profile, already carries a grant, so it is not the demo profile)
#   00014  US, iOS, apple_search_ads, settings flow: annual $49.99 bought Nov 4, active until Nov 2027
#   02138  US, Play, meta_ads, onb_weekly: weekly trial from Nov 1, billing error at trial end on Nov 4, no revenue
DEMO_TICKETS = [
    Ticket(
        id="demo-1",
        customer_user_id="lumen_443814_02021",
        subject="Charged after my free trial",
        body=(
            "I downloaded Lumen on Nov 3 for the 3-day free trial. This morning I got charged $6.99. "
            "The trial screen never said it turns into a weekly plan, I thought I had a week to decide. "
            "I don't want to pay $7 every week for a few filters. I feel tricked. What can you do about this?"
        ),
        created_at="2026-11-06T18:40:00Z",
        demo=True,
        rule="charged_after_trial",
    ),
    Ticket(
        id="demo-2",
        customer_user_id="lumen_443814_00014",
        subject="Paid $49.99, app still locked",
        body=(
            "I bought the annual plan yesterday for $49.99 (Apple receipt attached) and Lumen still shows the upgrade screen "
            "every time I open an AI tool. Restore Purchases says there is nothing to restore. I need this for a client shoot tomorrow."
        ),
        created_at="2026-11-05T14:12:00Z",
        demo=True,
        rule="no_access_after_purchase",
    ),
    Ticket(
        id="demo-3",
        customer_user_id="lumen_443814_02138",
        subject="Payment problem after trial",
        body=(
            "My 3-day trial ended on Nov 4 and instead of charging me the app now says there is a problem with my payment and "
            "locked all my edits. My card is fine, I bought groceries with it this morning. I actually wanted to keep the subscription. What do I do?"
        ),
        created_at="2026-11-05T09:20:00Z",
        demo=True,
        rule="billing_issue",
    ),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--now", default="2026-11-07T15:00:00Z", help="the clock the generator runs on (ISO 8601 UTC)")
    parser.add_argument("--out", type=Path, default=config.ROOT / "data" / "tickets.json")
    parser.add_argument("--force", action="store_true", help="overwrite an existing file (invalidates eval labels and cached proposals)")
    args = parser.parse_args()
    if args.out.exists() and not args.force:
        print(f"{args.out} exists; pass --force to replace it (then relabel data/eval.json and re-run the batch)")
        return 1

    now = datetime.fromisoformat(args.now.replace("Z", "+00:00"))
    offline = config.SEED_REPO / "data" / "offline"
    tickets = generate(offline / "users.csv.gz", offline / "transactions.csv.gz", DEMO_TICKETS, now=now, seed=args.seed)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps([t.as_dict() for t in tickets], indent=2, ensure_ascii=False) + "\n")

    print(f"{len(tickets)} tickets → {args.out}")
    for rule, count in Counter(t.rule for t in tickets).most_common():
        print(f"  {rule:26s} {count}")
    print(f"  created_at {tickets[0].created_at} … {tickets[-1].created_at}")
    print(f"  demo: {[t.id for t in tickets if t.demo]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
