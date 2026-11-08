"""Analyze v3 (resolve/analytics.py docstring): period, reply-by-reply, agent log, live notes. Fixed clock, hand-built rows."""

import json
from datetime import datetime, timezone

import pytest

from resolve import analytics, db
from tests.fakes_v2 import grant_ticket

NOW = datetime(2026, 11, 7, 22, 0, 0, tzinfo=timezone.utc)
DAY = "2026-11-07T"
PREV = "2026-11-06T"


def conv(c, sid, user, outcome, created, ticket_id=None):
    c.execute("INSERT INTO conversations VALUES (?, ?, ?, ?, ?, ?)", (sid, user, outcome, ticket_id, created, created))


def msg(c, sid, role, ts, text="text"):
    c.execute("INSERT INTO messages (session_id, role, content, ts) VALUES (?, ?, ?, ?)", (sid, role, text, ts))


def event(c, sid, ts, tool, args=None, allowed=True, reason=None, result=None):
    c.execute(
        "INSERT INTO l2_events (session_id, ts, tool, args_json, allowed, blocked_reason, result_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (sid, ts, tool, json.dumps(args or {}), int(allowed), reason, json.dumps(result)),
    )


def grant(c, source, days, ts, ref="x"):
    c.execute("INSERT INTO grants (customer_user_id, access_level, days, source, ref, ts) VALUES ('u', 'premium', ?, ?, ?, ?)", (days, source, ref, ts))


def ticket(c, tid, source, status, created, **fields):
    c.execute(
        "INSERT INTO tickets (id, customer_user_id, subject, body, created_at, rule, status, source, updated_at) VALUES (?, 'u', 's', 'b', ?, '', ?, ?, ?)",
        (tid, created, status, source, fields.pop("updated_at", created)),
    )
    for k, v in fields.items():
        c.execute(f"UPDATE tickets SET {k} = ? WHERE id = ?", (json.dumps(v) if isinstance(v, (dict, list)) else v, tid))


TRIAGE = grant_ticket().model_dump_json()  # first option: grant_access 3 days


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "v3.sqlite")
    with c:
        # Chat A: answered, resolved, then the user writes again.
        conv(c, "sA", "U1", "resolved", DAY + "10:00:00Z")
        msg(c, "sA", "user", DAY + "10:00:00Z")
        event(c, "sA", DAY + "10:00:01Z", "get_profile")
        msg(c, "sA", "agent", DAY + "10:00:03Z", "first answer")
        msg(c, "sA", "user", DAY + "10:05:00Z")
        event(c, "sA", DAY + "10:05:01Z", "mark_resolved", {"note": "ok"})
        msg(c, "sA", "agent", DAY + "10:05:02Z", "glad it works")
        msg(c, "sA", "user", DAY + "10:20:00Z")
        msg(c, "sA", "agent", DAY + "10:20:01Z", "anything else")
        # Chat B: grant blocked by a limit → handoff (logged twice for one ticket), then a reply while with a teammate.
        conv(c, "sB", "U2", "handed_off", DAY + "11:00:00Z", "tB")
        msg(c, "sB", "user", DAY + "11:00:00Z")
        event(c, "sB", DAY + "11:00:02Z", "grant_access", {"days": 3}, False, "grant_limit_30d", "blocked: grant_limit_30d")
        event(c, "sB", DAY + "11:00:02Z", "handoff", {"reason": "repeat_after_grant"}, result={"ticket_id": "tB"})
        event(c, "sB", DAY + "11:00:03Z", "handoff", {"reason": "other"}, result={"ticket_id": "tB"})
        msg(c, "sB", "agent", DAY + "11:00:04Z", "passed to a teammate")
        msg(c, "sB", "user", DAY + "11:10:00Z")
        event(c, "sB", DAY + "11:10:00Z", "grant_access", {"days": 1}, False, None, "not run: a teammate has the conversation")
        msg(c, "sB", "agent", DAY + "11:10:00Z", "a teammate has it")
        # Chat C: still open, the agent granted 3 days.
        conv(c, "sC", "U3", "open", DAY + "12:00:00Z")
        msg(c, "sC", "user", DAY + "12:00:00Z")
        event(c, "sC", DAY + "12:00:05Z", "grant_access", {"days": 3}, result={"type": "grant_access", "days": 3})
        msg(c, "sC", "agent", DAY + "12:00:06Z", "added 3 days")
        grant(c, "l2", 3, DAY + "12:00:05Z", "sC")
        # Chat D: yesterday, resolved at once.
        conv(c, "sD", "U4", "resolved", PREV + "09:00:00Z")
        msg(c, "sD", "user", PREV + "09:00:00Z")
        event(c, "sD", PREV + "09:00:01Z", "mark_resolved", {"note": "ok"})
        msg(c, "sD", "agent", PREV + "09:00:02Z", "done")
        event(c, "sD", PREV + "09:00:01Z", "grant_access", {"days": 30}, False, "over_max_days", "blocked")
        grant(c, "l2", 7, PREV + "09:00:01Z", "sD")

        # Tickets (people).
        accepted_2nd_edited = {"option": 1, "edits": {"action": None, "reply": None, "days": 5, "kind": None, "store": None, "question": None}}
        ticket(c, "tB", "chat_handoff", "done", DAY + "11:00:02Z", session_id="sB", triage_at=DAY + "11:01:00Z", triage_json=TRIAGE,
               decision_json=accepted_2nd_edited, action_json={"final": {"action": "grant_access", "reply": "added 5 days"}, "approved_at": DAY + "11:30:02Z"},
               action_at=DAY + "11:30:02Z", reply_sent_at=DAY + "11:30:02Z")
        grant(c, "l1", 5, DAY + "11:30:02Z", "tB")
        ticket(c, "tE", "email", "done", DAY + "08:00:00Z", triage_at=DAY + "08:01:00Z", triage_json=TRIAGE,
               decision_json={"rejected": True, "note": "", "reply": "my own words"}, reply_sent_at=DAY + "09:00:00Z")
        ticket(c, "tF", "form", "done", DAY + "07:00:00Z", triage_at=DAY + "07:02:00Z", triage_json=TRIAGE,
               history_json=[{"at": DAY + "08:00:00Z", "from_status": "done", "decision": {"option": 1},
                              "action": {"final": {"action": "refund_to_human", "reply": "ask the store"}, "approved_at": DAY + "07:30:00Z"}, "note": "angry"}],
               decision_json={"option": 0, "edits": {}}, action_json={"final": {"action": "ask_user", "reply": "which email?"}, "approved_at": DAY + "08:30:00Z"},
               action_at=DAY + "08:30:00Z", reply_sent_at=DAY + "08:30:00Z")
        ticket(c, "tG", "form", "dismissed", DAY + "06:00:00Z", triage_at=DAY + "06:05:00Z", triage_json=TRIAGE,
               decision_json={"rejected": True, "note": "", "reply": None}, updated_at=DAY + "06:10:00Z")
        ticket(c, "tH", "chat_handoff", "dismissed", DAY + "13:00:00Z", triage_at=DAY + "13:00:30Z", triage_json=TRIAGE,
               decision_json={"rejected": True, "note": "user cancelled the handoff: fine now", "reply": None})
        ticket(c, "tI", "form", "new", DAY + "14:00:00Z")
        ticket(c, "tJ", "form", "new", PREV + "23:59:59Z")
        ticket(c, "tK", "email", "proposed", DAY + "15:00:00Z", triage_at=DAY + "15:00:30Z", triage_json=TRIAGE)
    return c


