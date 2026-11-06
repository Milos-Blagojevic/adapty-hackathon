# Adapty Hackathon Belgrade, 7–8 November 2026

Team repo. Created empty before the event, as the rules ask; all code is written during the hackathon.

- Event page and agenda: https://adapty-hackathon.netlify.app/ (Haos, Kraljice Natalije 11, 2nd floor)
- Guidelines and rules: https://app.notion.com/p/adapty/Adapty-Hackathon-Belgrade-Before-you-start-4101ca4355c381e2ae2df84ce84b37fc
- Demo data (the organisers' seeder, not vendored here): https://github.com/kpotehin/adapty-seed-data-hack
- Team check-in form (once per team): https://docs.google.com/forms/d/e/1FAIpQLSdVWJmF_5uBFtEioTUzXunFvP7WFPkpVENxcWgtL2vMUDW05A/viewform

## Pre-existing material (rule 1 disclosure)

This README and `.gitignore`. Nothing else.

## Before Saturday (the organisers' checklist)

1. Account at https://app.adapty.io. **One of us seeds, then invites the other two** from the
   dashboard's team settings, so we all see the same data (FAQ allows this). Seed in the evening,
   not Saturday morning: the run takes ~15 min and charts take up to 30 min more.
   ```bash
   npm install -g adapty@latest && adapty auth login
   git clone https://github.com/kpotehin/adapty-seed-data-hack.git && cd adapty-seed-data-hack
   python3 seed_adapty.py --dry-run     # see what it creates
   python3 seed_adapty.py               # once; asks for the new Lumen app's secret key (App settings → General)
   ```
   Then check Flows, Profiles and A/B tests are filled in. Seed each app **once**; to start over, use
   a new app, never re-run into the old one days later.
2. Node 18+, Python 3.9+. Mobile tooling is optional: the FAQ says most ideas need only the
   APIs, CLI and MCP.
3. AI tooling: `npx adapty skills install --yes` (Adapty skills + MCP server for Claude Code /
   Codex), then in Claude Code `/mcp` → `plugin:adapty-skills:adapty` → Authenticate. Without the
   plugin: `claude mcp add --transport http --scope user adapty https://mcp.adapty.io/mcp`.
   The MCP server acts with your account's rights: started A/B tests and placement changes are real.
4. Think about problems. Write no code before kickoff.

## Rules that bite

- Core of the project written during the event; anything pre-existing disclosed in this README.
- Use Adapty meaningfully. "Generic AI app with an Adapty logo" scores badly; a project competing
  with Adapty scores worse than one that uses it.
- Demo or synthetic data only. No real end-user data from any company.
- No committed secrets: `.env` is ignored. No load tests, no scraping.
- Any AI tools allowed; be ready to explain how we used them.

## What the judges will ask (answer these in the demo)

1. Who exactly is this for, and what do they do today without it?
2. What changes for them: money, time or risk?
3. Why does it need Adapty? If it uses AI, why does it need AI?
4. What is the smallest version that proves the idea by Sunday 15:00?

Where the organisers suggest looking: the people around Adapty (SDK developers, growth people
running experiments, analysts reading charts, support answering purchase questions); the data
(what could someone know earlier or decide better); AI as an interface over an Adapty account
(what should software do alone, where does a human stay in the loop); and the rough edges we hit
ourselves in the dashboard, CLI and API on Saturday morning.

## Demo data in one paragraph

Fictional app **Lumen — AI Photo Editor**: 6 products, 2 onboardings + 5 paywalls, 5 placements,
3 segments, 4 A/B tests, 5,000 profiles with custom attributes, ~2,900 transactions over 4 months,
plus ~1,000 "today" users whose flow views and day-one purchases fill A/B tests and placement
metrics (the 4-month history shows "No Data" there). `data/offline/` in the seed repo has the same
population at 50,000 users / ~30,000 transactions as CSV, with `placement`, `flow` and `ab_test`
per user, for analysis and ML without the API. Store product IDs are fictional, so real device
purchases need sandbox products of our own.

## Schedule

| When | What |
|---|---|
| Sat 10:10 | Brief + 15-min Adapty walkthrough, then Q&A and team check |
| Sat 11:00 | Hacking starts. First hour: decide, write the demo script, then build only that path |
| Sat 22:00 | Venue closes. Target: demo path runs end-to-end, however ugly |
| Sun 09:30 | Doors open. Polish what the judges will see |
| Sun 12:00 | Our feature freeze. Rehearse ×3, record a backup video, fill the submission form |
| Sun 15:00 | Code freeze: repo + submission form |
| Sun 15:30 | Demos: 5 min + 3 min Q&A per team |

## Working agreement

- `main` is always demoable. Small commits, push often, pull before you start.
- Hardcode anything not on the demo path. Seed data, fake users, mocked integrations are fine.
- Every hour: "if code froze now, what would we show?"
