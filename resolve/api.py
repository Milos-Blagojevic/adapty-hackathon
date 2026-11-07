"""The JSON API (docs/agents.md, "JSON API" and "JSON shapes"). Every UI talks to the backend only through this."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from resolve import analytics, config, db, frontline, llm, policy, triage, workflow
from resolve.adapty import access_level
from resolve.models import (
    API_STATUSES,
    DB_TO_API_STATUS,
    SOURCES,
    AgentConfig,
    ChatRequest,
    Decision,
    FeedbackRequest,
    Rejection,
)
from resolve.tickets import CATEGORIES

ACCEPT_STATUSES = ("proposed",)
REJECT_STATUSES = ("new", "triaged", "proposed")
MAX_USERS = 200


def _switch_llm(services, choice: str) -> None:
    """Rebuild the LLM client for both agents. Demo mode never builds one."""
    if services.demo:
        return
    try:
        services.llm = llm.make_llm(choice)
    except (config.MissingKey, ValueError) as exc:
        raise HTTPException(422, f"cannot use {choice}: {exc}") from None


def install(app: FastAPI, services) -> None:
    """CORS for any origin (the APK and other UIs), then the JSON API routes."""
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    app.include_router(build_router(services))
    choice = policy.load_config(services.conn).llm_provider
    if choice != "env" and not services.demo:
        try:
            _switch_llm(services, choice)
        except HTTPException as exc:
            print(f"warning: {exc.detail}; keeping the provider from .env")


def _triage_view(ticket: dict) -> tuple[dict | None, bool]:
    """The Level 1 ticket and whether it was built from a v1 proposal."""
    if ticket["triage"]:
        return ticket["triage"], False
    if ticket["proposal"]:
        return triage.legacy_to_ticket(ticket["proposal"]), True
    return None, False


def ticket_summary(ticket: dict) -> dict:
    view, _ = _triage_view(ticket)
    options = (view or {}).get("options") or []
    return {
        "id": ticket["id"],
        "status": DB_TO_API_STATUS[ticket["status"]],
        "source": ticket["source"],
        "category": ticket["category"],
        "customer_user_id": ticket["customer_user_id"],
        "email": ticket["email"],
        "subject": ticket["subject"],
        "created_at": ticket["created_at"],
        "updated_at": ticket["updated_at"],
        "confidence": view.get("confidence") if view else None,
        "top_action": options[0]["action"] if options else None,
    }


def ticket_full(conn: db.Database, ticket: dict) -> dict:
    view, legacy = _triage_view(ticket)
    conversation = db.list_messages(conn, ticket["session_id"]) if ticket["session_id"] else None
    return {
        **ticket_summary(ticket),
        "body": ticket["body"],
        "session_id": ticket["session_id"],
        "triage": view,
        "legacy": legacy,
        "profile": ticket["profile"],
        "decision": ticket["decision"],
        "action": ticket["action"],
        "action_error": ticket["action_error"],
        "triage_error": ticket["triage_error"],
        "conversation": conversation,
    }


def build_router(services) -> APIRouter:
    router = APIRouter()
    conn = services.conn

    def ticket_or_404(ticket_id: str) -> dict:
        ticket = db.get_ticket(conn, ticket_id)
        if ticket is None:
            raise HTTPException(404, f"no ticket {ticket_id}")
        return ticket

    @router.post("/chat")
    def chat(body: ChatRequest, background: BackgroundTasks):
        before = db.get_conversation(conn, body.session_id)
        before_ticket = before["ticket_id"] if before else None
        result = frontline.chat(services, body.customer_user_id, body.session_id, body.message)
        if result.handed_off and result.ticket_id and result.ticket_id != before_ticket:
            background.add_task(triage.run, services, result.ticket_id)
        return result.model_dump()

    @router.get("/api/chat/{session_id}")
    def chat_history(session_id: str):
        conversation = db.get_conversation(conn, session_id)
        if conversation is None:
            raise HTTPException(404, f"no session {session_id}")
        events = [
            {k: e[k] for k in ("ts", "tool", "args", "allowed", "blocked_reason", "result")}
            for e in db.list_events(conn, session_id)
        ]
        return {
            "session_id": session_id,
            "customer_user_id": conversation["customer_user_id"],
            "outcome": conversation["outcome"],
            "ticket_id": conversation["ticket_id"],
            "messages": db.list_messages(conn, session_id),
            "events": events,
        }

    @router.post("/feedback")
    def feedback(body: FeedbackRequest, background: BackgroundTasks):
        ticket_id = db.insert_feedback(
            conn, body.text, source=body.source, customer_user_id=body.customer_user_id, email=body.email, subject=body.subject
        )
        background.add_task(triage.run, services, ticket_id)
        return {"ticket_id": ticket_id}

    @router.get("/api/tickets")
    def tickets(
        status: Literal[*API_STATUSES] | None = None,
        source: Literal[*SOURCES] | None = None,
        category: Literal[*CATEGORIES] | None = None,
    ):
        rows = db.list_tickets(conn)
        return [
            ticket_summary(t)
            for t in rows
            if (status is None or DB_TO_API_STATUS[t["status"]] == status)
            and (source is None or t["source"] == source)
            and (category is None or t["category"] == category)
        ]

    @router.get("/api/tickets/{ticket_id}")
    def ticket(ticket_id: str):
        return ticket_full(conn, ticket_or_404(ticket_id))

    @router.post("/api/tickets/{ticket_id}/triage")
    def run_triage(ticket_id: str, force: bool = False):
        ticket_or_404(ticket_id)
        triage.run(services, ticket_id, force=force)
        return ticket_full(conn, ticket_or_404(ticket_id))

    @router.post("/api/tickets/{ticket_id}/accept")
    def accept(ticket_id: str, body: Decision):
        if ticket_or_404(ticket_id)["status"] not in ACCEPT_STATUSES:
            raise HTTPException(409, "only a proposed ticket can be accepted")
        try:
            workflow.accept(services, ticket_id, body)
        except workflow.NotFound:
            raise HTTPException(404, f"no ticket {ticket_id}") from None
        return ticket_full(conn, ticket_or_404(ticket_id))

    @router.post("/api/tickets/{ticket_id}/reject")
    def reject(ticket_id: str, body: Rejection):
        if ticket_or_404(ticket_id)["status"] not in REJECT_STATUSES:
            raise HTTPException(409, "only an open ticket can be rejected")
        try:
            workflow.reject(services, ticket_id, body)
        except workflow.NotFound:
            raise HTTPException(404, f"no ticket {ticket_id}") from None
        return ticket_full(conn, ticket_or_404(ticket_id))

    @router.get("/api/config")
    def get_config():
        return policy.load_config(conn).model_dump()

    @router.put("/api/config")
    def put_config(body: AgentConfig):
        if body.llm_provider != policy.load_config(conn).llm_provider:
            _switch_llm(services, body.llm_provider)  # 422 when that provider has no keys; nothing saved
        db.save_config(conn, body.model_dump())
        return body.model_dump()

    @router.get("/api/llm")
    def get_llm():
        active = services.llm
        return {
            "selected": policy.load_config(conn).llm_provider,
            "env_default": config.LLM_PROVIDER,
            "active": getattr(active, "provider", None),
            # AnthropicLLM resolves its model lazily over the network; never trigger that from here.
            "model": None if active is None else (config.ANTHROPIC_MODEL or "newest Opus") if active.provider == "anthropic" else active.model,
            "available": llm.available_providers(),
            "demo": services.demo,
        }

    @router.get("/api/analytics")
    def get_analytics():
        return analytics.compute(conn)

    @router.get("/api/users")
    def users():
        ids = {r[0] for r in conn.execute("SELECT customer_user_id FROM tickets WHERE customer_user_id != ''")}
        ids |= {r[0] for r in conn.execute("SELECT customer_user_id FROM conversations WHERE customer_user_id != ''")}
        profiles = {}  # latest cached profile per user (later rows overwrite earlier ones)
        for r in conn.execute(
            "SELECT customer_user_id, profile_json FROM tickets WHERE profile_json IS NOT NULL ORDER BY profile_fetched_at, updated_at"
        ):
            profiles[r[0]] = json.loads(r[1])
        now = datetime.now(timezone.utc)
        out = []
        for uid in sorted(ids)[:MAX_USERS]:
            profile = profiles.get(uid)
            if profile is None:
                out.append({"customer_user_id": uid, "badge": "unknown", "product": None})
                continue
            level = access_level(profile, config.ACCESS_LEVEL)
            out.append(
                {
                    "customer_user_id": uid,
                    "badge": triage.level_badge(level, now),
                    "product": level.get("store_product_id") if level else None,
                }
            )
        return out

    return router
