"""The JSON API (docs/agents.md, "JSON API" + "JSON shapes"), through resolve.web.create_app."""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from resolve import db
from resolve.adapty import Client
from resolve.llm import FakeLLM
from resolve.models import AgentConfig
from tests.fakes import legacy_proposal
from tests.fakes_v2 import (
    CUID,
    OTHER,
    RoutingLLM,
    call,
    http_calls,
    make_proposed,
    mock_grant_ok,
    mock_profile,
    paid_profile,
    say,
    submit,
    ticket_row,
    tickets_file,
    triage_ticket,
)

SUMMARY_KEYS = {"id", "status", "source", "category", "customer_user_id", "email", "subject", "created_at", "updated_at", "confidence", "top_action"}
TICKET_KEYS = SUMMARY_KEYS | {"body", "session_id", "triage", "legacy", "profile", "decision", "action", "action_error", "triage_error", "conversation"}
CHAT_KEYS = {"reply", "actions", "handed_off", "ticket_id"}
ACTION_KEYS = {"tool", "args", "allowed", "blocked_reason", "result"}
SESSION_KEYS = {"session_id", "customer_user_id", "outcome", "ticket_id", "messages", "events"}
EVENT_KEYS = {"ts", "tool", "args", "allowed", "blocked_reason", "result"}


def build(tmp_path, demo=False):
    from resolve.web import create_app

    seed = tickets_file(tmp_path, [ticket_row(), ticket_row(id="t-002", customer=OTHER, rule="cancel_how")])
    llm = RoutingLLM(FakeLLM([]), FakeLLM([]))
    path = tmp_path / ("demo.sqlite" if demo else "api.sqlite")
    if demo:
        app = create_app(path, demo=True, seed_file=seed)
    else:
        app = create_app(path, demo=False, adapty_client=Client("sk"), llm_client=llm, seed_file=seed)
    return SimpleNamespace(client=TestClient(app), conn=db.connect(path), chat=llm.chat, triage=llm.triage)


@pytest.fixture
def env(tmp_path, http):
    mock_profile(http, paid_profile())
    return build(tmp_path)


def get_ticket(env, tid):
    response = env.client.get(f"/api/tickets/{tid}")
    assert response.status_code == 200
    return response.json()


# --- tickets ----------------------------------------------------------------------------------------


def test_list_tickets_summary_shape(env):
    response = env.client.get("/api/tickets")
    assert response.status_code == 200
    rows = response.json()
    assert {r["id"] for r in rows} == {"t-001", "t-002"}
    for r in rows:
        assert SUMMARY_KEYS <= set(r)
        assert not any(k.endswith("_json") for k in r)
        assert r["status"] == "new" and r["confidence"] is None and r["top_action"] is None


def test_list_filters(env):
    db.update_ticket(env.conn, "t-002", category="cancel_how", source="email")
    assert [r["id"] for r in env.client.get("/api/tickets?source=email").json()] == ["t-002"]
    assert [r["id"] for r in env.client.get("/api/tickets?category=cancel_how").json()] == ["t-002"]
    make_proposed(env.conn, "t-001")
    assert [r["id"] for r in env.client.get("/api/tickets?status=proposed").json()] == ["t-001"]
    assert env.client.get("/api/tickets?status=done").json() == []


def test_status_mapping(env):
    db.update_ticket(env.conn, "t-001", status="triaged")
    db.update_ticket(env.conn, "t-002", status="approved")
    assert get_ticket(env, "t-001")["status"] == "new"
    assert get_ticket(env, "t-002")["status"] == "proposed"
    assert [r["id"] for r in env.client.get("/api/tickets?status=new").json()] == ["t-001"]
    assert [r["id"] for r in env.client.get("/api/tickets?status=proposed").json()] == ["t-002"]


def test_ticket_shape_new(env):
    t = get_ticket(env, "t-001")
    assert TICKET_KEYS <= set(t)
    assert not any(k.endswith("_json") for k in t)
    assert t["triage"] is None and t["legacy"] is False and t["conversation"] is None
    assert t["body"] == ticket_row()["body"] and t["customer_user_id"] == CUID


