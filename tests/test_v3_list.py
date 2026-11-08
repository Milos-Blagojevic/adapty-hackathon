"""GET /api/tickets list items carry triage_error, so the Incoming list can show a failed triage."""

from resolve import db
from tests.test_v2_api import build


def by_id(env) -> dict:
    response = env.client.get("/api/tickets")
    assert response.status_code == 200
    return {t["id"]: t for t in response.json()}


def test_list_item_has_triage_error_none_by_default(tmp_path):
    env = build(tmp_path)
    items = by_id(env)
    assert items["t-001"]["triage_error"] is None and items["t-002"]["triage_error"] is None


def test_list_item_shows_the_stored_triage_error(tmp_path):
    env = build(tmp_path)
    db.update_ticket(env.conn, "t-001", triage_error="LLM is not configured")
    items = by_id(env)
    assert items["t-001"]["triage_error"] == "LLM is not configured"
    assert items["t-001"]["status"] == "new"
    assert items["t-002"]["triage_error"] is None


def test_search_results_carry_triage_error_too(tmp_path):
    env = build(tmp_path)
    db.update_ticket(env.conn, "t-001", triage_error="model gave up")
    subject = env.client.get("/api/tickets/t-001").json()["subject"]
    response = env.client.get("/api/tickets", params={"q": subject.split()[0]})
    hits = {t["id"]: t for t in response.json()}
    assert hits["t-001"]["triage_error"] == "model gave up"


def test_full_ticket_still_has_triage_error(tmp_path):
    env = build(tmp_path)
    db.update_ticket(env.conn, "t-001", triage_error="model gave up")
    assert env.client.get("/api/tickets/t-001").json()["triage_error"] == "model gave up"
