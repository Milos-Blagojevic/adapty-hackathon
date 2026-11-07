# AI log

How AI was used to build Resolve, what it got wrong, and how we caught it. Appended during the build, not written up afterwards.

## Setup

- Claude Code with `AGENTS.md` (conventions, module layout, hard limits, Adapty docs pointers) read on every session.
- Adapty skills (`adapty-docs`, `ab-tests`) for the server-side API operation pages and the A/B-test CLI contract; Adapty CLI 0.18 logged in on the build machine.
- The scope document (`scope.md`, written before the event, text only) as the brief; its "Handover for the building agent" section carries paths, defaults and limits.
- Owner decisions the agent was told not to infer: Gate 1 profiles (free user `00042` and active weekly subscriber `00549`), the compensation rule (`max(now, current expiry) + 7 days`), README layout.

## Log

| When | Tool | What for | What went wrong | How it was caught |
|---|---|---|---|---|
| Sat, before Gate 1 | Claude Code + `adapty-docs` skill | Server-side API auth and body format | — (read the operation pages before the first call instead of guessing) | — |

## Prompts that worked

(to fill in)

## What we would never delegate

- Adapty writes: every grant and the A/B draft run on a human click, after the human read the proposal.
- The 20 eval labels: hand-labelled by a person, never by the model under test.
