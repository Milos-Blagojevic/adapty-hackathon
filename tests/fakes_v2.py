"""v2 test helpers: scripted LLM steps, Level 1 tickets, paid / unpaid / lifetime profiles, services.

Written from docs/agents.md only (the contract), never from an implementation. No network, no keys.
"""

from __future__ import annotations

import itertools
import json

from resolve import db
from resolve.adapty import Client
from resolve.llm import FakeLLM, Step, ToolCall
from resolve.models import TriageTicket
from tests.fakes import ACTIVE_WEEKLY, LIFETIME, PROFILE_URL, GRANT_URL, profile, ticket_row, tickets_file  # noqa: F401

CUID = "lumen_443814_00549"
OTHER = "lumen_443814_00042"
STRANGER = "lumen_443814_99999"

EPOCH = "2000-01-01T00:00:00Z"  # `since` that returns every grant

# --- scripted steps ---------------------------------------------------------------------------------

_ids = itertools.count(1)


def call(name: str, **args) -> Step:
    """One model turn that calls one tool."""
    return Step(text=None, tool_calls=[ToolCall(id=f"call_{next(_ids)}", name=name, args=args)], stop="tool")


def calls(*pairs: tuple[str, dict]) -> Step:
    """One model turn that calls several tools at once."""
    return Step(text=None, tool_calls=[ToolCall(id=f"call_{next(_ids)}", name=n, args=a) for n, a in pairs], stop="tool")


def say(text: str) -> Step:
    """A final model turn: text, no tools."""
    return Step(text=text, stop="end")


def submit(ticket: TriageTicket | dict) -> Step:
    args = ticket.model_dump(mode="json") if isinstance(ticket, TriageTicket) else ticket
    return call("submit_ticket", **args)


def tool_results(fake: FakeLLM, name: str | None = None) -> list[dict]:
    """Every tool message the model was shown, across all calls, deduplicated by tool_call_id."""
    seen, out = set(), []
    for c in fake.calls:
        for m in c["messages"]:
            if m.get("role") == "tool" and m.get("tool_call_id") not in seen and (name is None or m.get("name") == name):
                seen.add(m.get("tool_call_id"))
                out.append(m)
    return out


class RoutingLLM:
    """Two scripted fakes behind one client: calls offering `submit_ticket` go to `triage`, the rest to `chat`.

    Lets one app serve both agents (Level 2 chat and the background Level 1 triage) deterministically.
    """

    provider = "fake"
    model = "fake"

    def __init__(self, chat: FakeLLM | None = None, triage: FakeLLM | None = None):
        self.chat = chat or FakeLLM([])
        self.triage = triage or FakeLLM([])

    def step(self, system, messages, tools):
        names = {t.name for t in tools}
        target = self.triage if ("submit_ticket" in names or "submit_insight" in names) else self.chat
        return target.step(system, messages, tools)


# --- Level 1 tickets --------------------------------------------------------------------------------


def option(action="grant_access", days=None, reply="Sorry about that.", risk="low", **params) -> dict:
    p = {"days": days, "kind": None, "store": None, "question": None}
    p.update(params)
    return {"action": action, "params": p, "reply": reply, "risk": risk, "risk_reason": "small, reversible"}


def triage_dict(**overrides) -> dict:
    """Raw `submit_ticket` arguments (may be made invalid on purpose)."""
    base = {
        "customer_user_id": CUID,
        "lookup": "id from the ticket",
        "summary": "Charged after the trial and wants access back.",
        "category": "charged_after_trial",
        "evidence": ["subscriptions[0].store_product_id: lumen_weekly"],
        "root_cause": "The weekly trial converted and the user missed the date.",
        "confidence": 0.85,
        "options": SAFE_OPTIONS,
    }
    base.update(overrides)
    return base


# Valid without a profile (binding rules: no grant_access before get_profile).
SAFE_OPTIONS = [
    option("reply_only", reply="Here is how the trial works and when it renews."),
    option("store_instructions", reply="Here is how to cancel in Google Play.", kind="cancel", store="play_store"),
]
# grant_access first needs get_profile in the run and a safe second option.
GRANT_OPTIONS = [
    option("grant_access", days=3, reply="I added 3 days of premium."),
    option("reply_only", reply="Here is how the trial works.", risk="low"),
]
# Unidentified user (customer_user_id null): one option must be ask_user with a question.
ASK_OPTIONS = [
    option("ask_user", reply="Could you tell me the email you used to buy?", question="Which email did you buy with?"),
    option("reply_only", reply="Here is how to restore purchases."),
]


def triage_ticket(**overrides) -> TriageTicket:
    """Default: identified user, reply_only + store_instructions (valid with no tool calls)."""
    return TriageTicket.model_validate(triage_dict(**overrides))