def today(conn, **kw):
    return analytics.compute(conn, now=NOW, **kw)["period"]


def test_range_and_buckets_for_today(conn):
    p = today(conn)
    assert (p["range"], p["bucket"], p["start"], p["end"], p["prev_start"]) == ("today", "hour", DAY + "00:00:00Z", DAY + "22:00:00Z", PREV + "00:00:00Z")
    assert len(p["points"]) == 23 and p["points"][0]["t"] == DAY + "00:00:00Z" and p["points"][-1]["t"] == DAY + "22:00:00Z"


def test_range_follows_the_viewers_timezone_and_7_days(conn):
    p = today(conn, tz_offset_min=60)  # 23:00 local: today began at 23:00Z yesterday
    assert p["start"] == PREV + "23:00:00Z" and p["points"][0]["t"] == PREV + "23:00:00Z" and len(p["points"]) == 24
    assert p["points"][0]["feedback"] == 1  # tJ at 23:59:59Z is local Nov 7 00:59
    assert today(conn, tz_offset_min=180)["start"] == DAY + "21:00:00Z"  # already Nov 8 locally
    week = today(conn, range_id="7d")
    assert (week["bucket"], week["start"], week["prev_start"], len(week["points"])) == ("day", "2026-11-01T00:00:00Z", "2026-10-25T00:00:00Z", 7)
    assert analytics.compute(conn, "30d", now=NOW)["period"]["start"] == "2026-10-09T00:00:00Z"


def test_chart_points(conn):
    pts = {p["t"][11:13]: p for p in today(conn)["points"]}
    nonzero = {h: {k: v for k, v in p.items() if k != "t" and v} for h, p in pts.items()}
    assert {h: v for h, v in nonzero.items() if v} == {
        "06": {"closed_by_people": 1, "feedback": 1},  # tG dismissed by a person at 06:10; tG came in
        "07": {"feedback": 1},
        "08": {"closed_by_people": 1, "feedback": 1},  # tF replied 08:30; tE came in
        "09": {"closed_by_people": 1},  # tE own reply
        "10": {"agent": 1},  # chat A resolved
        "11": {"handed_off": 1, "closed_by_people": 1},  # chat B; tB replied 11:30
        "14": {"feedback": 1},
        "15": {"feedback": 1},
    }  # tJ (yesterday 23:59:59) is not in the first bucket; tH (user-cancelled handoff) is not closed by people


