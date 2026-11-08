# Adapty Support · web UI (design hand-off)

Design sources for the support web app: Incoming, Ticket, Chat, Agent settings, Analyze, New feedback, Reopen, States and motion, All screens.

## What is in here

- `project/*.dc.html` — one file per screen. The markup inside `<x-dc>` is plain HTML + inline CSS; page CSS is in `<helmet><style>`; the behaviour and mock data are in the `<script type="text/x-dc">` block at the bottom (`renderVals()` returns everything the template uses, `{{ name }}` = a value from it, `<sc-for>` = loop, `<sc-if>` = condition).
- `project/canvas.json` — the canvas layout (which screen is where, titles).
- `spec.md` — the original brief.

These files run inside the Design canvas runtime, not as standalone pages. For standalone HTML, use Share › Export on the canvas, or copy the markup and CSS into `static/ui-alex/` and wire it to `app.js`.

## Screens → routes → API

| File | Route | Data |
|---|---|---|
| Main.dc.html (Incoming) | `#/incoming` | `GET /api/tickets?status=&q=` |
| Ticket.dc.html | `#/incoming/{id}` | `GET /api/tickets/{id}`, `POST …/accept`, `…/reject` |
| Reopen.dc.html | `#/reopen/{id}` | `POST /api/tickets/{id}/reopen` |
| NewFeedback.dc.html | `#/new` | new ticket (form / email) |
| Chat.dc.html | `#/chat/{session}` | `POST /chat`, `GET /api/chat/{session}` |
| Settings.dc.html | `#/settings` | `GET/PUT /api/config`, `GET /api/llm` |
| Analyze.dc.html | `#/analyze` | `GET /api/analytics` + new fields below |

**New in Analyze, not in the current API:** per-reply records for the agent (from the app chat logs: time, user, reply text, latency, outcome) and for people (from replies sent in Incoming), to compute: time to first reply, closed with that reply, user came back unhappy, passed on, free days given.

## Tokens

- Page `#f6f6f4`, cards `#fff` with `1px #e9e9ec`, radius 16; controls radius 8–10.
- Text `#18181b`, secondary `#52525b`, muted `#71717a`.
- Brand violet `#6247f5` (active nav `#efedfb` / `#5b3cf0`).
- Hero band: `#12064f → #2a0fa0 → #4a22e0 → #7b5cff` with a white dot texture (7px grid).
- Analyze: machines `#2563eb`, humans `#e5484d`, closed by humans `#8f1d3a`, still open `#c2770f`, other feedback `#52525b` (checked for colour-blind readers).
- Counts: Open red `#d63a3f`, Closed green `#15803d`. Send / OK stamps green `#16a34a`.
- Type: Inter 400/500/600/700; page titles 26–32px, body 14.5px, labels 12px uppercase +0.09em.
- Motion 150–340 ms: OK stamp 300 ms, SEND press 320 ms, ticket → Closed 340 ms, toast 220 ms, menus 180 ms.