def grant_dict(**overrides) -> dict:
    return triage_dict(**{"options": GRANT_OPTIONS, **overrides})


def grant_ticket(**overrides) -> TriageTicket:
    """grant_access 3 days first, reply_only second: valid only after get_profile on a non-lifetime user."""
    return TriageTicket.model_validate(grant_dict(**overrides))


def unidentified_ticket(**overrides) -> TriageTicket:
    return TriageTicket.model_validate(triage_dict(**{"customer_user_id": None, "lookup": "email not in index", "options": ASK_OPTIONS, **overrides}))


# --- profiles ---------------------------------------------------------------------------------------

FAR_EXPIRY = "2030-01-01T00:00:00+00:00"
ACTIVE_FAR = dict(ACTIVE_WEEKLY, expires_at=FAR_EXPIRY)
EXPIRED = dict(ACTIVE_WEEKLY, expires_at="2026-10-01T00:00:00+00:00")
SUB_WEEKLY = {
    "store": "play_store",
    "store_product_id": "lumen_weekly",
    "vendor_transaction_id": "GPA.1234",
    "vendor_original_transaction_id": "GPA.1234",
    "purchased_at": "2026-10-30T05:08:30+00:00",
    "originally_purchased_at": "2026-10-27T05:08:30+00:00",
    "expires_at": FAR_EXPIRY,
    "is_active": True,
    "is_sandbox": False,
    "is_refund": False,
    "will_renew": True,
    "offer": None,
}
LIFETIME_PURCHASE = {
    "store": "app_store",
    "store_product_id": "lumen.lifetime",
    "vendor_transaction_id": "2000001",
    "purchased_at": "2026-09-01T10:00:00+00:00",
    "is_sandbox": False,
    "is_refund": False,
    "is_consumable": False,
}


def paid_profile(customer=CUID, access_levels=(ACTIVE_FAR,)) -> dict:
    """Has paid (revenue > 0, a store subscription) with premium until 2030."""
    p = profile(customer, access_levels=access_levels, subscriptions=[SUB_WEEKLY])
    p["total_revenue_usd"] = 15.59
    return p


def unpaid_profile(customer=CUID) -> dict:
    """Never paid: no revenue, no purchases, no access."""
    p = profile(customer)
    p["total_revenue_usd"] = 0
    return p


def lifetime_profile(customer=CUID) -> dict:
    p = profile(customer, access_levels=[LIFETIME])
    p["non_subscriptions"] = [LIFETIME_PURCHASE]
    p["total_revenue_usd"] = 49.99
    return p


def mock_profile(http, prof: dict) -> None:
    http.get(PROFILE_URL, json={"data": prof})


def mock_grant_ok(http, prof: dict | None = None) -> None:
    after = prof or paid_profile()
    http.post(GRANT_URL, json={"data": after})
    http.patch(PROFILE_URL, json={"data": after})


def http_calls(http, method: str) -> list:
    return [c for c in http.calls if c.request.method == method]


def customer_headers(http) -> set[str]:
    return {c.request.headers.get("adapty-customer-user-id") for c in http.calls}


def body(c) -> dict:
    return json.loads(c.request.body)


# --- services and DB --------------------------------------------------------------------------------


def services(conn, llm=None, demo=False, adapty="default"):
    from resolve.workflow import Services

    client = Client("sk") if adapty == "default" else adapty
    return Services(conn=conn, demo=demo, adapty=client, llm=llm)


def new_db(tmp_path, seed=True, name="t.sqlite"):
    conn = db.connect(tmp_path / name)
    if seed:
        db.seed(conn, tickets_file(tmp_path, [ticket_row(), ticket_row(id="t-002", customer=OTHER, rule="cancel_how")]))
    return conn


def make_proposed(conn, ticket_id="t-001", ticket: TriageTicket | None = None, prof: dict | None = None) -> TriageTicket:
    """Put a ticket in `proposed` with a stored Level 1 ticket, as triage would (default: grant_access 3 days first)."""
    tt = ticket or grant_ticket()
    fields = dict(triage_json=tt.model_dump_json(), triage_at=db.now_iso(), category=tt.category, status="proposed")
    if prof is not None:
        fields["profile_json"] = json.dumps(prof)
    db.update_ticket(conn, ticket_id, **fields)
    return tt


def all_grants(conn, customer=CUID) -> list[dict]:
    return db.grants_since(conn, customer, EPOCH)


def set_config(conn, **changes) -> None:
    """Store an AgentConfig with nested changes, e.g. set_config(conn, grant={"max_days": 3}, max_turns=2)."""
    from resolve.models import AgentConfig

    cfg = AgentConfig().model_dump(mode="json")
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(cfg.get(key), dict):
            cfg[key] = {**cfg[key], **value}
        else:
            cfg[key] = value
    db.save_config(conn, cfg)
