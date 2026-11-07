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
| Sat, generator | Claude Code | Ticket generator from the CSVs | The agent compared `renew_status != "True"` after seeing pandas print `True`; the raw CSV says `true`, so `cancel_how` had zero candidates and the run crashed | The `ValueError` from the generator's own pool check; fixed with `.lower()` and a test that asserts the mix |
| Sat, generator | Claude Code | Ticket ages in the inbox | Tickets whose complaint window reached past "now" were clamped to `now − 5 min`, so the first inbox screenshot showed five tickets all "11m" old | Looked at the rendered inbox instead of the JSON; the window is now spread between the event and now |
| Sat, agent | Claude Code | Retry on invalid structured output | The first draft of `agent.propose` echoed the model's previous text as an assistant turn before the correction, but when pydantic rejects the output inside `messages.parse` there is no response object to echo: `UnboundLocalError` on the retry path | Read the SDK (`parse_text` raises inside `parse`) before running it; the retry now resends the ticket with the rejection reason in the user turn |
| Sat, agent | Claude Code | Prompt policy | The first prompt said a bug report gets `no_action` "with no reply needed"; labelling the eval set showed that contradicts how support works (a crash report deserves a reply) | Writing the 20 labels by hand before running the model: the label disagreed with the prompt, the prompt changed |
| Sat, Level 1 | Claude Code | A/B test link | The agent wrote `https://app.adapty.io/ab-tests/<id>` as the dashboard link for a draft; the CLI's `ab-tests get` has no URL field and the docs do not state the pattern | Checked the CLI output for a URL field before trusting the guess; the link is out until the owner confirms the pattern |
| Sat, tests | Claude Code + `responses` | Demo-mode test | `assert http.calls == []` failed: `responses` returns a `CallList`, not a list | The test failed on first run; `len(...) == 0` |

## Prompts that worked

(to fill in)

## What we would never delegate

- Adapty writes: every grant and the A/B draft run on a human click, after the human read the proposal.
- The 20 eval labels: hand-labelled by a person, never by the model under test.
