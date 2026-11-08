"""Insights over the JSON API, from docs/agents.md "Insights: JSON API and live screen". No network, no keys, no CLI."""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from resolve import db, insights_live
from resolve.adapty import Client
from resolve.llm import FakeLLM
from tests.fakes import ticket_row, tickets_file
from tests.fakes_v2 import call

VIEW_KEYS = {"updated_at", "demo", "llm", "auto", "total", "triaged", "untriaged", "categories", "top", "tested", "report", "previous",
             "run", "pending", "placements", "drafts"}


def facts_in(messages: list[dict]) -> dict:
    text = messages[0]["content"]
    return json.loads(text[text.index("<inbox_facts>") + len("<inbox_facts>"): text.index("</inbox_facts>")])


class FactsLLM(FakeLLM):
    """FakeLLM whose callable steps get the facts of the request: the insight is built from them and passes check_insight."""

    def step(self, system, messages, tools):
        out = super().step(system, messages, tools)
        return out(facts_in(messages)) if callable(out) else out


def insight_step(headline="Charged after trial leads; most saw Onboarding - Weekly first.", main=None, **over):
    """An insight on the largest flow-known cell, or on `main` (category, flow), or kept on the previous main cell on a tie."""

    def build_call(facts):
        cells = [c for c in facts["cells"] if c["flow"] != "unknown"]
        previous, changes = facts.get("previous"), facts.get("changes", {})
        want = main
        if want is None and previous and previous.get("main_cell") and changes.get("cells_now_larger_than_it") == "none":
            want = tuple(previous["main_cell"].split(" × "))
        pick = next((c for c in cells if (c["category"], c["flow"]) == want), cells[0])
        shown = [e["id"] for c in facts["cells"] for e in c.get("examples", [])]
        args = {
            "cited_cells": [{k: pick[k] for k in ("category", "flow", "tickets", "users")}],
            "ticket_ids": [e["id"] for e in pick.get("examples", [])][:2] or shown[:1],
            "evidence": [f"{pick['category']} × {pick['flow']}: {pick['users']} users"],
            "headline": headline,
            "placement": pick["placement"],
            "control_flow": pick["title"],
            "variant_source": "edited_copy",
            "variant_flow": None,
            "variant": "Put the price and renewal date on the CTA.",
            "hypothesis": "Show the renewal date on the trial button.",
            "expected_effect": "Fewer tickets of this category per 100 trials; trial starts no lower.",
            "caveat": "A handful of users.",
        }
        args.update(over)
        return call("submit_insight", **args)

    return build_call


class Runner:
    """Stands in for insights.Runner: records argv, answers like the CLI."""

    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    def run(self, *args):
        self.calls.append(list(args))
        if self.fail:
            from resolve.insights import DraftError

            raise DraftError(self.fail)
        if args[:2] == ("ab-tests", "create"):
            return {"ab_test_id": "test-1", "title": "Resolve: Onboarding complaints", "state": "draft"}
        return {"status": "sent"}


ROWS = [
    ticket_row("t-001", customer="u1"),
    ticket_row("t-002", customer="u2"),
    ticket_row("t-003", customer="u3"),
    dict(ticket_row("t-004", customer="u4"), context={"placement": "main", "flow": "main"}),
    dict(ticket_row("t-005", customer="u5"), context={"placement": "main", "flow": "main"}),
]


def build(tmp_path, steps=(), demo=False, rows=ROWS):
    from resolve.web import create_app

    fake = FactsLLM(list(steps))
    path = tmp_path / "ins.sqlite"
    seed = tickets_file(tmp_path, rows)
    if demo:
        app = create_app(path, demo=True, seed_file=seed)
    else:
        app = create_app(path, demo=False, adapty_client=Client("sk"), llm_client=fake, seed_file=seed)
    live = app.state.insights_live
    clock = [1_000_000.0]
    live.clock = lambda: clock[0]
    live.auto_enabled = True
    return SimpleNamespace(client=TestClient(app), conn=db.connect(path), llm=fake, app=app, live=live, clock=clock)


