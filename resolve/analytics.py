"""Numbers for the Analyze tab (docs/agents.md, "Analyze, first version" and the `GET /api/analytics` shape).

Readings declared here (the spec does not fix them):
- `tickets.by_status` uses API statuses (DB_TO_API_STATUS); a ticket without a category counts as "uncategorized".
- `level2.grants` / `days_granted` count only Level 2 grants (`grants.source = 'l2'`).
- `level2.handoff_reasons` = `args.reason` of logged `handoff` tool calls; `blocked` = blocked_reason of disallowed calls.
- `triage.*` reads only `triage_json` (v2 Level 1 tickets), not legacy v1 proposals.
- `accepted_option`: a dismissed ticket, or a decision with a truthy `rejected` or without `option`, is "rejected";
  a decision whose `edits` has any non-null field is "edited"; otherwise `option + 1` (0-based index in Decision).
"""

from __future__ import annotations

import json
from collections import Counter

from resolve import config, db
from resolve.models import DB_TO_API_STATUS

EVAL_RESULTS = config.ROOT / "data" / "eval_results.json"
BUCKETS = (("0-0.5", 0.5), ("0.5-0.7", 0.7), ("0.7-0.9", 0.9), ("0.9-1", None))


def _bucket(confidence: float) -> str:
    for name, upper in BUCKETS:
        if upper is None or confidence < upper:
            return name
    raise AssertionError("unreachable")


def _accepted(status: str, decision: dict | None) -> str | None:
    if status == "dismissed":
        return "rejected"
    if decision is None:
        return None
    if decision.get("rejected") or "option" not in decision:
        return "rejected"
    if any(v is not None for v in (decision.get("edits") or {}).values()):
        return "edited"
    return str(decision["option"] + 1)


def _eval() -> dict | None:
    if not EVAL_RESULTS.exists():
        return None
    summary = json.loads(EVAL_RESULTS.read_text()).get("summary") or {}
    return {k: summary.get(k) for k in ("model", "cases", "category_correct", "action_correct")}


def compute(conn: db.Database) -> dict:
    tickets = conn.execute("SELECT status, source, category, created_at, triage_json, decision_json FROM tickets").fetchall()

    by_status = Counter(DB_TO_API_STATUS.get(t["status"], t["status"]) for t in tickets)
    per_day = Counter(t["created_at"][:10] for t in tickets)

    buckets = {name: 0 for name, _ in BUCKETS}
    accepted = {"1": 0, "2": 0, "3": 0, "edited": 0, "rejected": 0}
    root_causes: Counter = Counter()
    triaged = 0
    for t in tickets:
        if t["triage_json"]:
            tri = json.loads(t["triage_json"])
            triaged += 1
            if isinstance(tri.get("confidence"), (int, float)):
                buckets[_bucket(tri["confidence"])] += 1
            if tri.get("root_cause"):
                root_causes[tri["root_cause"]] += 1
        key = _accepted(t["status"], json.loads(t["decision_json"]) if t["decision_json"] else None)
        if key in accepted:
            accepted[key] += 1

    outcomes = Counter({r[0]: r[1] for r in conn.execute("SELECT outcome, COUNT(*) FROM conversations GROUP BY outcome")})
    events = db.list_events(conn)
    handoff_reasons = Counter(str(e["args"].get("reason")) for e in events if e["tool"] == "handoff" and e["allowed"])
    blocked = Counter(e["blocked_reason"] or "unknown" for e in events if not e["allowed"])
    grants, days = conn.execute("SELECT COUNT(*), COALESCE(SUM(days), 0) FROM grants WHERE source = 'l2'").fetchone()

    return {
        "tickets": {
            "total": len(tickets),
            "by_status": dict(by_status),
            "by_source": dict(Counter(t["source"] for t in tickets)),
            "by_category": dict(Counter(t["category"] or "uncategorized" for t in tickets)),
            "per_day": [{"day": day, "n": n} for day, n in sorted(per_day.items())],
        },
        "level2": {
            "sessions": sum(outcomes.values()),
            "resolved": outcomes["resolved"],
            "handed_off": outcomes["handed_off"],
            "open": outcomes["open"],
            "handoff_reasons": dict(handoff_reasons),
            "grants": grants,
            "days_granted": days,
            "blocked": dict(blocked),
        },
        "triage": {
            "triaged": triaged,
            "confidence_buckets": buckets,
            "accepted_option": accepted,
            "top_root_causes": [{"root_cause": rc, "n": n} for rc, n in root_causes.most_common(5)],
        },
        "eval": _eval(),
    }
