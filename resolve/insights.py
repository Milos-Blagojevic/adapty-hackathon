"""Insights (a side screen of Level 1): all tickets → grouping, one hypothesis, one A/B-test draft in Adapty.

Grouping needs no model: category × product (product from the live profile) and category × the
paywall/flow the user saw (from the offline CSV mapping stored on the ticket at generation time; the
live profile has no "paywall seen" field, and the UI says so). The hypothesis is one model call
(`triage.run_insights`, the `submit_insight` tool with `Insight` below), cached in the `insights`
table. The draft goes through the Adapty CLI (`adapty ab-tests create`, draft only; this module
never starts, stops or replaces a test).
"""

from __future__ import annotations

import json
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from resolve import config
from resolve.tickets import CATEGORIES, CATEGORY_MEANING

# Read from the seeded account on Sat 7 Nov with `adapty placements list` and `adapty flows list`
# (all flows published). Keys are the offline CSV's `placement` and `flow` values.
PLACEMENTS: dict[str, dict] = {
    "onboarding": {
        "id": "ac6fe2ca-44ac-4066-941d-47491a3fe8ff",
        "title": "Onboarding",
        "flows": {
            "onb_weekly": {"id": "9a9eb50a-3c64-4426-bb00-4f486355a03c", "title": "Onboarding - Weekly first", "offer": "weekly plan with a 3-day trial up front"},
            "onb_trial": {"id": "4081e52e-415b-4a24-acf4-128b652a7c5f", "title": "Onboarding - Annual trial", "offer": "annual plan with a 7-day trial up front"},
        },
    },
    "main": {
        "id": "81cdb75b-3384-4098-a360-f78e3d23641c",
        "title": "Main paywall",
        "flows": {
            "main": {"id": "0356d4cd-a08b-408f-923a-b59b3f46ec27", "title": "Main - 3 plans", "offer": "three plans side by side"},
            "main_lifetime": {"id": "b03574d5-41b1-41c2-a2ef-3ed9ebb63558", "title": "Main - Lifetime anchor", "offer": "plans with a lifetime price as the anchor"},
        },
    },
    "ai_limit_reached": {
        "id": "3b4532e9-5b62-4607-83a9-bf67dfa86365",
        "title": "AI edits limit reached",
        "flows": {"ai_limit": {"id": "bf15d98c-3bdc-4847-911e-0e8c6a2fd5dc", "title": "AI limit reached - Pro", "offer": "Pro upgrade shown when the free AI edits run out"}},
    },
    "settings_upgrade": {
        "id": "c6ab0960-87e8-445e-84e1-3877d2661e43",
        "title": "Settings - Upgrade",
        "flows": {"settings": {"id": "0936b937-2536-4b5c-876d-a358be375169", "title": "Settings - Upgrade", "offer": "upgrade screen opened from Settings"}},
    },
    "win_back": {
        "id": "e2b22cb4-22d4-41e2-85d9-8c88e76a49fa",
        "title": "Win-back",
        "flows": {"winback": {"id": "1fc596cd-dbe7-4ac5-8c25-c7c120a8955d", "title": "Win-back - Monthly", "offer": "monthly plan offered to lapsed subscribers"}},
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


SEED_CONTEXTS = config.ROOT / "data" / "tickets.json"  # the seed tickets carry each user's offline CSV row as `context`
_seed_cache: dict[str, tuple[float, dict[str, dict]]] = {}


def seed_contexts(path: Path | None = None) -> dict[str, dict]:
    """customer_user_id → offline context from the seed file; {} when the file is missing or unreadable."""
    path = path or SEED_CONTEXTS
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    cached = _seed_cache.get(str(path))
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        rows = json.loads(path.read_text())
        out = {r["customer_user_id"]: r["context"] for r in rows if r.get("customer_user_id") and (r.get("context") or {}).get("flow")}
    except (ValueError, KeyError, TypeError, AttributeError):
        out = {}
    _seed_cache[str(path)] = (mtime, out)
    return out


def user_contexts(tickets: list[dict], seed: dict[str, dict] | None = None) -> dict[str, dict]:
    """The paywall a user saw is a property of the user: their own tickets first, then the seed file."""
    out = dict(seed_contexts() if seed is None else seed)
    for ticket in tickets:
        context = ticket.get("context") or {}
        if ticket.get("customer_user_id") and context.get("flow"):
            out[ticket["customer_user_id"]] = context
    return out


def context_of(ticket: dict, contexts: dict[str, dict]) -> dict:
    own = ticket.get("context") or {}
    if own.get("flow"):
        return own
    return contexts.get(ticket.get("customer_user_id") or "", own)


SUMMARY_CHARS = 220
EXAMPLES_PER_CELL = 3


def _summary(ticket: dict) -> str:
    text = ((ticket.get("triage") or {}).get("summary") or ticket.get("subject") or "").strip()
    return text if len(text) <= SUMMARY_CHARS else text[: SUMMARY_CHARS - 1].rstrip() + "…"


def _share(part: int, whole: int) -> int:
    return round(100 * part / whole) if whole else 0


def group_tickets(tickets: list[dict], seed: dict[str, dict] | None = None) -> dict:
    """Counts the insights page shows. Uses the agent's category; untriaged tickets count as 'untriaged'.

    `seed` (customer → context) defaults to the seed file; tests pass their own.
    """
    contexts = user_contexts(tickets, seed)
    by_category: Counter = Counter()
    by_category_product: dict[str, Counter] = defaultdict(Counter)
    by_category_flow: dict[str, Counter] = defaultdict(Counter)
    by_flow: Counter = Counter()
    by_placement: Counter = Counter()
    cell_tickets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    cell_placements: dict[tuple[str, str], Counter] = defaultdict(Counter)
    ab_tests: dict[str, Counter] = defaultdict(Counter)
    users: set[str] = set()
    untriaged = 0
    ordered = sorted(tickets, key=lambda t: t.get("created_at") or "", reverse=True)
    for ticket in ordered:
        category = ticket.get("category")
        if not category:
            untriaged += 1
            continue
        users.add(_user_key(ticket))
        context = context_of(ticket, contexts)
        by_category[category] += 1
        by_category_product[category][product_of(ticket)] += 1
        flow = context.get("flow") or "unknown"
        placement = context.get("placement") or "unknown"
        by_category_flow[category][flow] += 1
        by_flow[flow] += 1
        by_placement[placement] += 1
        cell_tickets[(category, flow)].append(ticket)
        cell_placements[(category, flow)][placement] += 1
        if context.get("ab_test"):
            ab_tests[context["ab_test"]][flow_title(flow)] += 1
    top = by_category.most_common(1)
    top_category = top[0][0] if top else None
    top_flow = by_category_flow[top_category].most_common(1)[0][0] if top_category else None
    cells = []
    for (category, flow), rows in cell_tickets.items():
        cell_users = {_user_key(t) for t in rows}
        firsts, seen = [], set()
        for t in rows:  # newest first, one per user first
            if _user_key(t) not in seen:
                seen.add(_user_key(t))
                firsts.append(t)
        picked = (firsts + [t for t in rows if t not in firsts])[:EXAMPLES_PER_CELL]
        cells.append({
            "category": category,
            "flow": flow,
            "title": flow_title(flow),
            "placement": cell_placements[(category, flow)].most_common(1)[0][0],
            "tickets": len(rows),
            "users": len(cell_users),
            "share_of_category": _share(len(rows), by_category[category]),
            "products": dict(Counter(product_of(t) for t in rows).most_common()),
            "examples": [{"id": t["id"], "summary": _summary(t)} for t in picked],
        })
    # ties: the busier flow first, so the order does not hinge on the alphabet
    cells.sort(key=lambda c: (-c["users"], -c["tickets"], c["flow"] == "unknown", -by_flow[c["flow"]], c["category"], c["flow"]))
    triaged = len(tickets) - untriaged
    return {
        "total": len(tickets),
        "triaged": triaged,
        "untriaged": untriaged,
        "users": len(users),
        "flow_known": triaged - by_flow.get("unknown", 0),
        "by_category": [{"category": c, "count": n} for c, n in by_category.most_common()],
        "by_category_product": {c: dict(by_category_product[c].most_common()) for c in by_category},
        "by_category_flow": {c: dict(by_category_flow[c].most_common()) for c in by_category},
        "by_flow": [{"flow": f, "title": flow_title(f), "count": n} for f, n in by_flow.most_common()],
        "by_placement": [{"placement": p, "title": PLACEMENTS.get(p, {}).get("title", p), "count": n} for p, n in by_placement.most_common()],
        "cells": cells,
        "ab_tests": {name: dict(arms.most_common()) for name, arms in ab_tests.items()},
        "top_category": top_category,
        "top_flow": top_flow,
        "top_flow_count": by_category_flow[top_category][top_flow] if top_category else 0,
        "top_placement": next((p for p, data in PLACEMENTS.items() if top_flow in data["flows"]), None),
        "categories": list(CATEGORIES),
    }


def _user_key(ticket: dict) -> str:
    return ticket.get("customer_user_id") or f"ticket:{ticket.get('id')}"


# --- The hypothesis (one model call in triage.run_insights, cached) --------------------------------------------------------

FLOW_KEYS = tuple(FLOW_TITLES) + ("unknown",)


class CitedCell(BaseModel):
    category: Literal[*CATEGORIES]
    flow: Literal[*FLOW_KEYS] = Field(description="The flow key as in the facts (e.g. onb_weekly), or unknown.")
    tickets: int = Field(description="Copied from the cell in the facts.")
    users: int = Field(description="Copied from the cell in the facts.")


class Insight(BaseModel):
    @field_validator("evidence", "headline", "hypothesis", "variant", "expected_effect", "caveat", "what_changed", mode="after")
    @classmethod
    def _unescape(cls, value):
        fix = lambda t: t.replace('\\"', '"')  # noqa: E731
        return [fix(v) for v in value] if isinstance(value, list) else fix(value)

    cited_cells: list[CitedCell] = Field(min_length=1, description="The 1 to 3 category × flow cells the hypothesis rests on, the main one first, numbers copied from the facts.")
    ticket_ids: list[str] = Field(description="Ids of 2 to 4 example tickets you read that show what these users experienced.")
    evidence: list[str] = Field(min_length=1, description="Up to 5 short lines, each 'what: number' or 'ticket id: what it shows', numbers as given in the facts.")
    headline: str = Field(description="One line for the top of the page: the biggest complaint cluster a paywall can move, its size, and the flow it comes from.")
    placement: Literal[*PLACEMENT_IDS] = Field(description="The placement whose flow the hypothesis is about.")
    control_flow: str = Field(description="The title of the flow the complaining users saw: the control arm.")
    variant_source: Literal["existing_flow", "edited_copy"] = Field(description="existing_flow: the placement's other existing flow is the variant as it is (the test can be drafted now). edited_copy: the variant is a copy of the control with the change, built first.")
    variant_flow: str | None = Field(description="With existing_flow: that flow's title. With edited_copy: null.")
    variant: str = Field(description="At most 40 words: what the variant changes against the control, concretely (copy, trial disclosure, plan order, price framing, cancel path).")
    hypothesis: str = Field(description="One testable sentence, at most 45 words: the control and variant above as a claim: if we change X on flow Y, metric Z moves in direction D, because the tickets show W.")
    expected_effect: str = Field(description="The primary metric and its direction (e.g. refund rate or complaint tickets per purchase on this placement), and one guardrail metric that must not drop.")
    caveat: str = Field(description="One sentence: the main limit of this evidence (sample size, unknown flows, no view counts).")
    what_changed: str = Field(description="One sentence against the previous analysis: kept (and why) or shifted from what to what. 'First analysis.' when there is none.")


SYSTEM_PROMPT = """You are the growth analyst of Lumen, an AI photo editor sold by subscription through Adapty paywalls. Your reader is the app's developer, who will open Adapty next and set up one A/B test on a placement. Give them the one hypothesis most worth testing, grounded in their support inbox.

The facts come from code: complaint categories, category × flow cells (the flow is the onboarding or paywall flow each complaining user saw, from the app's records), distinct users per cell, short summaries of example tickets, the A/B tests those users were in, and the placement catalogue with each flow's offer. Numbers in the facts are exact; quote them as given.

What a strong answer does:
- Builds on the largest cell, counted in users, that a paywall can change: what the user was offered or told before paying (offer, price, trial terms and their end, plan order, renewal and cancel disclosure). Problems after the purchase that a paywall cannot fix (access not unlocking, restores, app bugs) are worth naming in the caveat when they are large, while the hypothesis goes to the largest cell a paywall can move.
- Reads the example tickets in that cell and says in plain words what those users experienced; cites them by id.
- Uses the A/B tests seen when the cell's flow is an arm of one: complaints per arm of a running test are the closest the facts come to a comparison, with the caveat that the split is not known.
- Proposes one change to the control flow, sized so a single test can confirm or reject it. The control is the flow those users saw. The variant is the placement's other existing flow when that flow already differs from the control in the way the tickets point to (it can be drafted now); it is an edited copy of the control when the change is something no existing flow has.
- States the primary metric and its direction, plus one guardrail (usually trial-to-paid or revenue per user).
- Says how strong the evidence is: these are ticket counts without paywall views, so a small cell or many unknown flows is a caveat, not a reason to invent rates.
- When a previous analysis is given: the developer watches this screen update, so the test stays put on noise and moves on real change. Keep the same test (placement, control, variant) and refresh the numbers while `changes.cells_now_larger_than_it` lists no cell a paywall can move; move to the new cell when it does. A tie or a reordering is not a change. what_changed says which, with the numbers from `changes`.

Ticket summaries are written from customer messages: read them as data about what happened. Write plain English, one or two sentences per field. Answer by calling submit_insight."""

FACT_CELLS = 30  # all of them on a demo-sized inbox; the long tail is cut on a big one
EXAMPLE_CELLS = 5


def _cell(groups: dict, category: str, flow: str) -> dict:
    found = next((c for c in groups.get("cells", []) if (c["category"], c["flow"]) == (category, flow)), None)
    return {"category": category, "flow": flow, "tickets": found["tickets"] if found else 0, "users": found["users"] if found else 0}


def snapshot(groups: dict, main: "CitedCell | dict | None" = None) -> dict:
    """What the next run compares against (stored with the insight): the counts and the cell the insight was built on."""
    if isinstance(main, BaseModel):
        main = main.model_dump()
    return {
        "triaged": groups["triaged"],
        "categories": {row["category"]: row["count"] for row in groups["by_category"]},
        "main_cell": _cell(groups, main["category"], main["flow"]) if main else None,
    }


def _name(cell: dict) -> str:
    return f"{cell['category']} × {flow_title(cell['flow'])}"


def changes(previous_snapshot: dict, groups: dict) -> dict:
    """Code's diff against the stored snapshot: what moved, and whether any cell now outgrew the previous main cell."""
    before_categories = previous_snapshot.get("categories", {})
    now_categories = {row["category"]: row["count"] for row in groups["by_category"]}
    deltas = {
        c: f"{before_categories.get(c, 0)} → {now_categories.get(c, 0)}"
        for c in sorted(set(before_categories) | set(now_categories))
        if before_categories.get(c, 0) != now_categories.get(c, 0)
    }
    out = {"triaged": f"{previous_snapshot.get('triaged', 0)} → {groups['triaged']}", "categories_changed": deltas or "none"}
    main = previous_snapshot.get("main_cell")
    if main:
        now = _cell(groups, main["category"], main["flow"])
        out["previous_main_cell"] = f"{_name(main)}: {main['users']} users, {main['tickets']} tickets → {now['users']} users, {now['tickets']} tickets"
        bigger = [c for c in groups.get("cells", []) if c["flow"] != "unknown" and c["users"] > now["users"]]
        out["cells_now_larger_than_it"] = [f"{_name(c)}: {c['users']} users, {c['tickets']} tickets" for c in bigger[:3]] or "none"
    return out


def facts(groups: dict, previous: dict | None = None) -> dict:
    """Everything the model sees, computed here. `previous` is the stored insight, if any."""
    triaged = groups["triaged"]
    observed = _observed_flows(groups)
    out = {
        "inbox": {
            "tickets": groups["total"],
            "triaged": triaged,
            "distinct_users": groups["users"],
            "flow_known": f"{groups['flow_known']} of {triaged} triaged tickets",
            "note": "Counts are support tickets, not rates: paywall views per flow are not known.",
        },
        "categories": [
            {"category": row["category"], "meaning": CATEGORY_MEANING.get(row["category"], ""), "tickets": row["count"], "share_of_triaged_pct": _share(row["count"], triaged),
             "users": sum(c["users"] for c in groups["cells"] if c["category"] == row["category"])}
            for row in groups["by_category"]
        ],
        "cells": [],
        "flows": [
            {"flow": row["flow"], "title": row["title"], "tickets": row["count"], "share_of_triaged_pct": _share(row["count"], triaged),
             "categories": {c["category"]: c["tickets"] for c in groups["cells"] if c["flow"] == row["flow"]}}
            for row in groups["by_flow"]
        ],
        "ab_tests_seen": {
            "note": "A/B tests the complaining users were in (from the app's records), with complaint tickets per arm; traffic split and views per arm are not known.",
            "tests": groups.get("ab_tests", {}),
        },
        "placements": [
            {
                "placement": key,
                "title": data["title"],
                "flows": [{"title": f["title"], "offer": f.get("offer", "")} for f in data["flows"].values()]
                + [{"title": flow_title(f), "offer": PLACEMENTS_FLOW_OFFER.get(f, ""), "seen_in_test": True} for f in sorted(observed.get(key, set()) - set(data["flows"]))],
                "draftable_now": len(data["flows"]) >= 2,
            }
            for key, data in PLACEMENTS.items()
        ],
    }
    for i, cell in enumerate(groups["cells"][:FACT_CELLS]):
        row = {k: cell[k] for k in ("category", "flow", "title", "placement", "tickets", "users", "products")}
        row["share_of_category_pct"] = cell["share_of_category"]
        if i < EXAMPLE_CELLS:
            row["examples"] = cell["examples"]
        out["cells"].append(row)
    if previous:
        # the test it proposed, not its headline: old counts in the text would be copied as if current
        out["previous"] = {k: previous.get(k) for k in ("hypothesis", "placement", "control_flow", "variant_source", "variant_flow", "created_at")}
        main = (previous.get("cited_cells") or [None])[0]
        if main:
            out["previous"]["main_cell"] = f"{main['category']} × {main['flow']}"
        if isinstance(previous.get("snapshot"), dict):
            out["changes"] = changes(previous["snapshot"], groups)
    return out


PLACEMENTS_FLOW_OFFER = {flow: data.get("offer", "") for p in PLACEMENTS.values() for flow, data in p["flows"].items()}


def _observed_flows(groups: dict) -> dict[str, set[str]]:
    seen: dict[str, set[str]] = defaultdict(set)
    for cell in groups.get("cells", []):
        if cell["flow"] != "unknown" and cell["placement"] in PLACEMENTS:
            seen[cell["placement"]].add(cell["flow"])
    return seen


NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])")


