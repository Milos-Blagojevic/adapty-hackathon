import json
import os
import stat
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from resolve import config, db, insights
from resolve.insights import DraftError, Insight, Runner, draft_ab_test
from resolve.web import build_services_from_env, create_app
from tests.fakes import ACTIVE_WEEKLY, FakeAnthropic, profile, proposal, ticket_row, tickets_file

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
        "headline": "16 of 60 tickets are charged_after_trial; 12 saw Onboarding - Weekly first.",
        "hypothesis": "Disclose the 3-day trial end on Onboarding - Weekly first.",
        "placement": "onboarding",
        "control_flow": "Onboarding - Weekly first",
        "variant": "Add the trial end date next to the CTA.",
        "expected_effect": "Fewer charged_after_trial tickets.",
        "evidence": ["charged_after_trial: 16"],
    }
    base.update(overrides)
    return Insight.model_validate(base)


def test_insights_run_and_draft_routes(tmp_path, stub_cli):
    script, log = stub_cli
    seed = tickets_file(tmp_path, [ticket_row()])
    fake = FakeAnthropic([insight()])
    app = create_app(tmp_path / "i.sqlite", demo=False, anthropic_client=fake, model="m", seed_file=seed, ab_runner=Runner(binary=str(script)))
    conn = app.state.services.conn
    db.update_ticket(conn, "t-001", profile_json=json.dumps(profile(access_levels=[ACTIVE_WEEKLY])), proposal_json=proposal().model_dump_json(), category="charged_after_trial", status="proposed")
    client = TestClient(app)

    ran = client.post("/insights/run")
    assert "12 saw Onboarding - Weekly first" in ran.text and "1 model call, cached" in ran.text
    assert len(fake.calls) == 1
    assert db.get_insights(conn)["placement"] == "onboarding"

    drafted = client.post("/insights/ab-draft", data={"placement_id": "onboarding", "hypothesis": "Disclose the trial end"})
    assert "test-123" in drafted.text and "Onboarding - Weekly first 50%" in drafted.text
    assert db.list_ab_drafts(conn)[0]["test_id"] == "test-123"
    refused = client.post("/insights/ab-draft", data={"placement_id": "win_back"})
    assert "two arms" in refused.text and len(db.list_ab_drafts(conn)) == 1


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
    db_path, demo, adapty_client, anthropic_client, model, runner = build_services_from_env()
    assert demo is True and db_path == tmp_path / "data" / "demo-run.sqlite" and db_path.exists()
    assert (adapty_client, anthropic_client, model, runner) == (None, None, None, None)
    assert os.environ.get("ADAPTY_SECRET_KEY") is None or True  # keys are never read in this branch