def test_ticket_shape_proposed(env):
    prof = paid_profile()
    tt = make_proposed(env.conn, "t-001", prof=prof)
    t = get_ticket(env, "t-001")
    assert t["status"] == "proposed"
    assert t["triage"]["summary"] == tt.summary and len(t["triage"]["options"]) == 2
    assert t["profile"] == prof
    assert t["confidence"] == tt.confidence and t["top_action"] == "grant_access"
    (summary,) = [r for r in env.client.get("/api/tickets").json() if r["id"] == "t-001"]
    assert (summary["confidence"], summary["top_action"]) == (tt.confidence, "grant_access")


def test_legacy_proposal_is_a_one_option_ticket(env):
    db.update_ticket(env.conn, "t-001", proposal_json=json.dumps(legacy_proposal(action="explain")), category="charged_after_trial", status="proposed")
    t = get_ticket(env, "t-001")
    assert t["legacy"] is True
    assert len(t["triage"]["options"]) == 1 and t["triage"]["options"][0]["action"] == "reply_only"


def test_unknown_ticket_is_404(env):
    assert env.client.get("/api/tickets/nope").status_code == 404
    assert env.client.post("/api/tickets/nope/triage").status_code == 404
    assert env.client.post("/api/tickets/nope/accept", json={"option": 0, "edits": {}}).status_code == 404
    assert env.client.post("/api/tickets/nope/reject", json={"note": "x"}).status_code == 404


# --- triage -----------------------------------------------------------------------------------------


def test_triage_endpoint_runs_level1_cached_unless_force(env):
    env.triage.steps.extend([call("get_profile", customer_user_id=CUID), submit(triage_ticket(summary="first"))])
    assert env.client.post("/api/tickets/t-001/triage").status_code == 200
    t = get_ticket(env, "t-001")
    assert t["status"] == "proposed" and t["triage"]["summary"] == "first"
    made = len(env.triage.calls)

    assert env.client.post("/api/tickets/t-001/triage").status_code == 200
    assert len(env.triage.calls) == made, "cached"

    env.triage.steps.append(submit(triage_ticket(summary="second")))
    assert env.client.post("/api/tickets/t-001/triage?force=1").status_code == 200
    assert get_ticket(env, "t-001")["triage"]["summary"] == "second"


# --- accept / reject --------------------------------------------------------------------------------


def test_accept_runs_the_option_once(env, http):
    make_proposed(env.conn, "t-001")
    mock_grant_ok(http)
    response = env.client.post("/api/tickets/t-001/accept", json={"option": 0, "edits": {}})
    assert response.status_code == 200
    t = get_ticket(env, "t-001")
    assert t["status"] == "done" and t["action"]["type"] == "grant_access" and t["decision"]["option"] == 0
    assert env.client.post("/api/tickets/t-001/accept", json={"option": 0, "edits": {}}).status_code == 409
    assert len(http_calls(http, "POST")) == 1


def test_accept_with_edits(env, http):
    make_proposed(env.conn, "t-001")
    response = env.client.post("/api/tickets/t-001/accept", json={"option": 0, "edits": {"action": "reply_only", "reply": "Here is why."}})
    assert response.status_code == 200
    assert get_ticket(env, "t-001")["action"]["type"] == "reply_only"
    assert http_calls(http, "POST") == []


def test_accept_wrong_status_is_409(env, http):
    assert env.client.post("/api/tickets/t-001/accept", json={"option": 0, "edits": {}}).status_code == 409
    assert http_calls(http, "POST") == []


@pytest.mark.parametrize("payload", [{"option": "first"}, {"option": 7}, {"option": 2}, {"option": 0, "edits": {"days": 0}}, {"option": 0, "edits": {"action": "refund_now"}}, {}])
def test_accept_invalid_body_is_422(env, http, payload):
    make_proposed(env.conn, "t-001")
    assert env.client.post("/api/tickets/t-001/accept", json=payload).status_code == 422
    assert get_ticket(env, "t-001")["status"] == "proposed"


def test_accept_grant_without_customer_is_422_with_the_reason(env, http):
    make_proposed(env.conn, "t-001")
    db.update_ticket(env.conn, "t-001", customer_user_id="")
    response = env.client.post("/api/tickets/t-001/accept", json={"option": 0, "edits": {}})
    assert response.status_code == 422 and "identified customer" in response.json()["detail"]
    assert get_ticket(env, "t-001")["status"] == "proposed"
    assert http_calls(http, "POST") == []


