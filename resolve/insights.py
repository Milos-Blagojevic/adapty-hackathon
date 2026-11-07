"""Level 1: all tickets → grouping, one hypothesis, one A/B-test draft in Adapty.

Grouping needs no model: category × product (product from the live profile) and category × the
paywall/flow the user saw (from the offline CSV mapping stored on the ticket at generation time; the
live profile has no "paywall seen" field, and the UI says so). The hypothesis is one Opus call,
cached in the `insights` table. The draft goes through the Adapty CLI (`adapty ab-tests create`,
draft only; this module never starts, stops or replaces a test).
"""

from __future__ import annotations

import json
import subprocess
from collections import Counter, defaultdict
from typing import Literal

import anthropic
import pydantic
from pydantic import BaseModel, Field

from resolve import config
from resolve.agent import MAX_TOKENS, AgentError
from resolve.tickets import CATEGORIES

# Read from the seeded account on Sat 7 Nov with `adapty placements list` and `adapty flows list`
# (all flows published). Keys are the offline CSV's `placement` and `flow` values.
PLACEMENTS: dict[str, dict] = {
    "onboarding": {
        "id": "ac6fe2ca-44ac-4066-941d-47491a3fe8ff",
        "title": "Onboarding",
        "flows": {
            "onb_weekly": {"id": "9a9eb50a-3c64-4426-bb00-4f486355a03c", "title": "Onboarding - Weekly first"},
            "onb_trial": {"id": "4081e52e-415b-4a24-acf4-128b652a7c5f", "title": "Onboarding - Annual trial"},
        },
    },
    "main": {
        "id": "81cdb75b-3384-4098-a360-f78e3d23641c",
        "title": "Main paywall",
        "flows": {
            "main": {"id": "0356d4cd-a08b-408f-923a-b59b3f46ec27", "title": "Main - 3 plans"},
            "main_lifetime": {"id": "b03574d5-41b1-41c2-a2ef-3ed9ebb63558", "title": "Main - Lifetime anchor"},
        },
    },
    "ai_limit_reached": {
        "id": "3b4532e9-5b62-4607-83a9-bf67dfa86365",
        "title": "AI edits limit reached",
        "flows": {"ai_limit": {"id": "bf15d98c-3bdc-4847-911e-0e8c6a2fd5dc", "title": "AI limit reached - Pro"}},
    },
    "settings_upgrade": {
        "id": "c6ab0960-87e8-445e-84e1-3877d2661e43",
        "title": "Settings - Upgrade",
        "flows": {"settings": {"id": "0936b937-2536-4b5c-876d-a358be375169", "title": "Settings - Upgrade"}},
    },
    "win_back": {
        "id": "e2b22cb4-22d4-41e2-85d9-8c88e76a49fa",
        "title": "Win-back",
        "flows": {"winback": {"id": "1fc596cd-dbe7-4ac5-8c25-c7c120a8955d", "title": "Win-back - Monthly"}},
    },
}
FLOW_TITLES = {flow: data["title"] for placement in PLACEMENTS.values() for flow, data in placement["flows"].items()}
PLACEMENT_IDS = tuple(PLACEMENTS)


def flow_title(flow: str | None) -> str:
    return FLOW_TITLES.get(flow or "", flow or "unknown")


GRANT_PRODUCT = "adapty_server_side_product"


def product_of(ticket: dict) -> str:
    """The store product behind the complaint: the subscription's, else the access level's, else none.

    A granted access level reports `adapty_server_side_product`, so the subscription wins; "granted"
    appears only when the profile has nothing but the grant.
    """
    profile = ticket.get("profile")
    if not profile:
        return "unknown"
    subs = profile.get("subscriptions") or profile.get("non_subscriptions") or []
    if subs:
        return _product_name(subs[-1].get("store_product_id", ""))
    for level in profile.get("access_levels", []):
        if level.get("store_product_id"):
            return _product_name(level["store_product_id"])
    return "none"


