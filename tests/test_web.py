import json

import pytest
from fastapi.testclient import TestClient

from resolve import config, db
from resolve.adapty import Client
from resolve.web import age, create_app
from tests.fakes import ACTIVE_WEEKLY, GRANT_URL, PROFILE_URL, FakeAnthropic, profile, proposal, ticket_row, tickets_file


@pytest.fixture
def app(tmp_path):
    seed = tickets_file(tmp_path, [ticket_row(), ticket_row(id="t-002", customer="lumen_443814_00042", rule="cancel_how")])
    fake = FakeAnthropic([proposal()])
    application = create_app(tmp_path / "web.sqlite", demo=False, adapty_client=Client("sk"), anthropic_client=fake, model="m", seed_file=seed)
    application.state.fake = fake
    return application


@pytest.fixture
def client(app):
    return TestClient(app)


def test_inbox_lists_and_filters(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "Charged after free trial" in page.text and "lumen_443814_00042" in page.text
    assert client.get("/?status=done").text.count('class="row"') == 0
    assert client.get("/?status=bogus").status_code == 400
    assert client.get("/tickets/nope").status_code == 404


def test_ticket_page_shows_live_profile_then_proposal_then_grant(http, client):
    http.get(PROFILE_URL, json={"data": profile(access_levels=[ACTIVE_WEEKLY])})
    page = client.get("/tickets/t-001")
    assert page.status_code == 200
    assert "lumen_weekly" in page.text and "would run until" in page.text
    assert 'hx-trigger="load"' in page.text, "Level 2 auto-runs on open"

    partial = client.post("/tickets/t-001/propose")
    assert "Charged after trial" in partial.text and "Approve" in partial.text
    assert "Grant 7 days of premium" in partial.text

    http.post(GRANT_URL, json={"data": profile(access_levels=[dict(ACTIVE_WEEKLY, store="adapty", expires_at="2026-11-20T05:08:30+00:00")])})
    http.patch(PROFILE_URL, json={"data": profile(access_levels=[dict(ACTIVE_WEEKLY, store="adapty", expires_at="2026-11-20T05:08:30+00:00")])})
    done = client.post("/tickets/t-001/approve")
    assert "Granted <b>premium</b>" in done.text and "Nov 20, 2026" in done.text
    assert 'hx-swap-oob="true"' in done.text, "status badge and profile panel refresh out of band"
    assert client.get("/?status=done").text.count('class="row"') == 1


def test_demo_mode_serves_snapshot_without_clients(tmp_path, http):
    seed = tickets_file(tmp_path, [ticket_row()])
    app = create_app(tmp_path / "demo.sqlite", demo=True, seed_file=seed)
    conn = app.state.services.conn
    db.update_ticket(conn, "t-001", profile_json=json.dumps(profile(access_levels=[ACTIVE_WEEKLY])), proposal_json=proposal().model_dump_json(), category="charged_after_trial", status="proposed")
    client = TestClient(app)
    assert "DEMO MODE" in client.get("/").text
    page = client.get("/tickets/t-001")
    assert "Approve" in page.text and 'hx-trigger="load"' not in page.text
    done = client.post("/tickets/t-001/approve")
    assert "Demo mode: no Adapty call" in done.text
    assert len(http.calls) == 0
    insights = client.get("/insights")
    assert insights.status_code == 200 and "Charged after trial" in insights.text


def test_age():
    from datetime import datetime, timezone

    now = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
    assert age("2026-11-07T11:30:00Z", now) == "30m"
    assert age("2026-11-07T01:00:00Z", now) == "11h"
    assert age("2026-11-01T12:00:00Z", now) == "6d"


def test_insights_groups_by_agent_category_and_store_product():
    from resolve.insights import group_tickets, product_of

    granted = profile(access_levels=[dict(ACTIVE_WEEKLY, store="adapty", store_product_id="adapty_server_side_product")], subscriptions=[{"store_product_id": "lumen_weekly"}])
    assert product_of({"profile": granted}) == "weekly"
    assert product_of({"profile": profile()}) == "none"
    assert product_of({"profile": None}) == "unknown"
    tickets = [
        dict(ticket_row(), category="charged_after_trial", profile=granted),
        dict(ticket_row(id="t-002"), category="charged_after_trial", profile=profile(access_levels=[ACTIVE_WEEKLY])),
        dict(ticket_row(id="t-003"), category=None, profile=None),
    ]
    groups = group_tickets(tickets)
    assert (groups["triaged"], groups["untriaged"]) == (2, 1)
    assert groups["by_category"] == [{"category": "charged_after_trial", "count": 2}]
    assert groups["by_category_product"]["charged_after_trial"] == {"weekly": 2}
    assert (groups["top_category"], groups["top_flow"], groups["top_placement"]) == ("charged_after_trial", "onb_weekly", "onboarding")
