import json
import stat
import sys
from pathlib import Path

import pytest

from resolve import config, db, insights
from resolve.insights import DraftError, Insight, Runner, draft_ab_test

STUB = '''#!{python}
import json, sys
from pathlib import Path
log = Path({log!r})
log.write_text(log.read_text() + json.dumps(sys.argv[1:]) + "\\n") if log.exists() else log.write_text(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[1:3] == ["ab-tests", "create"]:
    print(json.dumps({{"ab_test_id": "test-123", "title": "Resolve: Onboarding complaints", "state": "draft"}}))
elif sys.argv[1] == "feedback":
    print(json.dumps({{"status": "sent"}}))
else:
    print(json.dumps({{"error": {{"message": "unexpected command"}}}}))
    sys.exit(4)
'''


@pytest.fixture
def stub_cli(tmp_path):
    """A fake `adapty` binary that records argv and answers `ab-tests create` with a draft."""
    log = tmp_path / "cli.log"
    script = tmp_path / "adapty"
    script.write_text(STUB.format(python=sys.executable, log=str(log)))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script, log


def calls(log: Path) -> list[list[str]]:
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def test_draft_runs_only_create_and_feedback_with_the_placements_flows(stub_cli):
    script, log = stub_cli
    draft = draft_ab_test(Runner(binary=str(script)), "onboarding", "Reduce trial surprise; --yes 'quoted' $(rm -rf /)")
    assert draft["ab_test_id"] == "test-123" and draft["state"] == "draft"
    create, feedback = calls(log)
    assert create[:2] == ["ab-tests", "create"] and create[-1] == "--json"
    assert "--yes" not in create and "start" not in create and "stop" not in create
    assert create[create.index("--placement") + 1] == insights.PLACEMENTS["onboarding"]["id"]
    assert create[create.index("--goal") + 1] == "Reduce trial surprise; --yes 'quoted' $(rm -rf /)", "form text is one argv item, never shell"
    arms = json.loads(create[create.index("--flows") + 1])
    assert [(a["title"], a["weight"]) for a in arms] == [("Onboarding - Weekly first", 50), ("Onboarding - Annual trial", 50)]
    assert "--all-users" in create
    assert feedback[0] == "feedback" and "test-123" in feedback[feedback.index("--context") + 1]


def test_draft_refuses_single_flow_placement_and_unknown_placement(stub_cli):
    script, log = stub_cli
    with pytest.raises(DraftError, match="two arms"):
        draft_ab_test(Runner(binary=str(script)), "win_back")
    with pytest.raises(DraftError, match="unknown placement"):
        draft_ab_test(Runner(binary=str(script)), "../etc")
    assert calls(log) == [], "nothing reached the CLI"


def test_runner_reports_missing_binary_and_cli_refusal(tmp_path, stub_cli):
    with pytest.raises(DraftError, match="not installed"):
        Runner(binary=str(tmp_path / "nope")).run("ab-tests", "create")
    script, _ = stub_cli
    with pytest.raises(DraftError, match="exit 4"):
        Runner(binary=str(script)).run("ab-tests", "start", "x")


def insight(**overrides) -> Insight:
    base = {
        "cited_cells": [{"category": "charged_after_trial", "flow": "onb_weekly", "tickets": 2, "users": 2}],
        "ticket_ids": ["t-1"],
        "evidence": ["charged_after_trial × onb_weekly: 2 tickets, 2 users", "t-1: charged after the 3-day trial"],
        "headline": "2 of 3 tickets are charged_after_trial on Onboarding - Weekly first.",
        "placement": "onboarding",
        "control_flow": "Onboarding - Weekly first",
        "variant_source": "existing_flow",
        "variant_flow": "Onboarding - Annual trial",
        "variant": "Lead with the annual plan and its 7-day trial.",
        "hypothesis": "Leading with the annual trial cuts charged_after_trial tickets.",
        "expected_effect": "Fewer charged_after_trial tickets per purchase; trial-to-paid must not drop.",
        "caveat": "Two users.",
        "what_changed": "First analysis.",
    }
    base.update(overrides)
    return Insight.model_validate(base)


def test_insight_rejects_unknown_placement():
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        insight(placement="checkout")