def test_chats_share_and_previous(conn):
    p = today(conn)
    cur, prev = p["current"], p["previous"]
    assert cur["chats"] == {"total": 3, "resolved": 1, "handed_off": 1, "open": 1}
    assert cur["machine_share"] == 0.5
    assert prev["chats"] == {"total": 1, "resolved": 1, "handed_off": 0, "open": 0} and prev["machine_share"] == 1.0
    assert prev["machines"]["first_reply_median_s"] == 2.0


def test_machines_reply_by_reply(conn):
    m = today(conn)["current"]["machines"]
    assert m == {
        "replies": 6,
        "first_reply_median_s": 4.0,  # A 3 s, B 4 s, C 6 s
        "closed_with_reply": round(1 / 3, 4),
        "came_back": 1.0,  # A wrote again after mark_resolved
        "passed_on": round(1 / 3, 4),
        "free_days": 3,  # Level 2 grants today
    }


def test_humans_reply_by_reply(conn):
    h = today(conn)["current"]["humans"]
    # replies: tB 11:30 (grant), tE 09:00 (own), tF 07:30 (refund, reopened later), tF 08:30 (ask_user)
    assert h == {
        "replies": 4,
        "first_reply_median_s": 1800.0,  # tB 1800, tE 3600, tF 1800 (its first reply, from history)
        "closed_with_reply": 0.5,  # tB and tE; tF's first came back, its second only asks
        "came_back": 0.25,
        "passed_on": 0.25,
        "free_days": 5,  # Level 1 grants today
    }


def test_person_came_back_when_the_user_writes_after_the_reply(conn):
    with conn:
        msg(conn, "sB", "user", DAY + "11:45:00Z", "still broken")
    h = today(conn)["current"]["humans"]
    assert h["came_back"] == 0.5 and h["closed_with_reply"] == 0.25


def test_breakdowns(conn):
    cur = today(conn)["current"]
    assert cur["grants"] == {"count": 1, "days": 3, "days_each": [3]}
    assert cur["handoff_reasons"] == {"repeat_after_grant": 1}  # one per ticket; the second event is the same handoff
    assert cur["blocked"] == {"grant_limit_30d": 1}  # "not run" is not a limit
    assert today(conn)["previous"]["blocked"] == {"over_max_days": 1}
    assert cur["decisions"] == {"triaged": 6, "decided": 4, "first": 1, "second_third": 1, "edited": 1, "own_reply": 1, "no_reply": 1}


def test_agent_log(conn):
    log = analytics.compute(conn, now=NOW)["agent_log"]
    assert [(r["session_id"], r["outcome"], r["latency_s"], r["grant_days"]) for r in log] == [
        ("sC", "granted", 6.0, 3),
        ("sB", "with_teammate", 0.0, None),
        ("sB", "grant_blocked", 4.0, None),
        ("sA", "answered", 1.0, None),
        ("sA", "resolved", 2.0, None),
        ("sA", "answered", 3.0, None),
        ("sD", "grant_blocked", 2.0, None),  # a blocked grant outranks mark_resolved in the same turn
    ]
    assert log[0]["user"] == "U3" and log[0]["reply"] == "added 3 days" and log[0]["ts"] == DAY + "12:00:06Z"


def test_live_notes_and_counts(conn):
    out = analytics.compute(conn, now=NOW)
    assert out["open_counts"] == {"chat": 1, "triage": 2, "review": 1}
    detail = {t["id"]: t["detail"] for t in out["open_tasks"]}
    assert detail["sC"] == "granted 3 days" and detail["tK"] == "grant 3 days" and detail["tI"] is None


def test_empty_db(tmp_path):
    out = analytics.compute(db.connect(tmp_path / "empty.sqlite"), now=NOW)
    p = out["period"]
    assert len(p["points"]) == 23 and all(v == 0 for pt in p["points"] for k, v in pt.items() if k != "t")
    for s in (p["current"], p["previous"]):
        assert s["machine_share"] is None and s["chats"]["total"] == 0
        for side in (s["machines"], s["humans"]):
            assert side == {"replies": 0, "first_reply_median_s": None, "closed_with_reply": None, "came_back": None, "passed_on": None, "free_days": 0}
    assert out["agent_log"] == [] and out["open_counts"] == {"chat": 0, "triage": 0, "review": 0}


def test_api_range_param(tmp_path):
    from fastapi.testclient import TestClient

    from resolve.web import create_app

    client = TestClient(create_app(tmp_path / "api.sqlite", demo=True, seed_file=tmp_path / "none.json"))
    r = client.get("/api/analytics?range=7d&tz=60")
    assert r.status_code == 200
    body = r.json()
    assert body["period"]["range"] == "7d" and body["period"]["tz_offset_min"] == 60 and len(body["period"]["points"]) == 7
    assert {"tickets", "level2", "triage", "timeline", "open_tasks"} <= set(body)  # the old contract stays
    assert client.get("/api/analytics").json()["period"]["range"] == "today"
    assert client.get("/api/analytics?range=week").status_code == 422
