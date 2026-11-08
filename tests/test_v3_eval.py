"""scripts/eval.py `ungrounded`: evidence checked against the facts code loads (offline, no LLM)."""

import importlib.util
import json

from resolve import config, db
from tests.fakes_v2 import CUID, mock_profile, new_db, paid_profile, services, triage_dict

spec = importlib.util.spec_from_file_location("eval_script", config.ROOT / "scripts" / "eval.py")
eval_script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eval_script)


def test_ungrounded_flags_only_lines_not_in_the_facts(http, tmp_path):
    conn = new_db(tmp_path)
    prof = paid_profile()
    mock_profile(http, prof)
    product = prof["access_levels"][0]["store_product_id"]
    result = triage_dict(evidence=[f"product: {product}", "price: $999.99"])
    ticket = db.get_ticket(conn, "t-001")
    assert ticket["customer_user_id"] == CUID
    out = eval_script.ungrounded(services(conn, None), conn, ticket, json.loads(json.dumps(result)))
    assert out == ["price: $999.99"]


def test_l2_run_scores_code_outcomes_freezes_the_clock_and_writes_nothing(tmp_path):
    """--l2: scenario profile override, frozen clock, simulated grant, steps and log checks (offline)."""
    from resolve import frontline
    from resolve.llm import FakeLLM
    from tests.fakes_v2 import ACTIVE_WEEKLY, SUB_WEEKLY, call, paid_profile, say

    conn = new_db(tmp_path, seed=False)
    prof = paid_profile(access_levels=(dict(ACTIVE_WEEKLY, expires_at="2026-11-08T06:00:00+00:00"),))
    prof["subscriptions"] = [dict(SUB_WEEKLY, expires_at="2026-11-08T06:00:00+00:00")]  # expired now, active at as_of
    overrides: dict = {}
    ro = eval_script.ReadOnlyAdapty(eval_script.profile_reader({}, overrides))
    fake = FakeLLM([
        call("store_instructions", kind="restore", store="play_store"),
        call("grant_access", days=3, reason="paid but locked", user_words="premium is locked"),
        say("Added 3 days."),
    ])
    scenario = {"id": "x", "customer_user_id": CUID, "setup": {"profile": prof}, "messages": ["I paid but premium is locked"],
                "expect": {"grant": "allowed", "handoff": None, "steps_require": ["restore:play_store"], "steps_forbid": ["cancel:play_store"]}}
    rows = eval_script.run_l2(services(conn, fake, adapty=ro), conn, ro, overrides, [scenario], "premium", "2026-11-07T18:00:00Z", use_judge=False)
    (row,) = rows
    assert row["grant"] == "allowed" and row["handoff"] is None and row["steps_ran"] == ["restore:play_store"]
    assert row["correct"] is True, row
    assert len(ro.attempts) == 1 and overrides == {}, "the grant was simulated, the override removed"
    assert frontline.datetime.now().year >= 2026 and frontline.datetime.__name__ == "datetime", "the clock is restored"