def _product_name(store_product_id: str) -> str:
    if store_product_id == GRANT_PRODUCT:
        return "granted"
    name = store_product_id.replace("lumen.", "").replace("lumen_", "")
    return name.split(":")[0] or "unknown"


def group_tickets(tickets: list[dict]) -> dict:
    """Counts the insights page shows. Uses the agent's category; untriaged tickets count as 'untriaged'."""
    by_category: Counter = Counter()
    by_category_product: dict[str, Counter] = defaultdict(Counter)
    by_category_flow: dict[str, Counter] = defaultdict(Counter)
    by_flow: Counter = Counter()
    by_placement: Counter = Counter()
    untriaged = 0
    for ticket in tickets:
        category = ticket.get("category")
        if not category:
            untriaged += 1
            continue
        by_category[category] += 1
        by_category_product[category][product_of(ticket)] += 1
        flow = ticket.get("context", {}).get("flow") or "unknown"
        by_category_flow[category][flow] += 1
        by_flow[flow] += 1
        by_placement[ticket.get("context", {}).get("placement") or "unknown"] += 1
    top = by_category.most_common(1)
    top_category = top[0][0] if top else None
    top_flow = by_category_flow[top_category].most_common(1)[0][0] if top_category else None
    return {
        "total": len(tickets),
        "triaged": len(tickets) - untriaged,
        "untriaged": untriaged,
        "by_category": [{"category": c, "count": n} for c, n in by_category.most_common()],
        "by_category_product": {c: dict(by_category_product[c].most_common()) for c in by_category},
        "by_category_flow": {c: dict(by_category_flow[c].most_common()) for c in by_category},
        "by_flow": [{"flow": f, "title": flow_title(f), "count": n} for f, n in by_flow.most_common()],
        "by_placement": [{"placement": p, "title": PLACEMENTS.get(p, {}).get("title", p), "count": n} for p, n in by_placement.most_common()],
        "top_category": top_category,
        "top_flow": top_flow,
        "top_flow_count": by_category_flow[top_category][top_flow] if top_category else 0,
        "top_placement": next((p for p, data in PLACEMENTS.items() if top_flow in data["flows"]), None),
        "categories": list(CATEGORIES),
    }


# --- The hypothesis (one model call, cached) --------------------------------------------------------


class Insight(BaseModel):
    headline: str = Field(description="One line for the top of the page: the biggest complaint cluster and where it comes from.")
    hypothesis: str = Field(description="One testable sentence: what change to which paywall should reduce which complaint.")
    placement: Literal[*PLACEMENT_IDS] = Field(description="The placement whose flow the hypothesis is about.")
    control_flow: str = Field(description="The flow users who complain saw (its title).")
    variant: str = Field(description="What the variant should change, concretely (copy, trial disclosure, plan order, price framing).")
    expected_effect: str = Field(description="What to measure and the direction: fewer tickets of which category, refund rate, trial-to-paid.")
    evidence: list[str] = Field(min_length=1, description="The counts you relied on, each as 'what: number'.")


SYSTEM_PROMPT = """You are the growth analyst of Lumen, an AI photo editor sold by subscription. You get the support inbox aggregated: complaint categories, the product behind each complaint, and the onboarding or paywall flow each complaining user saw (from the app's own records). Turn it into one testable paywall hypothesis for an A/B test in Adapty.

Flows and placements:
- onboarding: "Onboarding - Weekly first" (weekly plan with a 3-day trial up front) and "Onboarding - Annual trial" (annual with a 7-day trial up front)
- main: "Main - 3 plans" and "Main - Lifetime anchor"
- ai_limit_reached: "AI limit reached - Pro"; settings_upgrade: "Settings - Upgrade"; win_back: "Win-back - Monthly"

Rules: pick the largest cluster, name the flow most of those users saw, propose one concrete change to that flow, and say what should move. Numbers only from the data given. One sentence per field, plain English."""