def categorize(env, **cats):
    for tid, cat in cats.items():
        db.update_ticket(env.conn, tid, category=cat)


@pytest.fixture
def env(tmp_path):
    e = build(tmp_path, steps=[insight_step(), insight_step("Refund requests lead now.")])
    categorize(e, **{"t-001": "charged_after_trial", "t-002": "charged_after_trial", "t-003": "charged_after_trial", "t-004": "refund_request"})
    return e


def get(env, dt=0.0):
    env.clock[0] += dt
    r = env.client.get("/api/insights")
    assert r.status_code == 200
    return r.json()


# --- reads ----------------------------------------------------------------------------------------


def test_view_shape_and_grouping_without_a_model_call(env):
    v = get(env)
    assert set(v) == VIEW_KEYS
    assert (v["total"], v["triaged"], v["untriaged"]) == (5, 4, 1)
    first = v["categories"][0]
    assert first["category"] == "charged_after_trial" and first["count"] == 3 and first["share"] == 0.75
    assert first["flows"] == [{"flow": "onb_weekly", "title": "Onboarding - Weekly first", "count": 3}]
    assert first["products"] == [{"product": "unknown", "count": 3}]
    assert v["top"] == {"category": "charged_after_trial", "flow": "onb_weekly", "title": "Onboarding - Weekly first", "count": 3}
    assert v["report"] is None and v["previous"] is None and v["run"]["state"] == "idle" and v["pending"] is None
    assert v["llm"] and v["auto"] and not v["demo"]
    assert {p["key"]: p["draftable"] for p in v["placements"]}["win_back"] is False
    assert {p["key"]: p["draftable"] for p in v["placements"]}["onboarding"] is True
    assert env.llm.calls == []


def test_a_ticket_without_context_takes_the_same_users_flow(env):
    """One source: `insights.group_tickets` fills the flow per user."""
    tid = db.insert_feedback(env.conn, "Charged again", source="chat_handoff", customer_user_id="u4")
    db.update_ticket(env.conn, tid, category="refund_request")
    stranger = db.insert_feedback(env.conn, "Who am I", source="form")
    db.update_ticket(env.conn, stranger, category="refund_request")
    refund = next(c for c in get(env)["categories"] if c["category"] == "refund_request")
    assert {f["flow"]: f["count"] for f in refund["flows"]} == {"main": 2, "unknown": 1}


def test_no_triaged_tickets(tmp_path):
    e = build(tmp_path)
    v = get(e)
    assert v["triaged"] == 0 and v["categories"] == [] and v["top"] is None
    r = e.client.post("/api/insights/run").json()
    assert r["run"]["state"] == "error" and "no triaged tickets" in r["run"]["error"] and r["report"] is None
    assert e.llm.calls == []


# --- manual run -------------------------------------------------------------------------------------


def test_manual_run_stores_the_report_and_keeps_the_previous_one(env):
    v = env.client.post("/api/insights/run").json()
    assert v["run"]["state"] == "idle" and v["run"]["trigger"] == "manual"
    r = v["report"]
    assert r["headline"].startswith("Charged after trial") and r["placement_title"] == "Onboarding"
    assert r["trigger"] == "manual" and r["reason"] is None and r["created_at"] and v["previous"] is None
    assert len(env.llm.calls) == 1 and env.llm.calls[0]["tools"] == ["submit_insight"]
    v2 = env.client.post("/api/insights/run").json()
    assert v2["report"]["headline"] == "Refund requests lead now."
    assert v2["previous"]["headline"].startswith("Charged after trial")


def test_run_errors_are_in_the_view_and_the_last_report_stays(tmp_path):
    from resolve.llm import LLMError

    e = build(tmp_path, steps=[insight_step(), LLMError("timeout")])
    categorize(e, **{"t-001": "charged_after_trial"})
    e.client.post("/api/insights/run")
    v = e.client.post("/api/insights/run").json()
    assert v["run"]["state"] == "error" and "LLM error" in v["run"]["error"]
    assert v["report"]["headline"].startswith("Charged after trial")


