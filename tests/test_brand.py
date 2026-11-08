"""docs/agents.md, Brand: the name comes from the config; Level 1 and Insights call it a fitness app."""

from datetime import datetime, timezone

from resolve import frontline, insights, triage
from resolve.models import AgentConfig


def test_default_app_name_is_fitbuddy():
    assert AgentConfig().app_name == "FitBuddy"


def test_level1_prompt_reads_the_name_and_says_fitness():
    prompt = triage.system_prompt("Pebble")
    assert "support agent of Pebble, a fitness app" in prompt
    assert "Lumen" not in prompt and "photo" not in prompt.lower()


def test_insights_prompt_reads_the_name_and_says_fitness():
    prompt = insights.system_prompt("Pebble")
    assert "growth analyst of Pebble, a fitness app" in prompt
    assert "{app_name}" not in prompt and "Lumen" not in prompt and "photo" not in prompt.lower()


def test_level1_store_steps_use_the_config_name():
    profile = {"access_levels": [], "subscriptions": [{"store": "play_store", "store_product_id": "lumen_weekly"}], "non_subscriptions": []}
    out = triage.model_profile(profile, datetime(2026, 11, 8, tzinfo=timezone.utc), AgentConfig(app_name="Pebble"), [])
    steps = out["store_steps"]["play_store"]
    assert "Subscriptions → Pebble → Cancel" in steps["cancel"] and "Lumen" not in str(steps)


def test_level2_store_steps_are_templates_on_the_config_name():
    assert not [k for k, t in frontline.STORE_STEPS.items() if "Lumen" in t]