def test_build_services_copies_the_snapshot_in_demo_mode(tmp_path, monkeypatch):
    snapshot = tmp_path / "demo.sqlite"
    db.connect(snapshot).close()
    monkeypatch.setattr(config, "DEMO_MODE", True)
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(config, "DEMO_DB_PATH", snapshot)
    monkeypatch.delenv("ADAPTY_SECRET_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from resolve.web import build_services_from_env

    db_path, demo, *clients = build_services_from_env()  # no MissingKey without keys
    assert demo is True and db_path == tmp_path / "data" / "demo-run.sqlite" and db_path.exists()
    assert all(c is None for c in clients), "demo mode builds no Adapty, LLM or CLI client"


# --- grouping, facts and checks for the hypothesis (docs/agents.md, "Insights agent") ---------------------------

def _t(id, customer, category, flow=None, created="2026-11-01T00:00:00Z", summary=None):
    context = {"placement": "onboarding", "flow": flow, "ab_test": "Onboarding: annual trial vs weekly first"} if flow else {}
    return {"id": id, "customer_user_id": customer, "category": category, "context": context, "created_at": created,
            "subject": f"subject {id}", "triage": {"summary": summary or f"summary {id}"}, "profile": None}


def test_group_tickets_takes_the_flow_from_the_same_user_then_the_seed_file():
    tickets = [
        _t("t-1", "u1", "charged_after_trial", "onb_weekly"),
        _t("f-1", "u1", "charged_after_trial", created="2026-11-05T00:00:00Z"),  # chat handoff: no context of its own
        _t("f-2", "u2", "charged_after_trial"),  # only the seed file knows u2
        _t("f-3", "u3", "refund_request"),  # nobody knows u3
    ]
    groups = insights.group_tickets(tickets, seed={"u2": {"placement": "onboarding", "flow": "onb_weekly"}})
    assert groups["by_category_flow"] == {"charged_after_trial": {"onb_weekly": 3}, "refund_request": {"unknown": 1}}
    assert (groups["users"], groups["flow_known"]) == (3, 3)
    top = groups["cells"][0]
    assert (top["category"], top["flow"], top["tickets"], top["users"]) == ("charged_after_trial", "onb_weekly", 3, 2)
    assert [e["id"] for e in top["examples"]] == ["f-1", "f-2", "t-1"], "newest first, one per user before repeats"
    assert groups["ab_tests"] == {"Onboarding: annual trial vs weekly first": {"Onboarding - Weekly first": 2}}, "f-1 inherits u1's test arm"


def test_cells_rank_by_distinct_users_not_tickets():
    tickets = [_t(f"a{i}", "same-user", "other", "settings") for i in range(3)] + [_t(f"b{i}", f"u{i}", "refund_request", "main") for i in range(2)]
    cells = insights.group_tickets(tickets, seed={})["cells"]
    assert [(c["category"], c["users"], c["tickets"]) for c in cells] == [("refund_request", 2, 2), ("other", 1, 3)]


def test_seed_contexts_reads_the_seed_file_and_tolerates_a_missing_one(tmp_path):
    path = tmp_path / "tickets.json"
    path.write_text(json.dumps([{"customer_user_id": "u9", "context": {"flow": "main", "placement": "main"}}, {"customer_user_id": "u8", "context": {}}]))
    assert insights.seed_contexts(path) == {"u9": {"flow": "main", "placement": "main"}}
    assert insights.seed_contexts(tmp_path / "missing.json") == {}


def _facts():
    tickets = [_t("t-1", "u1", "charged_after_trial", "onb_weekly"), _t("t-2", "u2", "charged_after_trial", "onb_weekly", created="2026-11-02T00:00:00Z"), _t("t-3", "u3", "refund_request")]
    return insights.facts(insights.group_tickets(tickets, seed={}))


def test_facts_carry_shares_examples_and_the_placement_catalogue():
    facts = _facts()
    assert facts["inbox"]["flow_known"] == "2 of 3 triaged tickets" and "not rates" in facts["inbox"]["note"]
    assert facts["categories"][0] == {"category": "charged_after_trial", "meaning": "the user did not expect the charge when a free trial converted.", "tickets": 2, "share_of_triaged_pct": 67, "users": 2}
    assert facts["cells"][0]["examples"][0]["summary"] == "summary t-2"
    onboarding = next(p for p in facts["placements"] if p["placement"] == "onboarding")
    assert onboarding["draftable_now"] and {"title": "Onboarding - Annual trial", "offer": "annual plan with a 7-day trial up front"} in onboarding["flows"]
    assert "previous" not in facts and "changes" not in facts


def test_check_insight_accepts_a_grounded_insight():
    assert insights.check_insight(insight(headline="2 of 3 tickets are charged_after_trial.", evidence=["charged_after_trial: 2"]), _facts()) is None


@pytest.mark.parametrize("overrides, reason", [
    ({"cited_cells": [{"category": "charged_after_trial", "flow": "onb_weekly", "tickets": 12, "users": 2}]}, "has tickets 2, users 2"),
    ({"cited_cells": [{"category": "cancel_how", "flow": "main", "tickets": 1, "users": 1}]}, "no cell cancel_how × main"),
    ({"cited_cells": [{"category": "refund_request", "flow": "unknown", "tickets": 1, "users": 1}]}, "unknown is not a paywall"),
    ({"control_flow": "Onboarding - Annual trial"}, "control_flow must be the flow of cited_cells[0]"),
    ({"variant_flow": None}, "variant_flow must be another flow"),
    ({"variant_source": "edited_copy"}, "must be null"),
    ({"ticket_ids": ["x-404"]}, "not among the example tickets"),
    ({"cited_cells": [{"category": "charged_after_trial", "flow": "onb_weekly", "tickets": 2, "users": 2}, {"category": "refund_request", "flow": "unknown", "tickets": 1, "users": 1}]}, None),
    ({"headline": "12 of 60 tickets are charged_after_trial."}, "numbers that are not in the facts (12, 60)"),
    ({"evidence": ["charged_after_trial share: 45%"]}, "evidence[0] has numbers"),
])
def test_check_insight_rejects_what_the_facts_do_not_support(overrides, reason):
    fields = {"headline": "2 of 3 tickets.", "evidence": ["cell: 2"], **overrides}
    got = insights.check_insight(insight(**fields), _facts())
    assert got is None if reason is None else reason in got


def test_check_insight_wants_edited_copy_on_a_single_flow_placement():
    tickets = [_t("t-1", "u1", "cancel_how", "winback")]
    tickets[0]["context"]["placement"] = "win_back"
    facts = insights.facts(insights.group_tickets(tickets, seed={}))
    bad = insight(cited_cells=[{"category": "cancel_how", "flow": "winback", "tickets": 1, "users": 1}], headline="1 ticket.", evidence=["x: 1"],
                  placement="win_back", control_flow="Win-back - Monthly", variant_flow="Win-back - Monthly")
    assert "has no other flow; use variant_source edited_copy" in insights.check_insight(bad, facts)
    assert insights.check_insight(bad.model_copy(update={"variant_source": "edited_copy", "variant_flow": None}), facts) is None


def test_insight_drops_json_escapes_the_model_wrote_into_the_text():
    assert insight(headline='Most come from \\"Onboarding - Weekly first\\".').headline == 'Most come from "Onboarding - Weekly first".'


def _tie_tickets():
    return [_t("w1", "u1", "refund_request", "onb_weekly"), _t("w2", "u2", "refund_request", "onb_weekly"), _t("w3", "u3", "cancel_how", "onb_weekly"),
            _t("a1", "u4", "no_access_after_purchase", "onb_trial"), _t("a2", "u5", "no_access_after_purchase", "onb_trial")]


def test_a_tie_puts_the_busier_flow_first():
    cells = insights.group_tickets(_tie_tickets(), seed={})["cells"]
    assert [(c["category"], c["flow"]) for c in cells[:2]] == [("refund_request", "onb_weekly"), ("no_access_after_purchase", "onb_trial")]


def test_check_insight_keeps_the_previous_main_cell_on_a_tie_and_wants_the_main_cell_first():
    groups = insights.group_tickets(_tie_tickets(), seed={})
    previous = {"hypothesis": "h", "placement": "onboarding", "control_flow": "Onboarding - Weekly first", "cited_cells": [{"category": "refund_request", "flow": "onb_weekly"}],
                "snapshot": insights.snapshot(groups, {"category": "refund_request", "flow": "onb_weekly"})}
    facts = insights.facts(groups, previous)
    flip = insight(cited_cells=[{"category": "no_access_after_purchase", "flow": "onb_trial", "tickets": 2, "users": 2}], headline="2 users.", evidence=["x: 2"],
                   ticket_ids=[], control_flow="Onboarding - Annual trial", variant_flow="Onboarding - Weekly first")
    assert "(a tie is not a change); keep refund_request × onb_weekly as cited_cells[0]" in insights.check_insight(flip, facts)
    keep = insight(cited_cells=[{"category": "refund_request", "flow": "onb_weekly", "tickets": 2, "users": 2}], headline="2 users.", evidence=["x: 2"], ticket_ids=[], what_changed="Kept.")
    assert insights.check_insight(keep, facts) is None
    weaker_first = keep.model_copy(update={"cited_cells": [insights.CitedCell(category="cancel_how", flow="onb_weekly", tickets=1, users=1), *keep.cited_cells]})
    assert "put the cell the test rests on first" in insights.check_insight(weaker_first, facts)


def test_check_insight_keeps_the_previous_test_while_the_main_cell_is_the_same():
    groups = insights.group_tickets(_tie_tickets(), seed={})
    previous = {"hypothesis": "h", "placement": "onboarding", "control_flow": "Onboarding - Weekly first", "variant_source": "existing_flow", "variant_flow": "Onboarding - Annual trial",
                "cited_cells": [{"category": "refund_request", "flow": "onb_weekly"}], "snapshot": insights.snapshot(groups, {"category": "refund_request", "flow": "onb_weekly"})}
    facts = insights.facts(groups, previous)
    same = insight(cited_cells=[{"category": "refund_request", "flow": "onb_weekly", "tickets": 2, "users": 2}], headline="2 users.", evidence=["x: 2"], ticket_ids=[], hypothesis="Reworded.", what_changed="Kept.")
    assert insights.check_insight(same, facts) is None
    assert "say whether its test was kept or moved" in insights.check_insight(same.model_copy(update={"what_changed": "First analysis."}), facts)
    other_arm = same.model_copy(update={"variant_source": "edited_copy", "variant_flow": None})
    assert "keep its test: placement onboarding, control_flow Onboarding - Weekly first, variant_source existing_flow" in insights.check_insight(other_arm, facts)