def test_reject_with_reply_is_done_without_is_dismissed(env, http):
    make_proposed(env.conn, "t-001")
    make_proposed(env.conn, "t-002", ticket=triage_ticket(customer_user_id=OTHER))
    done = env.client.post("/api/tickets/t-001/reject", json={"note": "wrong", "reply": "We checked: no charge."})
    assert done.status_code == 200 and done.json()["status"] == "done"
    dismissed = env.client.post("/api/tickets/t-002/reject", json={"note": "spam"})
    assert dismissed.status_code == 200 and dismissed.json()["status"] == "dismissed"
    assert TICKET_KEYS <= set(dismissed.json())
    assert len(http.calls) == 0


def test_reject_from_new_is_allowed_and_returns_the_ticket(env):
    response = env.client.post("/api/tickets/t-001/reject", json={"note": "dup"})
    assert response.status_code == 200
    out = response.json()
    assert TICKET_KEYS <= set(out) and out["status"] == "dismissed"
    assert out["decision"] == {"rejected": True, "note": "dup", "reply": None}


def test_accept_returns_the_full_ticket(env):
    make_proposed(env.conn, "t-001")
    out = env.client.post("/api/tickets/t-001/accept", json={"option": 1, "edits": {}}).json()
    assert TICKET_KEYS <= set(out) and out["status"] == "done"
    assert out["decision"] == {"option": 1, "edits": {"action": None, "reply": None, "reply_en": None, "days": None, "kind": None, "store": None, "question": None}}


def test_reject_wrong_status_is_409_and_bad_body_422(env):
    make_proposed(env.conn, "t-001")
    db.update_ticket(env.conn, "t-002", status="done")
    assert env.client.post("/api/tickets/t-002/reject", json={"note": "x"}).status_code == 409
    assert env.client.post("/api/tickets/t-001/reject", json={"note": "x", "reply": ["not", "a", "string"]}).status_code == 422


# --- feedback and chat ------------------------------------------------------------------------------


def test_feedback_creates_a_ticket_and_triages_it_in_the_background(env):
    env.triage.steps.extend([call("get_profile", customer_user_id=CUID), submit(triage_ticket())])
    response = env.client.post("/feedback", json={"text": "Paid but still locked", "source": "email", "customer_user_id": CUID})
    assert response.status_code in (200, 201)
    tid = response.json()["ticket_id"]
    t = get_ticket(env, tid)
    assert t["source"] == "email" and t["body"] == "Paid but still locked"
    assert t["status"] == "proposed", "TestClient runs background tasks before returning"


@pytest.mark.parametrize("payload", [{"text": "x", "source": "sms"}, {"source": "form"}])
def test_feedback_invalid_body_is_422(env, payload):
    assert env.client.post("/feedback", json=payload).status_code == 422


def test_chat_shape_and_session(env, http):
    mock_grant_ok(http)
    env.chat.steps.extend([call("get_profile"), call("grant_access", days=3, reason="paid but locked", user_words="premium locked"), say("Added 3 days.")])
    response = env.client.post("/chat", json={"customer_user_id": CUID, "session_id": "s-api", "message": "premium locked"})
    assert response.status_code == 200
    out = response.json()
    assert CHAT_KEYS <= set(out)
    assert out["reply"] == "Added 3 days." and out["handed_off"] is False and out["ticket_id"] is None
    assert all(ACTION_KEYS <= set(a) for a in out["actions"])
    assert [a for a in out["actions"] if a["tool"] == "grant_access"][0]["allowed"] is True

    session = env.client.get("/api/chat/s-api")
    assert session.status_code == 200
    s = session.json()
    assert SESSION_KEYS <= set(s)
    assert (s["session_id"], s["customer_user_id"], s["outcome"]) == ("s-api", CUID, "open")
    assert s["messages"][0]["role"] == "user" and s["messages"][0]["content"] == "premium locked"
    assert all({"role", "content", "ts"} <= set(m) for m in s["messages"])
    assert [e["tool"] for e in s["events"]] == ["get_profile", "grant_access"]
    assert all(EVENT_KEYS <= set(e) for e in s["events"])


