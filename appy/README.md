# appy — FitBuddy, a fitness app with an in-app support chat

Expo / React Native client (iOS, Android, web) for the Resolve JSON API in this repo.
It talks to the backend only through the **mobile** endpoints of [`../docs/openapi.json`](../docs/openapi.json):

| Request | Used for |
| --- | --- |
| `GET /api/users` | "Chat as" user picker (seeded demo users) |
| `POST /chat` `{ customer_user_id, session_id, message }` | Every chat message. The Level 2 agent replies, or hands off (`handed_off`, `ticket_id`) |
| `GET /api/chat/{session_id}` | Polled every 3 s after a handoff; new `support` messages appear in the chat |

Requests go through [`openapi-fetch`](https://openapi-ts.dev/openapi-fetch/), typed by `src/api/schema.d.ts`,
which is generated from the contract. After the contract changes:

```bash
npm run api:types
```

## Run

```bash
# backend (repo root), keyless demo
DEMO_MODE=1 python -m resolve --port 8010

# app
cd appy
npm install
cp .env.example .env.local          # EXPO_PUBLIC_AGENT_API_URL=http://localhost:8010
npm start                           # w = web, i = iOS simulator, or scan with Expo Go
```

On a phone, set `EXPO_PUBLIC_AGENT_API_URL` to the laptop's LAN IP (e.g. `http://192.168.1.20:8010`).

Without `EXPO_PUBLIC_AGENT_API_URL` the app uses `MockAgentApi`, which follows the same contract offline.
It answers FAQ questions from a local list (`src/data/triageDb.ts`) and hands off everything else. A simulated
support teammate then replies using a fake billing history (`src/data/mockAccounts.ts`): duplicate charge,
wrong amount, Premium not activated, or a refund.

In `DEMO_MODE` the backend's agent sends a fixed reply. It hands off after 8 user messages, and no support reply
comes back, because accepting a ticket needs a triage proposal from an LLM.

## Checks

```bash
npm run typecheck && npm run lint && npm test
```

## Layout

- `src/app/`: screens (Home, Workouts, Assistant)
- `src/api/schema.d.ts`: generated from `../docs/openapi.json`, do not edit
- `src/lib/agentApi.ts`: `HttpAgentApi` (real, typed by the contract) and `MockAgentApi` (offline)
- `src/lib/support.tsx`: chat state, sessions, polling for support replies
- `src/lib/fitness.tsx`: workouts, weekly goal, streak
- `src/lib/triage.ts`, `src/lib/mockAgent.ts`, `src/data/`: used by the mock only
