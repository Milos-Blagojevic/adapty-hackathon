"""Pure web/insights helpers that survive v2.

The v1 HTML page tests (inbox, ticket page with the Proposal partial, approve partial) were removed: in v2
the UI is free and works only through the JSON API, which test_v2_api.py covers, including demo mode.
"""

from tests.fakes import ACTIVE_WEEKLY, profile, ticket_row


def test_age():
    from datetime import datetime, timezone

    from resolve.web import age

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


def test_ui_redirects_to_the_design_screens(tmp_path):
    from fastapi.testclient import TestClient

    from resolve.web import create_app

    client = TestClient(create_app(tmp_path / "ui.sqlite", demo=True))
    for path in ("/", "/ui"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code in (302, 307) and r.headers["location"] == "/static/ui/Main.dc.html"
    page = client.get("/static/ui/Main.dc.html")
    assert page.status_code == 200 and "support.js" in page.text

def test_impact_page_is_static_and_keyless(tmp_path):
    from fastapi.testclient import TestClient

    from resolve.web import create_app

    client = TestClient(create_app(tmp_path / "impact.sqlite", demo=True))
    r = client.get("/impact", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/static/ui/Impact.dc.html"
    anchors = ("168,607", "Calm", "6.4", "37/38", "1% of tracked revenue", "refundappeal.com")
    themed = client.get("/static/ui/Impact.dc.html")  # same content inside the support UI shell; no API calls
    assert themed.status_code == 200 and "support.js" in themed.text and "API." not in themed.text
    original = client.get("/static/impact.html")  # Milos's standalone original, no scripts
    assert original.status_code == 200 and "<script" not in original.text
    for anchor in anchors:
        assert anchor in themed.text and anchor in original.text, anchor



def test_old_inbox_page_lives_at_inbox(tmp_path):
    from fastapi.testclient import TestClient

    from resolve.web import create_app

    client = TestClient(create_app(tmp_path / "inbox.sqlite", demo=True))
    page = client.get("/inbox")
    assert page.status_code == 200 and "<h1>Inbox" in page.text and 'href="/inbox?status=' in page.text
