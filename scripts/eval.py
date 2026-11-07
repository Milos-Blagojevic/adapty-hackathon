"""Eval: category and action accuracy of the Level 2 agent on the 20 hand-labelled tickets.

    python scripts/eval.py              # reuse proposals cached in the DB; call the model only for the rest
    python scripts/eval.py --fresh      # re-run the agent on all 20 (paid: up to 40 Anthropic calls)

Profiles come from the DB cache when present, else live from Adapty. Writes data/eval_results.json
and prints the table. Ask the owner before running: it spends money.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from resolve import agent, config, db  # noqa: E402
from resolve.adapty import AdaptyError, Client  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fresh", action="store_true", help="ignore cached proposals")
    parser.add_argument("--db", type=Path, default=config.DB_PATH)
    parser.add_argument("--out", type=Path, default=config.ROOT / "data" / "eval_results.json")
    args = parser.parse_args()

    cases = json.loads((config.ROOT / "data" / "eval.json").read_text())["cases"]
    conn = db.connect(args.db)
    db.seed(conn, config.ROOT / "data" / "tickets.json")
    adapty = Client(config.adapty_secret())
    client = agent.make_client()
    model = agent.resolve_model(client)
    print(f"model {model}, {len(cases)} cases, {'fresh' if args.fresh else 'cached where possible'}\n")

    rows = []
    calls = 0
    for case in cases:
        ticket = db.get_ticket(conn, case["ticket_id"])
        if ticket is None:
            print(f"{case['ticket_id']}: not in the DB, skipped")
            continue
        profile = ticket["profile"]
        if profile is None:
            try:
                profile = adapty.get_profile(ticket["customer_user_id"])
                time.sleep(0.5)
            except AdaptyError as exc:
                print(f"{ticket['id']}: profile failed: {exc}")
                continue
        source = "cached"
        if ticket["proposal"] and not args.fresh:
            proposal = agent.parse_proposal(ticket["proposal"])
        else:
            source = "fresh"
            try:
                proposal, made = agent.propose(client, model, ticket, profile, agent.budget(2))
                calls += made
            except agent.AgentError as exc:
                calls += exc.calls
                print(f"{ticket['id']}: agent failed: {exc}")
                rows.append({"ticket_id": ticket["id"], "error": str(exc), **{k: case[k] for k in ("category", "action")}})
                continue
        row = {
            "ticket_id": ticket["id"],
            "expected_category": case["category"],
            "got_category": proposal.category,
            "expected_action": case["action"],
            "got_action": proposal.action,
            "confidence": proposal.confidence,
            "source": source,
        }
        rows.append(row)
        mark = ("✓" if row["got_category"] == case["category"] else "✗") + ("✓" if row["got_action"] == case["action"] else "✗")
        print(f"{mark} {ticket['id']:7s} {case['category']:26s}→ {proposal.category:26s} {case['action']:16s}→ {proposal.action:16s} {proposal.confidence:.2f} {source}")

    scored = [r for r in rows if "got_category" in r]
    cat = sum(r["got_category"] == r["expected_category"] for r in scored)
    act = sum(r["got_action"] == r["expected_action"] for r in scored)
    both = sum(r["got_category"] == r["expected_category"] and r["got_action"] == r["expected_action"] for r in scored)
    summary = {"model": model, "cases": len(cases), "scored": len(scored), "category_correct": cat, "action_correct": act, "both_correct": both, "model_calls": calls}
    print(f"\ncategory {cat}/{len(scored)}  action {act}/{len(scored)}  both {both}/{len(scored)}  ({calls} model calls this run)")
    args.out.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2) + "\n")
    print(f"→ {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
