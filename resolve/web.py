"""FastAPI + Jinja + htmx: the inbox, the ticket page (Level 1 triage), the insights page, and the JSON API (`api.install`)."""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

import pydantic
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from resolve.models import Health
from resolve import api, config, db, insights, llm, models, policy, triage, workflow
from resolve.adapty import Client, parse_datetime
from resolve.tickets import CATEGORIES, STATUSES
from resolve.workflow import Services

HERE = Path(__file__).resolve().parent

ACTION_LABELS = {
    "grant_access": f"Grant days of {config.ACCESS_LEVEL}",
    "reply_only": "Reply only",
    "store_instructions": "Store instructions",
    "refund_to_human": "Refund through the store (a person follows up)",
    "ask_user": "Ask the user",
    "no_reply": "No reply",
    # v1 actions, still on old done tickets in the snapshot
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
    llm_client: llm.LLM | None = None,
    seed_file: Path = config.ROOT / "data" / "tickets.json",
    ab_runner: insights.Runner | None = None,
) -> FastAPI:
    conn = db.connect(db_path)
    if seed_file.exists():
        db.seed(conn, seed_file)
    services = Services(conn=conn, demo=demo, adapty=adapty_client, llm=llm_client)

    app = FastAPI(title="Resolve")
    app.state.services = services
    app.state.ab_runner = ab_runner
    api.install(app, services)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.filters["age"] = age
    templates.env.filters["fmt_date"] = fmt_date
    templates.env.filters["fmt_day"] = fmt_day
    templates.env.globals.update(
        demo=demo,
        statuses=STATUSES,
        categories=CATEGORIES,
        actions=models.ACTIONS,
        store_kinds=("restore", "cancel", "refund_request"),
        stores=("app_store", "play_store"),
        action_labels=ACTION_LABELS,
        triage_of=workflow.triage_of,
        category_labels=CATEGORY_LABELS,
        access_level=config.ACCESS_LEVEL,
        flow_titles=insights.FLOW_TITLES,
        compensation_days=config.COMPENSATION_DAYS,
        max_calls=triage.MAX_CALLS,
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
        return render("ticket.html", request, ticket=ticket, summary=_summary(conn, ticket))

    @app.post("/tickets/{ticket_id}/profile", response_class=HTMLResponse)
    def refresh_profile(request: Request, ticket_id: str):
        ticket_or_404(ticket_id)
        ticket = workflow.open_ticket(services, ticket_id, refresh=True)
        return render("_profile.html", request, ticket=ticket, summary=_summary(conn, ticket))

    @app.post("/tickets/{ticket_id}/propose", response_class=HTMLResponse)
    def propose(request: Request, ticket_id: str, force: str = Form("")):
        ticket_or_404(ticket_id)
        ticket = workflow.propose(services, ticket_id, force=force == "1")
        return render("_level2.html", request, ticket=ticket, summary=_summary(conn, ticket), oob=True)

    @app.post("/tickets/{ticket_id}/approve", response_class=HTMLResponse)
    def approve(request: Request, ticket_id: str):
        ticket_or_404(ticket_id)
        ticket = workflow.approve(services, ticket_id)
        return render("_level2.html", request, ticket=ticket, summary=_summary(conn, ticket), oob=True)

    @app.post("/tickets/{ticket_id}/accept", response_class=HTMLResponse)
    def accept(
        request: Request,
        ticket_id: str,
        option: int = Form(0),
        action: str = Form(""),
        reply: str | None = Form(None),
        days: str = Form(""),
        kind: str = Form(""),
        store: str = Form(""),
        question: str = Form(""),
    ):
        """One option card's Accept (option only) or its edit form (option + edits). Empty fields keep the option's value."""
        ticket_or_404(ticket_id)
        try:
            edits = models.OptionEdits(
                action=action or None, reply=reply, days=int(days) if days.strip() else None,
                kind=kind or None, store=store or None, question=question or None,
            )
            ticket = workflow.accept(services, ticket_id, models.Decision(option=option, edits=edits))
        except (pydantic.ValidationError, ValueError) as exc:
            raise HTTPException(422, str(exc).splitlines()[0]) from None
        return render("_level2.html", request, ticket=ticket, summary=_summary(conn, ticket), oob=True)

    @app.post("/tickets/{ticket_id}/reject", response_class=HTMLResponse)
    def reject(request: Request, ticket_id: str, note: str = Form(""), reply: str = Form("")):
        ticket_or_404(ticket_id)
        ticket = workflow.reject(services, ticket_id, models.Rejection(note=note, reply=reply or None))
        return render("_level2.html", request, ticket=ticket, summary=_summary(conn, ticket), oob=True)

    @app.post("/tickets/{ticket_id}/dismiss", response_class=HTMLResponse)
    def dismiss(request: Request, ticket_id: str):
        ticket_or_404(ticket_id)
        ticket = workflow.dismiss(services, ticket_id)
        return render("_level2.html", request, ticket=ticket, summary=_summary(conn, ticket), oob=True)

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
        if services.demo or services.llm is None:
            report = db.get_insights(conn) or {"error": "Demo mode: insights come from the snapshot" if services.demo else "LLM is not configured"}
        else:
            try:
                db.save_insights(conn, triage.run_insights(services, groups))
                report = db.get_insights(conn)
            except triage.TriageError as exc:
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

    @app.get("/healthz", response_model=Health, tags=["mobile"])
    def healthz():
        return {"ok": True, "demo": demo}

    return app


def _summary(conn: db.Database, ticket: dict) -> dict | None:
    return triage.profile_summary(ticket["profile"], grant=policy.load_config(conn).grant) if ticket.get("profile") else None


def build_services_from_env() -> tuple[Path, bool, Client | None, llm.LLM | None, insights.Runner | None]:
    """What `python -m resolve` wires up: the snapshot copy in demo mode, the real clients otherwise."""
    if config.DEMO_MODE:
        runtime = config.ROOT / "data" / "demo-run.sqlite"
        runtime.parent.mkdir(parents=True, exist_ok=True)
        for suffix in ("", "-wal", "-shm"):
            Path(str(runtime) + suffix).unlink(missing_ok=True)
        shutil.copyfile(config.DEMO_DB_PATH, runtime)
        return runtime, True, None, None, None
    adapty_client = Client(config.adapty_secret())
    llm_client = None
    try:
        llm_client = llm.make_llm()
    except config.MissingKey as exc:
        print(f"warning: {exc}; Level 1 triage, Level 2 chat and insights are disabled")
    return config.DB_PATH, False, adapty_client, llm_client, insights.Runner()