def test_one_run_at_a_time(env):
    assert env.live._lock.acquire(blocking=False)
    try:
        r = env.client.post("/api/insights/run")
        assert r.status_code == 409 and "in progress" in r.json()["detail"]
    finally:
        env.live._lock.release()
    assert env.llm.calls == []


def test_demo_mode_never_calls_a_model(tmp_path):
    e = build(tmp_path, demo=True)
    v = get(e)
    assert v["demo"] and not v["auto"] and not v["llm"]
    v = e.client.post("/api/insights/run").json()
    assert v["run"]["state"] == "error" and "Demo mode" in v["run"]["error"]


# --- auto refresh -----------------------------------------------------------------------------------


def shift_top(env):
    """Refund request overtakes Charged after trial: 3 vs 2."""
    categorize(env, **{"t-001": "refund_request", "t-005": "refund_request"})


def test_no_auto_run_without_a_report_with_basis(env):
    shift_top(env)
    for _ in range(5):
        v = get(env, 5)
    assert v["pending"] is None and env.llm.calls == []


def test_auto_run_after_the_debounce_with_reason_and_before(env):
    env.client.post("/api/insights/run")
    assert get(env, 1)["pending"] is None, "unchanged grouping is not material"
    shift_top(env)
    v = get(env, 30)
    assert v["pending"]["reason"] == "top issue changed: Charged after trial → Refund request"
    assert v["pending"]["due_in_s"] == 3.0 and v["pending"]["blocked"] is None
    assert get(env, 2)["run"]["state"] == "idle" and len(env.llm.calls) == 1, "still inside the debounce"
    v = get(env, 1.5)
    assert v["run"]["state"] == "running" and v["run"]["trigger"] == "auto"  # the answer is built before the task runs
    v = get(env, 0.5)
    assert len(env.llm.calls) == 2 and v["run"]["state"] == "idle" and v["pending"] is None
    assert v["report"]["trigger"] == "auto" and v["report"]["reason"].startswith("top issue changed")
    assert v["report"]["headline"] == "Refund requests lead now."
    assert v["previous"]["headline"].startswith("Charged after trial")
    for _ in range(3):
        get(env, 5)
    assert len(env.llm.calls) == 2, "the new report's basis is the new grouping: no second run"


def test_debounce_restarts_while_the_grouping_keeps_moving(env):
    env.client.post("/api/insights/run")
    shift_top(env)
    since = get(env, 30)["pending"]["since"]
    env.clock[0] += 2
    db.update_ticket(env.conn, "t-002", category="refund_request")
    v = get(env)
    assert v["pending"]["since"] != since and v["pending"]["due_in_s"] == 3.0
    get(env, 2)
    assert len(env.llm.calls) == 1


def test_small_share_moves_are_not_material(tmp_path):
    rows = [ticket_row(f"t-{i:03d}", customer=f"u{i}") for i in range(1, 41)]
    e = build(tmp_path, steps=[insight_step()], rows=rows)
    categorize(e, **{f"t-{i:03d}": "charged_after_trial" for i in range(1, 21)}, **{f"t-{i:03d}": "refund_request" for i in range(21, 31)})
    e.client.post("/api/insights/run")
    categorize(e, **{"t-031": "refund_request"})  # 20/30 → 20/31 and 10/30 → 11/31: under 5 points
    assert get(e, 30)["pending"] is None
    categorize(e, **{"t-032": "refund_request", "t-033": "refund_request"})  # 10/30 → 13/33: 6 points
    assert get(e)["pending"]["reason"] == "Refund request share 33% → 39%"


