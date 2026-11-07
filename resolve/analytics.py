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
from datetime import datetime, timedelta, timezone
from collections import Counter

from resolve.adapty import format_datetime, parse_datetime
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
        "timeline": timeline(conn),
        "open_tasks": open_tasks(conn),
    }


TIMELINE_DAYS = 14


def _bucket_key(ts: str, hourly: bool) -> str:
    return ts[:13] + ":00:00Z" if hourly else ts[:10]


def timeline(conn: db.Database, now: datetime | None = None) -> dict:
    """AI vs people over time (real rows only). Hourly when everything is within 48 h, else daily, last 14 days.

    per bucket: chats Level 2 kept (resolved or answered and not handed off), chats handed to a person,
    tickets people closed in Level 1, other feedback that came in, and free days granted.
    """
    now = now or datetime.now(timezone.utc)
    since = format_datetime(now - timedelta(days=TIMELINE_DAYS))
    chats = conn.execute("SELECT outcome, created_at FROM conversations WHERE created_at >= ?", (since,)).fetchall()
    closed = conn.execute(
        "SELECT updated_at AS ts FROM tickets WHERE status IN ('done', 'dismissed') AND decision_json IS NOT NULL AND updated_at >= ?", (since,)
    ).fetchall()
    feedback = conn.execute("SELECT created_at AS ts FROM tickets WHERE source != 'chat_handoff' AND created_at >= ?", (since,)).fetchall()
    grants = conn.execute("SELECT ts, days FROM grants WHERE ts >= ?", (since,)).fetchall()
    stamps = [c["created_at"] for c in chats] + [r["ts"] for r in closed] + [r["ts"] for r in feedback] + [g["ts"] for g in grants]
    if not stamps:
        return {"bucket": "hour", "points": [], "agent_share": None}
    hourly = (now - parse_datetime(min(stamps))) <= timedelta(hours=48)
    points: dict[str, dict] = {}

    def point(ts: str) -> dict:
        key = _bucket_key(ts, hourly)
        return points.setdefault(key, {"t": key, "agent": 0, "handed_off": 0, "closed_by_people": 0, "feedback": 0, "grant_days": 0})

    for c in chats:
        point(c["created_at"])["handed_off" if c["outcome"] == "handed_off" else "agent"] += 1
    for r in closed:
        point(r["ts"])["closed_by_people"] += 1
    for r in feedback:
        point(r["ts"])["feedback"] += 1
    for g in grants:
        point(g["ts"])["grant_days"] += g["days"]
    agent = sum(p["agent"] for p in points.values())
    total_chats = agent + sum(p["handed_off"] for p in points.values())
    return {
        "bucket": "hour" if hourly else "day",
        "points": [points[k] for k in sorted(points)],
        "agent_share": round(agent / total_chats, 3) if total_chats else None,
    }


def open_tasks(conn: db.Database, now: datetime | None = None) -> list[dict]:
    """What is in flight right now: open chats, tickets Level 1 is still on, tickets waiting for a person."""
    now = now or datetime.now(timezone.utc)
    age = lambda ts: int((now - parse_datetime(ts)).total_seconds()) if ts else None  # noqa: E731
    tasks = []
    for c in conn.execute("SELECT session_id, customer_user_id, updated_at FROM conversations WHERE outcome = 'open' ORDER BY updated_at DESC LIMIT 20"):
        first = conn.execute("SELECT content FROM messages WHERE session_id = ? AND role = 'user' ORDER BY id LIMIT 1", (c["session_id"],)).fetchone()
        tasks.append({"kind": "chat", "id": c["session_id"], "title": (first["content"] if first else "")[:80], "user": c["customer_user_id"], "since": c["updated_at"], "age_s": age(c["updated_at"]), "note": "Level 2 is talking"})
    for t in conn.execute("SELECT id, subject, customer_user_id, created_at, triage_error FROM tickets WHERE status IN ('new', 'triaged') ORDER BY created_at DESC LIMIT 20"):
        tasks.append({"kind": "triage", "id": t["id"], "title": t["subject"], "user": t["customer_user_id"], "since": t["created_at"], "age_s": age(t["created_at"]), "note": "triage failed" if t["triage_error"] else "not triaged yet"})
    for t in conn.execute("SELECT id, subject, customer_user_id, created_at, triage_at FROM tickets WHERE status IN ('proposed', 'approved') ORDER BY COALESCE(triage_at, created_at) LIMIT 20"):
        since = t["triage_at"] or t["created_at"]
        tasks.append({"kind": "review", "id": t["id"], "title": t["subject"], "user": t["customer_user_id"], "since": since, "age_s": age(since), "note": "waiting for a person"})
    return tasks
