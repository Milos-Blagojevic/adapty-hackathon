"""The ticket state machine: open (profile), propose (Level 2), approve (the Adapty write), dismiss.

Statuses: new → triaged (profile fetched) → proposed (proposal cached) → approved (owner clicked;
the write is in flight) → done (write succeeded, reply marked sent), plus dismissed.

Approve is idempotent: the proposed → approved transition is a single conditional UPDATE, so a
second click, a double submit or a stale tab finds the ticket no longer `proposed` and does
nothing. An Adapty failure records the error and returns the ticket to `proposed`; nothing is
marked done unless Adapty said yes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

import anthropic

from resolve import agent, config, db
from resolve.adapty import AdaptyError, Client, compensation_expiry, format_datetime

STAMP_KEY = "last_support_ticket"
OPEN_STATUSES = ("new", "triaged", "proposed")


@dataclass
class Services:
    conn: db.Database
    demo: bool
    adapty: Client | None = None
    anthropic: anthropic.Anthropic | None = None
    model: str | None = None


class NotFound(Exception):
    pass


def _ticket(services: Services, ticket_id: str) -> dict:
    ticket = db.get_ticket(services.conn, ticket_id)
    if ticket is None:
        raise NotFound(ticket_id)
    return ticket


def open_ticket(services: Services, ticket_id: str, refresh: bool = True) -> dict:
    """Fetch the live profile (live mode) and move a new ticket to triaged. Never raises on Adapty errors."""
    ticket = _ticket(services, ticket_id)
    if services.demo or not refresh or services.adapty is None:
        return ticket
    try:
        profile = services.adapty.get_profile(ticket["customer_user_id"])
        fields = {"profile_json": json.dumps(profile), "profile_fetched_at": db.now_iso(), "profile_error": None}
    except AdaptyError as exc:
        fields = {"profile_error": str(exc)}
    if ticket["status"] == "new" and "profile_json" in fields:
        fields["status"] = "triaged"
    db.update_ticket(services.conn, ticket_id, **fields)
    return _ticket(services, ticket_id)


def propose(services: Services, ticket_id: str, force: bool = False) -> dict:
    """Run Level 2 unless a proposal is cached. Stores the proposal or the error; never raises AgentError."""
    ticket = _ticket(services, ticket_id)
    if ticket["proposal"] and not force:
        return ticket
    if services.demo:
        db.update_ticket(services.conn, ticket_id, proposal_error="Demo mode: proposals come from the snapshot; nothing to run")
        return _ticket(services, ticket_id)
    if ticket["status"] not in OPEN_STATUSES:
        return ticket
    if not ticket["profile"]:
        db.update_ticket(services.conn, ticket_id, proposal_error="No profile yet; refresh the profile first")
        return _ticket(services, ticket_id)
    if services.anthropic is None or not services.model:
        db.update_ticket(services.conn, ticket_id, proposal_error="ANTHROPIC_API_KEY is not set")
        return _ticket(services, ticket_id)
    reserve = lambda: db.reserve_model_call(services.conn, ticket_id, agent.MAX_CALLS)  # noqa: E731
    try:
        proposal, _ = agent.propose(services.anthropic, services.model, ticket, ticket["profile"], reserve)
    except agent.AgentError as exc:
        db.update_ticket(services.conn, ticket_id, only_status=OPEN_STATUSES, proposal_error=str(exc))
        return _ticket(services, ticket_id)
    # Only while the ticket is still open: a slow run must not revive a ticket the owner approved
    # or dismissed in the meantime (that would put a second Approve button on a granted ticket).
    db.update_ticket(
        services.conn,
        ticket_id,
        only_status=OPEN_STATUSES,
        proposal_json=proposal.model_dump_json(),
        proposal_at=db.now_iso(),
        proposal_error=None,
        category=proposal.category,
        status="proposed",
    )
    return _ticket(services, ticket_id)



def approve(services: Services, ticket_id: str) -> dict:
    """Execute the proposed action. Only a `proposed` ticket can be approved, exactly once."""
    ticket = _ticket(services, ticket_id)
    if not db.set_status_if(services.conn, ticket_id, "proposed", "approved"):
        return ticket
    proposal = ticket["proposal"]
    now = datetime.now(timezone.utc)
    action: dict = {"type": proposal["action"], "approved_at": format_datetime(now)}
    if services.demo or services.adapty is None:
        action["demo"] = True
        action["note"] = "Demo mode: no Adapty call was made; only the local ticket changed."
        _finish(services, ticket_id, action, proposal)
        return _ticket(services, ticket_id)
    customer = ticket["customer_user_id"]
    try:
        if proposal["action"] == "grant_access":
            fresh = services.adapty.get_profile(customer)
            try:
                expires_at = compensation_expiry(fresh, config.ACCESS_LEVEL, config.COMPENSATION_DAYS, now)
            except ValueError as exc:
                raise AdaptyError(str(exc), code="lifetime") from None
            granted = services.adapty.grant_access_level(customer, config.ACCESS_LEVEL, expires_at)
            action.update(
                access_level=config.ACCESS_LEVEL,
                days=config.COMPENSATION_DAYS,
                expires_at=format_datetime(expires_at),
                profile_id=granted.get("profile_id"),
            )
            profile = granted
        else:
            profile = None
        try:
            profile = services.adapty.update_profile(customer, {STAMP_KEY: ticket_id})
            action["stamped"] = {STAMP_KEY: ticket_id}
        except AdaptyError as exc:
            action["stamp_error"] = str(exc)
    except AdaptyError as exc:
        db.set_status_if(services.conn, ticket_id, "approved", "proposed")
        db.update_ticket(services.conn, ticket_id, action_error=str(exc))
        return _ticket(services, ticket_id)
    except Exception:
        # Anything unexpected before the grant is recorded: never leave the ticket stuck in `approved`.
        db.set_status_if(services.conn, ticket_id, "approved", "proposed")
        raise
    _finish(services, ticket_id, action, proposal, profile)
    return _ticket(services, ticket_id)


def _finish(services: Services, ticket_id: str, action: dict, proposal: dict, profile: dict | None = None) -> None:
    fields = {
        "action_json": json.dumps(action),
        "action_at": db.now_iso(),
        "action_error": None,
        "status": "done",
    }
    if proposal["action"] != "no_action":
        fields["reply_sent_at"] = db.now_iso()
    if profile is not None:
        fields["profile_json"] = json.dumps(profile)
        fields["profile_fetched_at"] = db.now_iso()
    db.update_ticket(services.conn, ticket_id, **fields)


def dismiss(services: Services, ticket_id: str) -> dict:
    ticket = _ticket(services, ticket_id)
    if ticket["status"] in ("done", "dismissed"):
        return ticket
    db.update_ticket(services.conn, ticket_id, status="dismissed")
    return _ticket(services, ticket_id)