def analyse(client: anthropic.Anthropic, model: str, groups: dict) -> Insight:
    if not groups["triaged"]:
        raise AgentError("no triaged tickets yet; open tickets (or run the batch) first")
    data = {k: groups[k] for k in ("total", "triaged", "by_category", "by_category_product", "by_category_flow", "by_flow", "by_placement")}
    try:
        response = client.messages.parse(
            model=model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"<inbox_aggregates>\n{json.dumps(data, indent=1)}\n</inbox_aggregates>"}],
            output_format=Insight,
        )
    except anthropic.APIError as exc:
        raise AgentError(f"Anthropic API error: {exc.__class__.__name__}: {exc}", calls=1) from None
    except pydantic.ValidationError as exc:
        raise AgentError(f"insight did not match the schema: {exc.errors()[0].get('msg')}", calls=1) from None
    if response.parsed_output is None:
        raise AgentError("empty structured output", calls=1)
    return response.parsed_output


# --- The draft (Adapty CLI) -------------------------------------------------------------------------


class DraftError(Exception):
    pass


class Runner:
    """Runs the Adapty CLI. Only `ab-tests create` and `feedback` are ever invoked from here."""

    def __init__(self, binary: str = "adapty", timeout: int = 60):
        self.binary = binary
        self.timeout = timeout

    def run(self, *args: str) -> dict:
        command = [self.binary, *args, "--json"]
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=self.timeout, check=False)
        except FileNotFoundError:
            raise DraftError(f"`{self.binary}` is not installed (npm i -g adapty)") from None
        except subprocess.TimeoutExpired:
            raise DraftError(f"`{self.binary} {args[0]}` timed out after {self.timeout} s") from None
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            try:
                detail = json.loads(completed.stdout).get("error", {}).get("message") or detail
            except (ValueError, AttributeError):
                pass
            raise DraftError(f"`{self.binary} {args[0]} {args[1]}` failed (exit {completed.returncode}): {detail[:300]}")
        try:
            return json.loads(completed.stdout)
        except ValueError:
            raise DraftError("the CLI returned no JSON") from None


def draft_ab_test(runner: Runner, placement_key: str, hypothesis: str = "") -> dict:
    """Create a draft flow test on `placement_key` with that placement's flows as arms. Never starts it."""
    placement = PLACEMENTS.get(placement_key)
    if placement is None:
        raise DraftError(f"unknown placement {placement_key}")
    flows = list(placement["flows"].values())
    if len(flows) < 2:
        raise DraftError(f"{placement['title']} has one flow; an A/B test needs two arms (build a variant flow first)")
    weight = 100 // len(flows)
    arms = [{"flow_id": f["id"], "weight": weight, "title": f["title"]} for f in flows]
    arms[0]["weight"] += 100 - weight * len(flows)
    title = f"Resolve: {placement['title']} complaints"
    goal = (hypothesis or "Reduce purchase complaints from this placement").strip()[:500]
    result = runner.run(
        "ab-tests", "create",
        "--app", config.APP_ID,
        "--placement", placement["id"],
        "--title", title,
        "--goal", goal,
        "--all-users",
        "--flows", json.dumps(arms),
    )
    test = result.get("data", result) if isinstance(result, dict) else result
    test_id = test.get("ab_test_id") or test.get("id")
    if not test_id:
        raise DraftError(f"the CLI returned no test id: {json.dumps(test)[:200]}")
    draft = {
        "ab_test_id": test_id,
        "title": test.get("title", title),
        "state": test.get("state"),
        "placement": placement["title"],
        "placement_id": placement["id"],
        "arms": arms,
        "goal": goal,
    }
    _feedback(runner, test_id)
    return draft


def _feedback(runner: Runner, test_id: str) -> None:
    """The ab-tests skill asks for one generic note per created test. Best effort; never raises."""
    try:
        runner.run(
            "feedback",
            "--feature", "ab-tests",
            "--sentiment", "positive",
            "--message", "Testing: two onboarding flows that differ in which plan and trial they lead with. Source: own data, support complaints grouped by flow. Did: created a draft.",
            "--context", f"app {config.APP_ID}, test {test_id}: create",
        )
    except DraftError:
        pass