def test_cooldown_and_hourly_cap(env, monkeypatch):
    env.client.post("/api/insights/run")
    shift_top(env)
    get(env, 30)
    get(env, 3.5)  # auto run 1
    assert len(env.llm.calls) == 2
    categorize(env, **{"t-001": "charged_after_trial", "t-005": "charged_after_trial", "t-002": "charged_after_trial"})  # top flips back
    v = get(env, 1)
    get(env, 4)
    assert get(env)["pending"]["blocked"] == "cooling down" and len(env.llm.calls) == 2
    monkeypatch.setattr(insights_live, "AUTO_MAX_PER_HOUR", 1)
    assert get(env, 30)["pending"]["blocked"] == "hourly cap" and len(env.llm.calls) == 2
    assert v["pending"]["reason"].startswith("top issue changed")


def test_a_failed_auto_run_is_not_retried_on_the_same_grouping(tmp_path):
    from resolve.llm import LLMError

    e = build(tmp_path, steps=[insight_step(), LLMError("down")])
    categorize(e, **{"t-001": "charged_after_trial", "t-002": "charged_after_trial", "t-003": "charged_after_trial", "t-004": "refund_request"})
    e.client.post("/api/insights/run")
    shift_top(e)
    get(e, 30)
    v = get(e, 3.5)
    v = get(e, 1)
    assert v["run"]["state"] == "error" and v["report"]["trigger"] == "manual"
    v = get(e, 60)
    assert v["pending"]["blocked"] == "last auto run failed" and len(e.llm.calls) == 2


def test_auto_off_by_setting(env):
    env.live.auto_enabled = False
    env.client.post("/api/insights/run")
    shift_top(env)
    v = get(env, 30)
    v = get(env, 10)
    assert not v["auto"] and v["pending"] is None and len(env.llm.calls) == 1


def test_report_carries_the_insight_fields_and_the_run_cost(env):
    r = env.client.post("/api/insights/run").json()["report"]
    assert r["cited_cells"] == [{"category": "charged_after_trial", "flow": "onb_weekly", "tickets": 3, "users": 3}]
    assert r["caveat"] == "A handful of users." and r["what_changed"] == "First analysis."
    assert r["variant_source"] == "edited_copy" and r["variant_flow"] is None
    assert r["ticket_ids"] and set(r["ticket_ids"]) <= {"t-001", "t-002", "t-003"}
    assert r["model_calls"] == 1 and isinstance(r["latency_s"], float)
    assert get(env)["tested"] == {"category": "charged_after_trial", "flow": "onb_weekly", "title": "Onboarding - Weekly first", "count": 3}


def test_the_stored_previous_is_one_level_deep(env):
    env.llm.steps.append(insight_step("Third."))
    for _ in range(3):
        env.client.post("/api/insights/run")
    stored = db.get_insights(env.conn)
    assert stored["previous"]["headline"] == "Refund requests lead now."
    assert set(stored["previous"]) <= set(insights_live.PREVIOUS_KEYS)
    assert "previous" not in stored["previous"] and "snapshot" not in stored["previous"]
    assert env.client.get("/api/insights").json()["previous"]["what_changed"] == "Kept the test on Charged after trial × Onboarding - Weekly first (3 → 3 users); no cell outgrew it."


WIDE = ROWS + [dict(ticket_row(f"t-00{i}", customer=f"u{i}"), context={"placement": "main", "flow": "main"}) for i in (6, 7)]


def outgrow(env):
    """Refund request × Main - 3 plans reaches 4 users; the tested Charged after trial × Weekly first has 3."""
    categorize(env, **{"t-005": "refund_request", "t-006": "refund_request", "t-007": "refund_request"})


