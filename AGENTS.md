# AGENTS.md

Resolve: a support inbox for a subscription app (the seeded Lumen app on Adapty). Users send purchase complaints; the Level 2 agent proposes a category, root cause, action and reply with the user's live Adapty profile in hand; the owner approves; the approved action runs in Adapty (`grantAccessLevel`). Level 1 groups all tickets into insights and drafts an A/B test on the offending placement. Built at the Adapty Hackathon Belgrade, 7–8 Nov 2026. `README.md` is the user documentation; the scope lives outside the repo (`~/hackathon/scope.md`).

Python 3.11+, FastAPI + Jinja + htmx, SQLite, one `uvicorn` process. Anthropic API with structured output for the agent. Adapty server-side API v2 for profiles and grants; the Adapty CLI (`adapty ab-tests create`) for the A/B draft.

## Layout

| Module | Role |
|---|---|
| `resolve/config.py` | Settings from the environment (`.env` via python-dotenv): keys, app id, access level, compensation days, `DEMO_MODE`, DB path |
| `resolve/adapty.py` | The only module that talks to the Adapty server-side API: `get_profile`, `grant_access_level`, `update_profile`, the compensation expiry rule (`compensation_expiry`), `AdaptyError`. `requests` with a 15 s timeout, no retries |
| `resolve/tickets.py` | Ticket model, statuses, categories, actions, the synthetic ticket generator from the offline CSVs |
| `resolve/db.py` | SQLite schema, connection, seeding from `data/tickets.json` |
| `resolve/agent.py` | Level 2: the `Proposal` pydantic model, prompt, Anthropic call, enum validation, cache on the ticket, call cap |
| `resolve/insights.py` | Level 1: grouping (category × product, by placement/flow from the CSV mapping), hypothesis, A/B draft through the CLI |
| `resolve/web.py` | FastAPI app, routes, htmx partials, templates in `resolve/templates/`, CSS in `resolve/static/` |
| `scripts/` | `generate_tickets.py`, `adapty_check.py` (Gate 1 proof), `eval.py` (20-ticket eval runner), `snapshot.py` (DEMO_MODE snapshot) |
| `data/` | `tickets.json` (seed tickets, 3 with `demo: true`), `eval.json` (hand-labelled), `demo.sqlite` (committed snapshot) |
| `tests/` | pytest; no network, no keys |

## Commands

```bash
uv venv .venv && source .venv/bin/activate   # or python3 -m venv .venv
pip install -r requirements.txt
cp .env.example .env                         # fill in the keys
python -m resolve                            # http://127.0.0.1:8000
DEMO_MODE=1 python -m resolve                # no keys, committed snapshot
python -m pytest
python scripts/eval.py                       # paid: 20 Anthropic calls; ask first
```

## Hard limits (from the scope; do not relax)

- Adapty writes allowed: `grantAccessLevel` and `updateProfile` on seeded profiles, `adapty ab-tests create` (draft only). Never `ab-tests start`/`stop`/`replace`/`archive`, never `placements update`, never anything in Adapty Mail, never the seeder (`seed_adapty.py`).
- Every Adapty write happens on a click in the UI (Approve, Draft A/B test). Nothing autonomous.
- Anthropic calls cost money: cache proposals on the ticket, cap at 3 model calls per ticket, no batch runs over all tickets without asking the owner. The eval runner is 20 calls; ask before running it.
- No real end-user data: tickets are synthetic; App Store reviews are never ticket text.
- Nothing sends email or messages anywhere. "Sent" is a status.
- `.env` is git-ignored. Never print, log or commit `ADAPTY_SECRET_KEY` or `ANTHROPIC_API_KEY`.

## Conventions

- Adapty server-side API: `Authorization: Api-Key <secret>`, user addressed by the `adapty-customer-user-id` header, base `https://api.adapty.io`. Docs: https://adapty.io/docs/ss-authorization.md and `https://adapty.io/docs/api-adapty/operations/<operationId>.md` (`getProfile`, `grantAccessLevel`, `updateProfile`). The `adapty-docs` skill indexes the rest; do not guess slugs.
- Compensation = 7 days of `premium`: `expires_at = max(now, current premium expires_at) + 7 days` (extends an active subscription, starts now for free or expired users). A lifetime access level (no `expires_at`) is not extended; the agent must not propose `grant_access` for it.
- Enums are fixed: categories `charged_after_trial`, `no_access_after_purchase`, `billing_issue`, `refund_request`, `cancel_how`, `price_complaint`, `other`; actions `grant_access`, `explain`, `escalate_refund`, `no_action`; statuses `new → triaged → proposed → approved → done`, plus `dismissed`. Agent output outside the enums is rejected, not coerced.
- Approve is idempotent: a ticket that is already `done` is never granted again; the grant response is stored on the ticket. An Adapty failure surfaces in the UI and leaves the ticket in `proposed`.
- The user → placement/flow/A/B arm mapping comes from `data/offline/users.csv.gz` in the seed repo, not from the live profile; say so in the UI.
- `DEMO_MODE=1` reads `data/demo.sqlite` and never calls Adapty or Anthropic; Approve changes local state and the page says so.
- Tests make no network calls: Adapty is mocked with `responses`, the Anthropic client with a fake, the CLI with a stub command.

## AI log

Append to `AI_LOG.md` as things happen: tool, what for, one thing it got wrong and how it was caught. Not at the end.