def test_unknown_session_is_404(env):
    assert env.client.get("/api/chat/nope").status_code == 404


def test_chat_invalid_body_is_422(env):
    assert env.client.post("/chat", json={"customer_user_id": CUID, "message": "hi"}).status_code == 422


def test_chat_handoff_is_triaged_in_the_background(env):
    env.chat.steps.extend([call("handoff", reason="refund", summary="wants a refund"), say("Passed to our team.")])
    env.triage.steps.extend([call("get_profile", customer_user_id=CUID), submit(triage_ticket(category="refund_request"))])
    out = env.client.post("/chat", json={"customer_user_id": CUID, "session_id": "s-h", "message": "refund me"}).json()
    assert out["handed_off"] is True and out["ticket_id"]
    t = get_ticket(env, out["ticket_id"])
    assert t["source"] == "chat_handoff" and t["session_id"] == "s-h"
    assert t["status"] == "proposed"
    assert any(m["content"] == "refund me" for m in t["conversation"])
    s = env.client.get("/api/chat/s-h").json()
    assert s["outcome"] == "handed_off" and s["ticket_id"] == out["ticket_id"]


def test_message_after_handoff_reruns_level_1_on_the_same_ticket(env):
    env.chat.steps.extend([call("handoff", reason="other", summary="asked for a human"), say("Passed to our team.")])
    env.triage.steps.extend([call("get_profile", customer_user_id=CUID), submit(triage_ticket(category="other"))])
    first = env.client.post("/chat", json={"customer_user_id": CUID, "session_id": "s-more", "message": "a human please"}).json()
    assert get_ticket(env, first["ticket_id"])["category"] == "other"

    env.chat.steps.append(say("Of course, I'm here."))
    env.triage.steps.extend([call("get_profile", customer_user_id=CUID), submit(triage_ticket(category="refund_request"))])
    second = env.client.post("/chat", json={"customer_user_id": CUID, "session_id": "s-more", "message": "I want my money back"}).json()
    assert second["ticket_id"] == first["ticket_id"]
    t = get_ticket(env, first["ticket_id"])
    assert t["category"] == "refund_request"
    assert any(m["content"] == "I want my money back" for m in t["conversation"])


def test_accept_refuses_when_the_options_changed_under_the_person(env):
    env.chat.steps.extend([call("handoff", reason="other", summary="asked for a human"), say("Passed to our team.")])
    env.triage.steps.extend([call("get_profile", customer_user_id=CUID), submit(triage_ticket(category="other"))])
    out = env.client.post("/chat", json={"customer_user_id": CUID, "session_id": "s-stale", "message": "a human please"}).json()
    r = env.client.post(f"/api/tickets/{out['ticket_id']}/accept", json={"option": 0, "expected_action": "not_the_action_now"})
    assert r.status_code == 409
    assert get_ticket(env, out["ticket_id"])["status"] == "proposed"


# --- config, analytics, users -----------------------------------------------------------------------


def test_config_get_defaults_and_put(env, http):
    response = env.client.get("/api/config")
    assert response.status_code == 200 and response.json() == AgentConfig().model_dump(mode="json")

    new = AgentConfig().model_dump(mode="json")
    new["grant"]["max_days"] = 3
    new["tone"] = "formal"
    put = env.client.put("/api/config", json=new)
    assert put.status_code == 200 and put.json()["grant"]["max_days"] == 3
    assert env.client.get("/api/config").json() == new

    mock_grant_ok(http)
    env.chat.steps.extend([call("grant_access", days=5, reason="x", user_words="locked"), say("Sorry.")])
    out = env.client.post("/chat", json={"customer_user_id": CUID, "session_id": "s-cfg", "message": "locked"}).json()
    assert [a["blocked_reason"] for a in out["actions"] if a["tool"] == "grant_access"] == ["over_max_days"]


@pytest.mark.parametrize("change", [{"max_turns": 0}, {"grant": {"max_days": 0}}, {"always_handoff": ["weather"]}])
def test_config_put_invalid_is_422_and_keeps_the_old_config(env, change):
    bad = {**AgentConfig().model_dump(mode="json"), **change}
    if "grant" in change:
        bad["grant"] = {**AgentConfig().model_dump(mode="json")["grant"], **change["grant"]}
    assert env.client.put("/api/config", json=bad).status_code == 422
    assert env.client.get("/api/config").json() == AgentConfig().model_dump(mode="json")


