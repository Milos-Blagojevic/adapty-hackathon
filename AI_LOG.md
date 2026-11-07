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
| Sat, skeleton commit | Claude Code | First commit (`README`, `AGENTS.md`, `.env.example`, `AI_LOG.md`) | The agent wrote `.env.example` and ran `git add -A`; the pre-event `.gitignore` has `.env.*`, which silently swallowed it | The staged-file list in the commit output had five files, not six; fixed with `!.env.example` and amended |
| Sat, data look | Claude Code + pandas | Picking a converted weekly-trial user for Gate 1 | The agent wrote `tt.product.value_counts()`; `DataFrame.product` is a method, so it crashed; it also filtered `renew_status == "active"` when the column is boolean, returning zero candidates | Tracebacks and an empty result; fixed to `tt["product"]` and `renew_status == True` |
| Sat, Gate 1 | Claude Code + `scripts/adapty_check.py` | `getProfile` + `grantAccessLevel` on `00042` (free) and `00549` (active weekly) | Not an error, a finding worth knowing before the demo: after a grant the `premium` access level reads `store=adapty`, `store_product_id=adapty_server_side_product`, with `renewal_cancelled_at` set to the grant time, while `subscriptions[]` still shows the Play weekly renewing on Nov 13. `expires_at` matched the request exactly on both profiles | Read the grant response and a second `getProfile` back, field by field, instead of trusting the 200 |
| Sat, Gate 1 | Claude Code | Offline CSV → live profile | Live timestamps are shifted from the offline CSV by a constant (offline expiry `2026-11-09T10:37:31Z`, live `2026-11-13T05:08:30Z`); a ticket generator that quotes offline dates would contradict the live profile the agent sees | Compared the offline row with the first live `getProfile` |

## Prompts that worked

(to fill in)

## What we would never delegate

- Adapty writes: every grant and the A/B draft run on a human click, after the human read the proposal.
- The 20 eval labels: hand-labelled by a person, never by the model under test.
