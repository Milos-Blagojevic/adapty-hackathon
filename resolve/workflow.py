"""The ticket state machine: open (profile), propose (Level 1 triage), accept / reject (the person's call), dismiss.

Statuses: new → triaged (profile fetched) → proposed (triage ticket cached) → approved (the person
accepted an option; the action is in flight) → done (action succeeded, reply marked sent), plus
dismissed (rejected with nothing to send).

Accept is idempotent: the proposed → approved transition is a single conditional UPDATE, so a
second click, a double submit or a stale tab finds the ticket no longer `proposed` and does
nothing. An Adapty failure records the error and returns the ticket to `proposed`; nothing is
marked done unless Adapty said yes. The only Adapty writes go through `actions.execute`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from resolve import actions, db, policy, triage
from resolve.adapty import AdaptyError, Client
from resolve.llm import LLM
from resolve.models import Decision, Rejection

OPEN_STATUSES = ("new", "triaged", "proposed")


@dataclass
class Services:
    conn: db.Database
    demo: bool
    adapty: Client | None = None
    llm: LLM | None = None


class NotFound(Exception):
    pass


def _ticket(services: Services, ticket_id: str) -> dict:
    ticket = db.get_ticket(services.conn, ticket_id)
    if ticket is None:
        raise NotFound(ticket_id)
    return ticket


def triage_of(ticket: dict) -> dict | None:
    """The ticket's triage (v2), or its v1 proposal as a one-option legacy ticket, or None."""
    if ticket.get("triage"):
        return ticket["triage"]
    if ticket.get("proposal"):
        return triage.legacy_to_ticket(ticket["proposal"])
    return None


def open_ticket(services: Services, ticket_id: str, refresh: bool = True) -> dict:
    """Fetch the live profile (live mode) and move a new ticket to triaged. Never raises on Adapty errors."""
    ticket = _ticket(services, ticket_id)
    if services.demo or not refresh or services.adapty is None or not ticket["customer_user_id"]:
        return ticket
    try:
        profile = services.adapty.get_profile(ticket["customer_user_id"])
        db.update_ticket(services.conn, ticket_id, profile_json=json.dumps(profile), profile_fetched_at=db.now_iso(), profile_error=None)
        db.set_status_if(services.conn, ticket_id, "new", "triaged")
    except AdaptyError as exc:
        db.update_ticket(services.conn, ticket_id, profile_error=str(exc))
    return _ticket(services, ticket_id)


def propose(services: Services, ticket_id: str, force: bool = False) -> dict:
    """Run Level 1 unless a ticket is cached. Stores the triage or `triage_error`; never raises on model errors."""
    _ticket(services, ticket_id)
    return triage.run(services, ticket_id, force=force)


def final_option(services: Services, option: dict, decision: Decision) -> dict:
    """The option with the person's edits applied; grant days default to the option's, else the config max."""
    edits = decision.edits
    params = dict(option["params"])
    for key in ("days", "kind", "store", "question"):
        if getattr(edits, key) is not None:
            params[key] = getattr(edits, key)
    final = {
        "action": edits.action or option["action"],
        "params": params,
        "reply": option["reply"] if edits.reply is None else edits.reply,
    }
    if final["action"] == "grant_access":
        days = params.get("days") or policy.load_config(services.conn).grant.max_days
        if not 1 <= days <= 365:
            raise ValueError(f"days must be between 1 and 365, got {days}")
        params["days"] = days
    return final


def accept(services: Services, ticket_id: str, decision: Decision) -> dict:
    """Run one option (with edits). Only a `proposed` ticket can be accepted, exactly once.

    Raises ValueError when the option index is out of range or the edits make no sense.
    """
    ticket = _ticket(services, ticket_id)
    if ticket["status"] != "proposed":
        return ticket
    proposed = triage_of(ticket)
    if proposed is None:
        raise ValueError("the ticket has no options to accept")
    if decision.option >= len(proposed["options"]):
        raise ValueError(f"option {decision.option} does not exist; the ticket has {len(proposed['options'])}")
    final = final_option(services, proposed["options"][decision.option], decision)
    customer = ticket["customer_user_id"]
    if final["action"] == "grant_access" and not customer:
        raise ValueError("grant_access needs an identified customer")
    if not db.set_status_if(services.conn, ticket_id, "proposed", "approved"):
        return _ticket(services, ticket_id)
    try:
        result = actions.execute(services, customer, final["action"], final["params"], source="l1", ref=ticket_id)
    except AdaptyError as exc:
        db.set_status_if(services.conn, ticket_id, "approved", "proposed")
        db.update_ticket(services.conn, ticket_id, action_error=str(exc))
        return _ticket(services, ticket_id)
    except Exception:
        # Unexpected failure before any grant: never leave the ticket stuck in `approved`.
        db.set_status_if(services.conn, ticket_id, "approved", "proposed")
        raise
    # From here on the action happened: the ticket is done and the action recorded, so a second
    # Accept is impossible.
    action = dict(result, option=decision.option, final=final, approved_at=db.now_iso())
    if action.get("demo"):
        action["note"] = "Demo mode: no Adapty call was made; only the local ticket changed."
    fields = {
        "action_json": json.dumps(action),
        "action_at": db.now_iso(),
        "action_error": None,
        "decision_json": json.dumps(decision.model_dump()),
        "status": "done",
    }
    if final["action"] != "no_reply" and final["reply"].strip():
        fields["reply_sent_at"] = db.now_iso()
        _post_reply(services, ticket, final["reply"])
    db.update_ticket(services.conn, ticket_id, **fields)
    return _ticket(services, ticket_id)


def approve(services: Services, ticket_id: str) -> dict:
    """The old pages' Approve: accept the best option as is."""
    return accept(services, ticket_id, Decision(option=0))


def reject(services: Services, ticket_id: str, rejection: Rejection) -> dict:
    """Reject all options: with a reply → done (reply marked sent), without → dismissed. No Adapty call.

    Allowed while the ticket is open (new, triaged, proposed), so a manual reply works when triage
    failed; never while an accept is in flight, and never after done or dismissed.
    """
    ticket = _ticket(services, ticket_id)
    reply = (rejection.reply or "").strip()
    fields = {"decision_json": json.dumps(dict(rejection.model_dump(), rejected=True)), "status": "done" if reply else "dismissed"}
    if reply:
        fields["reply_sent_at"] = db.now_iso()
    if db.update_ticket(services.conn, ticket_id, only_status=OPEN_STATUSES, **fields) and reply:
        _post_reply(services, ticket, reply)
    return _ticket(services, ticket_id)


def _post_reply(services: Services, ticket: dict, reply: str) -> None:
    """A ticket from a chat handoff: the reply shows up in that chat. Nothing is sent anywhere else."""
    if ticket.get("session_id"):
        db.append_message(services.conn, ticket["session_id"], "support", reply)


def dismiss(services: Services, ticket_id: str) -> dict:
    """Dismiss an open ticket. Not while an accept is in flight, and never after done or dismissed."""
    _ticket(services, ticket_id)
    db.update_ticket(services.conn, ticket_id, only_status=OPEN_STATUSES, status="dismissed")
    return _ticket(services, ticket_id)