def test_analytics_shape(env):
    response = env.client.get("/api/analytics")
    assert response.status_code == 200
    out = response.json()
    assert {"tickets", "level2", "triage", "eval"} <= set(out)
    assert out["tickets"]["total"] == 2


def test_users_shape(env):
    response = env.client.get("/api/users")
    assert response.status_code == 200
    users = response.json()
    assert isinstance(users, list)
    for u in users:
        assert {"customer_user_id", "badge", "product"} <= set(u)
        assert u["badge"] in ("active", "expired", "lifetime", "none", "unknown")
        assert u["product"] is None or isinstance(u["product"], str)


# --- CORS and demo mode -----------------------------------------------------------------------------


def test_cors_allows_any_origin(env):
    response = env.client.get("/api/tickets", headers={"Origin": "http://phone.local:8081"})
    assert response.headers.get("access-control-allow-origin") in ("*", "http://phone.local:8081")
    preflight = env.client.options("/chat", headers={"Origin": "http://phone.local:8081", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"})
    assert preflight.status_code == 200
    assert preflight.headers.get("access-control-allow-origin") in ("*", "http://phone.local:8081")


def test_demo_mode_serves_the_api_without_clients(tmp_path, http):
    env = build(tmp_path, demo=True)
    assert env.client.get("/api/tickets").status_code == 200
    make_proposed(env.conn, "t-001", prof=paid_profile())
    response = env.client.post("/api/tickets/t-001/accept", json={"option": 0, "edits": {}})
    assert response.status_code == 200
    t = get_ticket(env, "t-001")
    assert t["status"] == "done" and t["action"]["demo"] is True
    assert len(http.calls) == 0


def test_reopen_endpoint(tmp_path):
    from fastapi.testclient import TestClient

    from resolve import web
    from resolve.adapty import Client
    from resolve.llm import FakeLLM
    from tests.fakes_v2 import new_db

    new_db(tmp_path, name="r.sqlite")
    c = TestClient(web.create_app(tmp_path / "r.sqlite", demo=True, adapty_client=Client("sk"), llm_client=FakeLLM([]), seed_file=tmp_path / "none.json"))
    assert c.post("/api/tickets/nope/reopen").status_code == 404
    assert c.post("/api/tickets/t-001/reopen").status_code == 409  # still open
    assert c.post("/api/tickets/t-001/reject", json={"note": "x"}).json()["status"] == "dismissed"
    r = c.post("/api/tickets/t-001/reopen", json={"note": "again"})
    assert r.status_code == 200 and r.json()["status"] in ("new", "proposed")
    assert r.json()["history"][0]["from_status"] == "dismissed" and r.json()["history"][0]["note"] == "again"
    assert c.post("/api/tickets/t-001/reopen").status_code == 409


def test_full_text_search_over_conversation_and_triage(tmp_path):
    from fastapi.testclient import TestClient

    from resolve import db, web
    from resolve.adapty import Client
    from resolve.llm import FakeLLM
    from tests.fakes_v2 import new_db

    conn = new_db(tmp_path, name="s.sqlite")
    tid = db.insert_feedback(conn, "Handoff", source="chat_handoff", customer_user_id="u1", session_id="s9")
    db.ensure_conversation(conn, "s9", "u1")
    db.append_message(conn, "s9", "agent", "Спасибо, всё хорошо! Чем помочь?")
    c = TestClient(web.create_app(tmp_path / "s.sqlite", demo=True, adapty_client=Client("sk"), llm_client=FakeLLM([]), seed_file=tmp_path / "none.json"))
    hits = c.get("/api/tickets", params={"q": "спасибо"}).json()
    assert [h["id"] for h in hits] == [tid] and "Спасибо" in hits[0]["snippet"]
    assert c.get("/api/tickets", params={"q": "спасибо nothing-like-this"}).json() == []
    assert "snippet" not in c.get("/api/tickets").json()[0] or c.get("/api/tickets").json()[0]["snippet"] is None