def check_insight(insight: Insight, facts_: dict) -> str | None:
    """Checks pydantic cannot express. None when acceptable; else the reason for the corrective retry."""
    cells = {(c["category"], c["flow"]): c for c in facts_["cells"]}
    for i, cited in enumerate(insight.cited_cells):
        cell = cells.get((cited.category, cited.flow))
        if cell is None:
            return f"cited_cells[{i}]: there is no cell {cited.category} × {cited.flow} in the facts"
        if (cited.tickets, cited.users) != (cell["tickets"], cell["users"]):
            return f"cited_cells[{i}]: {cited.category} × {cited.flow} has tickets {cell['tickets']}, users {cell['users']} in the facts"
    main = insight.cited_cells[0]
    bigger = [c for c in insight.cited_cells[1:] if c.users > main.users]
    if bigger:
        return f"cited_cells[0] is the main cell; {bigger[0].category} × {bigger[0].flow} has more users, so put the cell the test rests on first"
    previous_main = (facts_.get("previous") or {}).get("main_cell")
    if previous_main and facts_.get("changes", {}).get("cells_now_larger_than_it") == "none" and previous_main != f"{main.category} × {main.flow}":
        return f"no cell has outgrown the previous main cell {previous_main}, so the test stays on it (a tie is not a change); keep {previous_main} as cited_cells[0]"
    if previous_main and previous_main == f"{main.category} × {main.flow}" and facts_["previous"].get("variant_source"):
        kept = (facts_["previous"]["placement"], facts_["previous"]["control_flow"], facts_["previous"]["variant_source"], facts_["previous"].get("variant_flow"))
        if (insight.placement, insight.control_flow, insight.variant_source, insight.variant_flow) != kept:
            return f"the main cell is the previous one, so keep its test: placement {kept[0]}, control_flow {kept[1]}, variant_source {kept[2]}, variant_flow {kept[3]}"
    if main.flow == "unknown":
        return "cited_cells[0] is the cell the test is built on; it needs a known flow (unknown is not a paywall)"
    if insight.control_flow != flow_title(main.flow):
        return f"control_flow must be the flow of cited_cells[0]: {flow_title(main.flow)}"
    flows = {f["title"] for p in facts_["placements"] if p["placement"] == insight.placement for f in p["flows"]}
    if insight.control_flow not in flows:
        return f"control_flow must be a flow of {insight.placement}: one of {sorted(flows)}"
    others = sorted(flows - {insight.control_flow})
    if insight.variant_source == "existing_flow" and insight.variant_flow not in others:
        return f"with variant_source existing_flow, variant_flow must be another flow of {insight.placement}: one of {others}" if others else f"{insight.placement} has no other flow; use variant_source edited_copy"
    if insight.variant_source == "edited_copy" and insight.variant_flow is not None:
        return "with variant_source edited_copy, variant_flow must be null"
    if facts_.get("previous") and insight.what_changed.strip().lower().startswith("first analysis"):
        return "what_changed: there is a previous analysis; say whether its test was kept or moved, with the numbers from changes"
    shown = {e["id"] for c in facts_["cells"] for e in c.get("examples", [])}
    unknown_ids = [t for t in insight.ticket_ids if t not in shown]
    if unknown_ids:
        return f"ticket_ids {unknown_ids} are not among the example tickets in the facts"
    allowed = set(NUMBER.findall(json.dumps(facts_, ensure_ascii=False)))
    for where, text in [("headline", insight.headline), *((f"evidence[{i}]", e) for i, e in enumerate(insight.evidence))]:
        made_up = [n for n in NUMBER.findall(text) if n not in allowed]
        if made_up:
            return f"{where} has numbers that are not in the facts ({', '.join(made_up)}); quote counts and shares as given"
    return None


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
