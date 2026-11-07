"""Shared contract models: agent config, the Level 1 ticket, decisions, Level 2 chat results.

Every local copy keeps these shapes (see docs/agents.md). Tool-argument models used with `llm.ToolSpec`
must stay strict-compatible: no free-form dicts; Optional fields are sent as required + nullable.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from resolve.tickets import CATEGORIES

# Level 1 option actions (what the support person can accept or switch to).
ACTIONS = ("grant_access", "reply_only", "store_instructions", "refund_to_human", "ask_user", "no_reply")
StoreKind = Literal["restore", "cancel", "refund_request"]
Store = Literal["app_store", "play_store"]

# API statuses. The DB also has `triaged` (shown as new) and `approved` (action in flight, shown as proposed).
API_STATUSES = ("new", "proposed", "done", "dismissed")
DB_TO_API_STATUS = {"new": "new", "triaged": "new", "proposed": "proposed", "approved": "proposed", "done": "done", "dismissed": "dismissed"}

SOURCES = ("chat_handoff", "email", "form")
OUTCOMES = ("open", "resolved", "handed_off")
HANDOFF_RULES = ("refund", "chargeback", "threat", "asks_for_human", "profile_mismatch", "repeat_after_grant")


# --- Agent settings (Level 2 config) ---------------------------------------------------------------


class GrantLimits(BaseModel):
    access_level: str = "premium"
    max_days: int = Field(7, ge=1, le=365)
    max_grants_per_user_30d: int = Field(1, ge=0, le=10)
    require_paid: bool = True


class AgentConfig(BaseModel):
    app_name: str = "Lumen"
    tone: str = "warm, short, plain English"
    tools: dict[str, bool] = Field(default_factory=lambda: {"grant_access": True})
    grant: GrantLimits = Field(default_factory=GrantLimits)
    always_handoff: list[Literal[*HANDOFF_RULES]] = Field(default_factory=lambda: list(HANDOFF_RULES))
    max_turns: int = Field(8, ge=1, le=50)
    # Which LLM runs both agents: env = LLM_PROVIDER from .env (auto picks the one with keys).
    llm_provider: Literal["env", "openai", "anthropic"] = "env"


# --- Level 1 ticket ------------------------------------------------------------------------------


class OptionParams(BaseModel):
    days: int | None = Field(None, description="grant_access only: days of the access level")
    kind: StoreKind | None = Field(None, description="store_instructions only")
    store: Store | None = Field(None, description="store_instructions / refund_to_human: which store")
    question: str | None = Field(None, description="ask_user only: the question to the user")


class TriageOption(BaseModel):
    action: Literal[*ACTIONS]
    params: OptionParams
    reply: str = Field(description="Draft reply to the user, ready to send, under 120 words. Empty for no_reply.")
    risk: Literal["low", "medium", "high"]
    risk_reason: str = Field(description="One line: why this risk level (money, abuse, wrong diagnosis).")


class TriageTicket(BaseModel):
    """The arguments of the `submit_ticket` tool: what Level 1 hands to the support person."""

    customer_user_id: str | None = Field(description="The Adapty customer user id, or null when not found")
    lookup: str = Field(description="How the user was identified or why not (e.g. 'id from chat', 'email not in index')")
    summary: str = Field(description="One sentence: what the user wants")
    category: Literal[*CATEGORIES]
    evidence: list[str] = Field(min_length=1, description="Facts used, each 'field: value' from the profile or history")
    root_cause: str = Field(description="One sentence hypothesis of why this happened")
    confidence: float = Field(ge=0, le=1)
    options: list[TriageOption] = Field(min_length=2, max_length=3, description="Ranked, best first")


class OptionEdits(BaseModel):
    """What the support person may change before accepting. Any field left None keeps the option's value."""

    action: Literal[*ACTIONS] | None = None
    reply: str | None = None
    days: int | None = Field(None, ge=1, le=365)
    kind: StoreKind | None = None
    store: Store | None = None
    question: str | None = None


class Decision(BaseModel):
    option: int = Field(ge=0, le=2)
    edits: OptionEdits = Field(default_factory=OptionEdits)


class Rejection(BaseModel):
    note: str = ""
    reply: str | None = None  # a manual reply → ticket done; none → dismissed


# --- Level 2 chat --------------------------------------------------------------------------------


class ChatRequest(BaseModel):
    customer_user_id: str
    session_id: str
    message: str


class ActionLog(BaseModel):
    tool: str
    args: dict
    allowed: bool
    blocked_reason: str | None = None
    result: dict | str | None = None


class ChatResult(BaseModel):
    reply: str
    actions: list[ActionLog] = Field(default_factory=list)
    handed_off: bool = False
    ticket_id: str | None = None


class FeedbackRequest(BaseModel):
    text: str
    source: Literal[*SOURCES] = "form"
    customer_user_id: str | None = None
    email: str | None = None
    subject: str | None = None