def test_a_cell_outgrowing_the_tested_cell_is_the_reason_and_the_run_moves(tmp_path):
    e = build(tmp_path, steps=[insight_step(), insight_step("Refund requests on the main paywall.")], rows=WIDE)
    categorize(e, **{"t-001": "charged_after_trial", "t-002": "charged_after_trial", "t-003": "charged_after_trial", "t-004": "refund_request"})
    e.client.post("/api/insights/run")
    outgrow(e)
    v = get(e, 30)
    assert v["pending"]["reason"] == "Refund request × Main - 3 plans (4 users) outgrew the tested Charged after trial × Onboarding - Weekly first (3 users)"
    get(e, 3.5)
    v = get(e, 0.5)
    assert len(e.llm.calls) == 2 and v["report"]["cited_cells"][0]["flow"] == "main"
    assert v["report"]["what_changed"] == "Moved the test from Charged after trial × Onboarding - Weekly first (3 users) to Refund request × Main - 3 plans (4 users)."
    assert v["report"]["reason"].endswith("(3 users)")
    for _ in range(3):
        assert get(e, 5)["pending"] is None
    assert len(e.llm.calls) == 2


def test_a_run_that_keeps_a_smaller_cell_is_not_retriggered_by_the_cells_it_saw(tmp_path):
    keep = ("charged_after_trial", "onb_weekly")
    e = build(tmp_path, steps=[insight_step(), insight_step(main=keep)], rows=WIDE)
    categorize(e, **{"t-001": "charged_after_trial", "t-002": "charged_after_trial", "t-003": "charged_after_trial", "t-004": "refund_request"})
    e.client.post("/api/insights/run")
    outgrow(e)
    get(e, 30)
    get(e, 3.5)
    v = get(e, 0.5)
    assert len(e.llm.calls) == 2 and v["report"]["cited_cells"][0]["category"] == "charged_after_trial"
    assert db.get_insights(e.conn)["basis"]["outgrown"] == ["refund_request|main"]
    assert v["report"]["what_changed"] == "Kept the test on Charged after trial × Onboarding - Weekly first (3 → 3 users); Refund request × Main - 3 plans has more users (4)."
    for _ in range(3):
        assert get(e, 30)["pending"] is None
    assert len(e.llm.calls) == 2


# --- draft A/B test ---------------------------------------------------------------------------------


def test_draft_creates_only_a_draft_and_lists_it(env):
    runner = Runner()
    env.app.state.ab_runner = runner
    r = env.client.post("/api/insights/ab-draft", json={"placement": "onboarding", "goal": "Fewer trial surprises"})
    assert r.status_code == 200
    d = r.json()["drafts"][0]
    assert d["ab_test_id"] == "test-1" and d["state"] == "draft" and d["placement"] == "Onboarding"
    assert [(a["title"], a["weight"]) for a in d["arms"]] == [("Onboarding - Weekly first", 50), ("Onboarding - Annual trial", 50)]
    assert d["goal"] == "Fewer trial surprises"
    commands = [c[:2] for c in runner.calls]
    assert commands[0] == ["ab-tests", "create"] and all(c[0] in ("ab-tests", "feedback") for c in commands)
    assert not any(w in c for c in runner.calls for w in ("start", "stop", "delete", "update"))
    assert env.llm.calls == []


def test_draft_errors(env, tmp_path):
    runner = Runner()
    env.app.state.ab_runner = runner
    assert env.client.post("/api/insights/ab-draft", json={"placement": "checkout"}).status_code == 422
    r = env.client.post("/api/insights/ab-draft", json={"placement": "win_back"})
    assert r.status_code == 422 and "two arms" in r.json()["detail"]
    assert runner.calls == []
    env.app.state.ab_runner = Runner(fail="`adapty` is not installed (npm i -g adapty)")
    r = env.client.post("/api/insights/ab-draft", json={"placement": "main"})
    assert r.status_code == 502 and "not installed" in r.json()["detail"]
    assert db.list_ab_drafts(env.conn) == []
    env.app.state.ab_runner = None
    assert env.client.post("/api/insights/ab-draft", json={"placement": "main"}).status_code == 409


def test_draft_in_demo_mode_is_refused(tmp_path):
    e = build(tmp_path, demo=True)
    r = e.client.post("/api/insights/ab-draft", json={"placement": "onboarding"})
    assert r.status_code == 409 and "Demo mode" in r.json()["detail"]
