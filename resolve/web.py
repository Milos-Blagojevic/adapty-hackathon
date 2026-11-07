"""FastAPI + Jinja + htmx: the inbox, the ticket page (Level 2) and the insights page (Level 1)."""

from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from resolve import agent, config, db, insights, workflow
from resolve.adapty import Client, parse_datetime
from resolve.tickets import ACTIONS, CATEGORIES, STATUSES
from resolve.workflow import Services

HERE = Path(__file__).resolve().parent

ACTION_LABELS = {
    "grant_access": f"Grant {config.COMPENSATION_DAYS} days of {config.ACCESS_LEVEL}",
    "explain": "Reply only",
    "escalate_refund": "Reply and flag for a store refund",
    "no_action": "No action",
}
CATEGORY_LABELS = {
    "charged_after_trial": "Charged after trial",
    "no_access_after_purchase": "No access after purchase",
    "billing_issue": "Billing issue",
    "refund_request": "Refund request",
    "cancel_how": "How to cancel",
    "price_complaint": "Price complaint",
    "other": "Other",
}


def age(value: str | None, now: datetime | None = None) -> str:
    if not value:
        return ""
    delta = (now or datetime.now(timezone.utc)) - parse_datetime(value)
    seconds = int(delta.total_seconds())
    if seconds < 3600:
        return f"{max(seconds // 60, 1)}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def fmt_date(value: str | None) -> str:
    if not value:
        return "—"
    return parse_datetime(value).strftime("%b %-d, %Y %H:%M")


def fmt_day(value: str | None) -> str:
    if not value:
        return "—"
    return parse_datetime(value).strftime("%b %-d, %Y")


