"""One pass of Level 2 over every open ticket without a proposal (paid; ask the owner first).

    python scripts/batch_triage.py [--db data/resolve.sqlite] [--limit N]

Per ticket: live getProfile, then at most 2 model calls (the second only after a rejected output),
never beyond agent.MAX_CALLS per ticket across runs. Tickets that already have a proposal, or are
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

from resolve import agent, config, db, workflow  # noqa: E402
from resolve.adapty import Client  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=config.DB_PATH)
    parser.add_argument("--limit", type=int, default=None, help="stop after N tickets (for a trial run)")
    args = parser.parse_args()

    conn = db.connect(args.db)
    db.seed(conn, config.ROOT / "data" / "tickets.json")
    client = agent.make_client()
    services = workflow.Services(conn=conn, demo=False, adapty=Client(config.adapty_secret()), anthropic=client, model=agent.resolve_model(client))
    todo = [t for t in db.list_tickets(conn) if t["status"] in workflow.OPEN_STATUSES and not t["proposal"]]
    if args.limit:
        todo = todo[: args.limit]
    print(f"{len(todo)} tickets to triage with {services.model}\n")

    calls_before = sum(t["model_calls"] for t in db.list_tickets(conn))
    outcomes: Counter = Counter()
    for ticket in sorted(todo, key=lambda t: t["created_at"]):
        opened = workflow.open_ticket(services, ticket["id"])
        if not opened["profile"]:
            print(f"  {ticket['id']}: profile failed: {opened['profile_error']}")
            outcomes["profile_error"] += 1
            continue
        result = workflow.propose(services, ticket["id"])
        if result["proposal"]:
            p = result["proposal"]
            outcomes[p["category"]] += 1
            print(f"  {ticket['id']:7s} {result['model_calls']} call(s)  {p['category']:26s} {p['action']:16s} {p['confidence']:.2f}")
        else:
            outcomes["agent_error"] += 1
            print(f"  {ticket['id']}: {result['proposal_error']}")
        time.sleep(0.5)
    calls_after = sum(t["model_calls"] for t in db.list_tickets(conn))
    print(f"\n{calls_after - calls_before} model calls this run. Outcomes: {dict(outcomes)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
