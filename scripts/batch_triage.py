"""One pass of Level 1 triage over every open ticket without a triage (paid; ask the owner first).

    python scripts/batch_triage.py [--db data/resolve.sqlite] [--limit N]

Per ticket: live getProfile, then the triage tool loop, never beyond triage.MAX_CALLS model calls per
ticket across runs. Tickets that already have a triage (or a v1 proposal), or are
done or dismissed, are skipped, so a re-run costs nothing for them. Logs rejection reasons only.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from resolve import config, db, llm, workflow  # noqa: E402
from resolve.adapty import Client  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=config.DB_PATH)
    parser.add_argument("--limit", type=int, default=None, help="stop after N tickets (for a trial run)")
    args = parser.parse_args()

    conn = db.connect(args.db)
    db.seed(conn, config.ROOT / "data" / "tickets.json")
    services = workflow.Services(conn=conn, demo=False, adapty=Client(config.adapty_secret()), llm=llm.make_llm())
    todo = [t for t in db.list_tickets(conn) if t["status"] in workflow.OPEN_STATUSES and not t["triage"] and not t["proposal"]]
    if args.limit:
        todo = todo[: args.limit]
    print(f"{len(todo)} tickets to triage with {services.llm.model}\n")

    calls_before = sum(t["model_calls"] for t in db.list_tickets(conn))
    outcomes: Counter = Counter()
    for ticket in sorted(todo, key=lambda t: t["created_at"]):
        opened = workflow.open_ticket(services, ticket["id"])
        if not opened["profile"]:
            print(f"  {ticket['id']}: profile failed: {opened['profile_error']}")
            outcomes["profile_error"] += 1
            continue
        result = workflow.propose(services, ticket["id"])
        if result["triage"]:
            t = result["triage"]
            outcomes[t["category"]] += 1
            print(f"  {ticket['id']:7s} {result['model_calls']} call(s)  {t['category']:26s} {t['options'][0]['action']:16s} {t['confidence']:.2f}")
        else:
            outcomes["triage_error"] += 1
            print(f"  {ticket['id']}: {result['triage_error']}")
        time.sleep(0.5)
    calls_after = sum(t["model_calls"] for t in db.list_tickets(conn))
    print(f"\n{calls_after - calls_before} model calls this run. Outcomes: {dict(outcomes)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