def create_app(
    db_path: Path,
    demo: bool,
    adapty_client: Client | None = None,
    anthropic_client: anthropic.Anthropic | None = None,
    model: str | None = None,
    seed_file: Path = config.ROOT / "data" / "tickets.json",
    ab_runner: insights.Runner | None = None,
) -> FastAPI:
    conn = db.connect(db_path)
    if seed_file.exists():
        db.seed(conn, seed_file)
    services = Services(conn=conn, demo=demo, adapty=adapty_client, anthropic=anthropic_client, model=model)

    app = FastAPI(title="Resolve")
    app.state.services = services
    app.state.ab_runner = ab_runner
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.filters["age"] = age
    templates.env.filters["fmt_date"] = fmt_date
    templates.env.filters["fmt_day"] = fmt_day
    templates.env.globals.update(
        demo=demo,
        statuses=STATUSES,
        categories=CATEGORIES,
        actions=ACTIONS,
        action_labels=ACTION_LABELS,
        category_labels=CATEGORY_LABELS,
        access_level=config.ACCESS_LEVEL,
        flow_titles=insights.FLOW_TITLES,
        compensation_days=config.COMPENSATION_DAYS,
        max_calls=agent.MAX_CALLS,
        now=lambda: datetime.now(timezone.utc),
    )

    def render(name: str, request: Request, **context) -> HTMLResponse:
        return templates.TemplateResponse(request, name, context)

    def ticket_or_404(ticket_id: str) -> dict:
        ticket = db.get_ticket(conn, ticket_id)
        if ticket is None:
            raise HTTPException(404, f"no ticket {ticket_id}")
        return ticket

    @app.get("/", response_class=HTMLResponse)
    def inbox(request: Request, status: str | None = None):
        if status and status not in STATUSES:
            raise HTTPException(400, "unknown status")
        return render(
            "inbox.html",
            request,
            tickets=db.list_tickets(conn, status),
            counts=db.status_counts(conn),
            total=sum(db.status_counts(conn).values()),
            current=status,
        )

    @app.get("/tickets/{ticket_id}", response_class=HTMLResponse)
    def ticket_page(request: Request, ticket_id: str):
        ticket_or_404(ticket_id)
        ticket = workflow.open_ticket(services, ticket_id)
        return render("ticket.html", request, ticket=ticket, summary=_summary(ticket))

    @app.post("/tickets/{ticket_id}/profile", response_class=HTMLResponse)
    def refresh_profile(request: Request, ticket_id: str):
        ticket_or_404(ticket_id)
        ticket = workflow.open_ticket(services, ticket_id, refresh=True)
        return render("_profile.html", request, ticket=ticket, summary=_summary(ticket))

    @app.post("/tickets/{ticket_id}/propose", response_class=HTMLResponse)
    def propose(request: Request, ticket_id: str, force: str = Form("")):
        ticket_or_404(ticket_id)
        ticket = workflow.propose(services, ticket_id, force=force == "1")
        return render("_level2.html", request, ticket=ticket, summary=_summary(ticket), oob=True)

    @app.post("/tickets/{ticket_id}/approve", response_class=HTMLResponse)
    def approve(request: Request, ticket_id: str):
        ticket_or_404(ticket_id)
        ticket = workflow.approve(services, ticket_id)
        return render("_level2.html", request, ticket=ticket, summary=_summary(ticket), oob=True)

    @app.post("/tickets/{ticket_id}/dismiss", response_class=HTMLResponse)
    def dismiss(request: Request, ticket_id: str):
        ticket_or_404(ticket_id)
        ticket = workflow.dismiss(services, ticket_id)
        return render("_level2.html", request, ticket=ticket, summary=_summary(ticket), oob=True)

    @app.get("/insights", response_class=HTMLResponse)
    def insights_page(request: Request):
        return render(
            "insights.html",
            request,
            report=db.get_insights(conn),
            groups=insights.group_tickets(db.list_tickets(conn)),
            drafts=db.list_ab_drafts(conn),
            placements=insights.PLACEMENTS,
        )

    @app.post("/insights/run", response_class=HTMLResponse)
    def insights_run(request: Request):
        tickets = db.list_tickets(conn)
        groups = insights.group_tickets(tickets)
        if services.demo or services.anthropic is None or not services.model:
            report = db.get_insights(conn) or {"error": "Demo mode: insights come from the snapshot" if services.demo else "ANTHROPIC_API_KEY is not set"}
        else:
            try:
                report = insights.analyse(services.anthropic, services.model, groups).model_dump()
                db.save_insights(conn, report)
                report = db.get_insights(conn)
            except agent.AgentError as exc:
                report = {"error": str(exc)}
        return render("_insights_report.html", request, report=report, groups=groups, drafts=db.list_ab_drafts(conn), placements=insights.PLACEMENTS)

    @app.post("/insights/ab-draft", response_class=HTMLResponse)
    def ab_draft(request: Request, placement_id: str = Form(...), hypothesis: str = Form("")):
        groups = insights.group_tickets(db.list_tickets(conn))
        report = db.get_insights(conn)
        error = None
        if services.demo or app.state.ab_runner is None:
            error = "Demo mode: no A/B test is created; in live mode this calls `adapty ab-tests create` (draft only)."
        else:
            try:
                draft = insights.draft_ab_test(app.state.ab_runner, placement_id, hypothesis)
                db.save_ab_draft(conn, placement_id, draft["ab_test_id"], draft["title"], draft)
            except insights.DraftError as exc:
                error = str(exc)
        return render("_insights_report.html", request, report=report, groups=groups, drafts=db.list_ab_drafts(conn), placements=insights.PLACEMENTS, draft_error=error)

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "demo": demo}

    return app


def _summary(ticket: dict) -> dict | None:
    return agent.profile_summary(ticket["profile"]) if ticket.get("profile") else None


def build_services_from_env() -> tuple[Path, bool, Client | None, anthropic.Anthropic | None, str | None, insights.Runner | None]:
    """What `python -m resolve` wires up: the snapshot copy in demo mode, the real clients otherwise."""
    if config.DEMO_MODE:
        runtime = config.ROOT / "data" / "demo-run.sqlite"
        runtime.parent.mkdir(parents=True, exist_ok=True)
        for suffix in ("", "-wal", "-shm"):
            Path(str(runtime) + suffix).unlink(missing_ok=True)
        shutil.copyfile(config.DEMO_DB_PATH, runtime)
        return runtime, True, None, None, None, None
    adapty_client = Client(config.adapty_secret())
    anthropic_client = model = None
    try:
        anthropic_client = agent.make_client()
        model = agent.resolve_model(anthropic_client)
    except config.MissingKey as exc:
        print(f"warning: {exc}; Level 2 and Level 1 are disabled")
        anthropic_client = None
    return config.DB_PATH, False, adapty_client, anthropic_client, model, insights.Runner()
