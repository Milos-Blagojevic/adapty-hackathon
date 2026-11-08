"""Eval v2: Level 1 triage on data/eval.json, and with --l2 the Level 2 chats in data/l2_scenarios.json.

    python scripts/eval.py                    # prints how many model calls it may make, runs nothing
    python scripts/eval.py --yes              # Level 1: 20 cases + hard cases (paid)
    python scripts/eval.py --yes --l2         # + Level 2 scenarios (paid)
    python scripts/eval.py --yes --only t-017,x-004,l2-repeat

Runs on a scratch copy of --db (default data/demo.sqlite, which has the profiles the labels were written
against); the source DB is never written. The hard-case feedback (data/feedback_extra.json) and the
email index (scripts/seed_emails.py EMAILS) are added to the copy. Profiles come from that cache (a
customer id not in it = profile not found); --live-profiles reads them from Adapty instead. Adapty
writes are never made: the client is wrapped so grant_access_level / update_profile raise AdaptyError,
and a grant the code tried to make is counted as "allowed" (would grant).

Level 1: triage.run(force=True) per case; scores category and options[0].action, and reports the
quality signals the score misses: a `forbidden_actions` option anywhere or a `forbidden_first` action
first, reply length in words, pronoun slips, evidence not in the facts. Cases with `feedback` (and
`chat`, for a handed-off chat) are inserted into the scratch DB; `profiles` in data/eval.json override
the cached profile of those customers. Level 2: each
scenario's user turns through frontline.chat with a fresh session id; scores the grant outcome
(allowed / blocked:<reason> / none) and the handoff reason. Writes data/eval_results.json; the
scratch DB is kept (path printed) so the transcripts can be inspected.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from resolve import config, db  # noqa: E402
from resolve.adapty import AdaptyError  # noqa: E402

import import_feedback  # noqa: E402
import seed_emails  # noqa: E402

L1_CALL_CAP = 8  # triage: db.reserve_model_call cap per ticket (docs/agents.md)
L2_CALLS_PER_MESSAGE = 4  # frontline: ≤4 model calls per user message
READ_ONLY = "eval_read_only"


class ReadOnlyAdapty:
    """Looks like adapty.Client for reads; every write raises AdaptyError, grants are remembered as attempts."""

    def __init__(self, read_profile):
        self._read_profile = read_profile
        self.attempts: list[dict] = []

    def get_profile(self, customer_user_id: str) -> dict:
        return self._read_profile(customer_user_id)

    def grant_access_level(self, customer_user_id, access_level_id, expires_at, starts_at=None):
        self.attempts.append({"customer_user_id": customer_user_id, "access_level_id": access_level_id, "expires_at": str(expires_at)})
        raise AdaptyError("Adapty writes are disabled during eval", code=READ_ONLY)

    def update_profile(self, customer_user_id, custom_attributes):
        raise AdaptyError("Adapty writes are disabled during eval", code=READ_ONLY)

    def update_email(self, customer_user_id, email):
        raise AdaptyError("Adapty writes are disabled during eval", code=READ_ONLY)


def cached_profiles(conn: db.Database) -> dict[str, dict]:
    """customer_user_id → the latest profile cached on any of its tickets (snapshot taken before the run)."""
    rows = conn.execute("SELECT customer_user_id, profile_json FROM tickets WHERE profile_json IS NOT NULL ORDER BY profile_fetched_at")
    return {r["customer_user_id"]: json.loads(r["profile_json"]) for r in rows}


def profile_reader(profiles: dict[str, dict]):
    def read(customer_user_id: str) -> dict:
        if customer_user_id not in profiles:
            # STUB: the real Adapty answer for an unknown customer id is assumed to be a 404; code not verified
            raise AdaptyError("profile not found (eval cache)", status=404, code="profile_does_not_exist")
        return json.loads(json.dumps(profiles[customer_user_id]))  # a fresh copy per call

    return read


def scratch_copy(source: Path) -> Path:
    target = Path(tempfile.mkdtemp(prefix="resolve-eval-")) / "eval.sqlite"
    src = sqlite3.connect(source)
    dst = sqlite3.connect(target)
    src.backup(dst)  # consistent copy, WAL included; the source is only read
    src.close()
    dst.close()
    return target


def matches(expected, got) -> bool:
    if expected == "any":
        return True
    if isinstance(expected, list):
        return got in expected
    return got == expected


def insert_cases(conn: db.Database, cases: list[dict], access_level: str) -> int:
    """Hard cases that carry their own feedback (data/eval.json `feedback`, `chat`, `setup`) → tickets in the scratch DB."""
    inserted = 0
    for case in cases:
        fb = case.get("feedback")
        if not fb or db.get_ticket(conn, case["ticket_id"]) is not None:
            continue
        text, session_id = fb.get("text", ""), None
        if case.get("chat"):
            chat = case["chat"]
            session_id = chat["session_id"]
            db.ensure_conversation(conn, session_id, fb["customer_user_id"])
            for role, content in chat["messages"]:
                db.append_message(conn, session_id, role, content)
            transcript = "\n".join(f"{role}: {content}" for role, content in chat["messages"])
            text = f"Handoff reason: {chat['reason']}\nSummary: {chat['summary']}\n\nConversation:\n{transcript}\n\nTools tried:\n- none"
        db.insert_feedback(
            conn,
            text,
            source=fb["source"],
            customer_user_id=fb.get("customer_user_id"),
            email=fb.get("email"),
            session_id=session_id,
            subject=fb.get("subject") or (f"Chat: {case['chat']['reason']}" if case.get("chat") else None),
            ticket_id=case["ticket_id"],
        )
        if session_id:
            db.set_outcome(conn, session_id, "handed_off", case["ticket_id"])
        prior = case.get("setup", {}).get("prior_grant")
        if prior:
            db.record_grant(conn, fb["customer_user_id"], access_level, prior["days"], prior.get("expires_at"), prior["source"], prior["ref"], {"eval_setup": True})
        inserted += 1
    return inserted


PRONOUN = re.compile(r"\b(he|she|him|his|her|hers|himself|herself)\b", re.I)


def pronoun_slips(result: dict) -> list[str]:
    """Gendered pronouns in what the support person reads (summary, root cause, evidence); the spec says 'the user'."""
    texts = [result.get("summary") or "", result.get("root_cause") or "", *(result.get("evidence") or [])]
    return [m.group(0) for t in texts for m in PRONOUN.finditer(t)]


def run_l1(services, conn: db.Database, ro: ReadOnlyAdapty, cases: list[dict]) -> list[dict]:
    from resolve import triage

    rows = []
    for case in cases:
        tid = case["ticket_id"]
        if db.get_ticket(conn, tid) is None:
            print(f"   {tid}: not in the DB, skipped")
            rows.append({"ticket_id": tid, "error": "not in the DB"})
            continue
        # fresh start on the scratch copy: no cached triage, the full call budget
        db.update_ticket(conn, tid, status="new", model_calls=0, triage_json=None, triage_at=None, triage_error=None)
        attempts_before = len(ro.attempts)
        error = None
        try:
            triage.run(services, tid, force=True)
        except Exception as exc:  # noqa: BLE001 — one failed case must not stop the eval
            error = f"{exc.__class__.__name__}: {exc}"
        ticket = db.get_ticket(conn, tid)
        result = ticket["triage"]
        row = {"ticket_id": tid, "model_calls": ticket["model_calls"], "adapty_write_attempts": len(ro.attempts) - attempts_before}
        if not result or not result.get("options"):
            row["error"] = error or ticket["triage_error"] or "no triage stored"
            print(f"✗✗ {tid:7s} error: {row['error']}")
            rows.append(row)
            continue
        categories = case.get("acceptable_categories", [case["category"]])
        actions = case.get("acceptable_actions", [case["expected_action"]])
        got_category, got_action = result["category"], result["options"][0]["action"]
        all_actions = [o["action"] for o in result["options"]]
        forbidden = [a for a in all_actions if a in case.get("forbidden_actions", [])]
        if got_action in case.get("forbidden_first", []):
            forbidden.append(f"{got_action} first")
        words = [len(o["reply"].split()) for o in result["options"] if o["action"] != "no_reply"]
        row.update(
            expected_category=case["category"],
            got_category=got_category,
            category_ok=got_category in categories,
            expected_action=case["expected_action"],
            got_action=got_action,
            action_ok=got_action in actions,
            confidence=result.get("confidence"),
            customer_user_id=result.get("customer_user_id"),
            evidence=len(result.get("evidence") or []),
            evidence_ungrounded=ungrounded(services, conn, ticket, result),
            actions=all_actions,
            forbidden=forbidden,
            reply_words=words,
            style=pronoun_slips(result),
        )
        rows.append(row)
        mark = ("✓" if row["category_ok"] else "✗") + ("✓" if row["action_ok"] else "✗")
        flags = (f"  FORBIDDEN {forbidden}" if forbidden else "") + (f"  style {len(row['style'])}" if row["style"] else "")
        print(f"{mark} {tid:7s} {case['category']:26s}→ {got_category:26s} {case['expected_action']:18s}→ {' / '.join(all_actions):52s} {row['confidence']}  words {max(words, default=0)}{flags}")
    return rows


def ungrounded(services, conn: db.Database, ticket: dict, result: dict) -> list[str]:
    """Evidence lines whose value is not in the facts code loads for this ticket (v3 prefetch). Rough: no LLM."""
    from datetime import datetime, timezone

    from resolve import policy, triage
    from resolve.models import TriageTicket

    facts = triage.prefetch(services, ticket, set(), {}, policy.load_config(conn), datetime.now(timezone.utc))
    return triage.ungrounded_evidence(TriageTicket.model_validate(result), json.dumps(facts) + ticket["body"] + ticket["subject"])


def grant_outcome(actions: list, attempted: bool) -> tuple[str, list]:
    """allowed (the code let a grant through, or a write reached Adapty) | blocked:<first reason> | none."""
    grants = [a for a in actions if a.tool == "grant_access"]
    days = [a.args.get("days") for a in grants if a.allowed]
    if attempted or any(a.allowed for a in grants):
        return "allowed", days
    if grants:
        return f"blocked:{grants[0].blocked_reason}", days
    return "none", days


def run_l2(services, conn: db.Database, ro: ReadOnlyAdapty, scenarios: list[dict], access_level: str) -> list[dict]:
    from resolve import frontline

    rows = []
    for sc in scenarios:
        session_id = f"eval-{sc['id']}-{uuid.uuid4().hex[:8]}"
        prior = sc.get("setup", {}).get("prior_grant")
        if prior:
            db.record_grant(conn, sc["customer_user_id"], access_level, prior["days"], None, prior["source"], prior["ref"], {"eval_setup": True})
        attempts_before = len(ro.attempts)
        actions, handed_off, error, sent = [], False, None, 0
        for message in sc["messages"]:
            sent += 1
            try:
                result = frontline.chat(services, sc["customer_user_id"], session_id, message)
            except AdaptyError as exc:
                if exc.code != READ_ONLY:
                    error = f"AdaptyError: {exc}"
                break  # a write reached the read-only client and frontline let it through: counted as a grant attempt
            except Exception as exc:  # noqa: BLE001
                error = f"{exc.__class__.__name__}: {exc}"
                break
            actions.extend(result.actions)
            if result.handed_off:
                handed_off = True
                break
        grant, days = grant_outcome(actions, len(ro.attempts) > attempts_before)
        handoff = None
        if handed_off:  # "auto" = handed off without a handoff tool call (max_turns, LLM failure)
            handoff = next((a.args.get("reason") for a in actions if a.tool == "handoff"), None) or "auto"
        ok = error is None and matches(sc["expect"]["grant"], grant) and matches(sc["expect"]["handoff"], handoff)
        row = {
            "id": sc["id"],
            "session_id": session_id,
            "messages_sent": sent,
            "expected": sc["expect"],
            "grant": grant,
            "grant_days": days,
            "handoff": handoff,
            "tools": [{"tool": a.tool, "allowed": a.allowed, "blocked_reason": a.blocked_reason} for a in actions],
            "correct": ok,
        }
        if error:
            row["error"] = error
        rows.append(row)
        tail = f"  {error}" if error else ""
        print(f"{'✓' if ok else '✗'} {sc['id']:14s} grant {grant:28s} handoff {handoff}  (expected {sc['expect']['grant']} / {sc['expect']['handoff']}){tail}")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", action="store_true", help="run it (paid model calls)")
    parser.add_argument("--l2", action="store_true", help="also run the Level 2 scenarios")
    parser.add_argument("--only", default="", help="comma-separated case / scenario ids")
    parser.add_argument("--db", type=Path, default=config.DEMO_DB_PATH, help="source DB, copied; never written")
    parser.add_argument("--live-profiles", action="store_true", help="read profiles from Adapty instead of the DB cache")
    parser.add_argument("--out", type=Path, default=config.ROOT / "data" / "eval_results.json")
    args = parser.parse_args()

    spec = json.loads((config.ROOT / "data" / "eval.json").read_text())
    cases = spec["cases"] + spec.get("hard_cases", [])
    scenarios = json.loads((config.ROOT / "data" / "l2_scenarios.json").read_text())["scenarios"] if args.l2 else []
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    if only:
        cases = [c for c in cases if c["ticket_id"] in only]
        scenarios = [s for s in scenarios if s["id"] in only]
    max_calls = len(cases) * L1_CALL_CAP + sum(len(s["messages"]) for s in scenarios) * L2_CALLS_PER_MESSAGE
    print(f"{len(cases)} Level 1 cases (≤{L1_CALL_CAP} model calls each), {len(scenarios)} Level 2 scenarios (≤{L2_CALLS_PER_MESSAGE} per user message)")
    print(f"up to {max_calls} model calls; profiles {'live from Adapty (reads only)' if args.live_profiles else 'from the DB cache'}; no Adapty writes")
    if not args.yes:
        print("paid: pass --yes to run")
        return 1

    from resolve import llm, workflow
    from resolve.models import AgentConfig

    scratch = scratch_copy(args.db)
    conn = db.connect(scratch)
    db.seed(conn, config.ROOT / "data" / "tickets.json")
    import_feedback.import_items(conn, import_feedback.load())
    seed_emails.index_only(conn)
    insert_cases(conn, cases, AgentConfig(**(db.get_config(conn) or {})).grant.access_level)
    if args.live_profiles:
        from resolve.adapty import Client

        ro = ReadOnlyAdapty(Client(config.adapty_secret()).get_profile)
    else:
        ro = ReadOnlyAdapty(profile_reader({**cached_profiles(conn), **spec.get("profiles", {})}))
    model = llm.make_llm()
    services = workflow.Services(conn=conn, demo=False, adapty=ro, llm=model)
    print(f"model {model.provider}/{model.model}, scratch DB {scratch}\n")

    l1_rows = run_l1(services, conn, ro, cases)
    l2_rows = []
    if scenarios:
        print()
        access_level = AgentConfig(**(db.get_config(conn) or {})).grant.access_level
        l2_rows = run_l2(services, conn, ro, scenarios, access_level)

    scored = [r for r in l1_rows if "got_category" in r]
    summary = {
        "model": model.model,
        "cases": len(cases),
        "scored": len(scored),
        "category_correct": sum(r["category_ok"] for r in scored),
        "action_correct": sum(r["action_ok"] for r in scored),
        "both_correct": sum(r["category_ok"] and r["action_ok"] for r in scored),
        "l2_cases": len(scenarios) if args.l2 else None,
        "l2_correct": sum(r["correct"] for r in l2_rows) if args.l2 else None,
        "model_calls_l1": sum(r.get("model_calls") or 0 for r in l1_rows),
        "evidence_lines": sum(r.get("evidence") or 0 for r in scored),
        "evidence_ungrounded": sum(len(r.get("evidence_ungrounded") or []) for r in scored),
        "forbidden": sum(len(r["forbidden"]) for r in scored),
        "reply_words_mean": round(sum(sum(r["reply_words"]) for r in scored) / max(1, sum(len(r["reply_words"]) for r in scored)), 1),
        "reply_words_max": max((max(r["reply_words"], default=0) for r in scored), default=0),
        "options_mean": round(sum(len(r["actions"]) for r in scored) / max(1, len(scored)), 2),
        "style_issues": sum(len(r["style"]) for r in scored),
        "profiles": "live" if args.live_profiles else "cached",
        "ran_at": db.now_iso(),
    }
    n = len(scored)
    line = f"\ncategory {summary['category_correct']}/{n}  action {summary['action_correct']}/{n}  both {summary['both_correct']}/{n}"
    line += f"  evidence grounded {summary['evidence_lines'] - summary['evidence_ungrounded']}/{summary['evidence_lines']}"
    line += f"\nforbidden {summary['forbidden']}  reply words mean {summary['reply_words_mean']} max {summary['reply_words_max']}  options {summary['options_mean']}  style {summary['style_issues']}  model calls {summary['model_calls_l1']}"
    if args.l2:
        line += f"  level 2 {summary['l2_correct']}/{len(scenarios)}"
    print(line)
    args.out.write_text(json.dumps({"summary": summary, "rows": l1_rows, "l2_rows": l2_rows}, indent=2, ensure_ascii=False) + "\n")
    print(f"→ {args.out}  (scratch DB kept: {scratch})")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
