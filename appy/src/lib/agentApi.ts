import createClient from 'openapi-fetch';

import type { components, paths } from '../api/schema';
import { MOCK_ACCOUNTS, MOCK_USERS, type AccountContext } from '../data/mockAccounts';
import { TRIAGE_DB } from '../data/triageDb';
import { resolveIssue } from './mockAgent';
import { triage } from './triage';

/** Types straight from ../docs/openapi.json (regenerate with `npm run api:types`). */
export type Schemas = components['schemas'];
export type ChatResult = Schemas['ChatResult'];
export type ChatSession = Schemas['ChatSession'];
export type UserItem = Schemas['UserItem'];

/**
 * What the app needs from the Resolve JSON API ("mobile" tag in the contract):
 *   POST /chat                    one user message → the Level 2 agent's reply (may hand off)
 *   GET  /api/chat/{session_id}   poll for support replies after a handoff
 *   GET  /api/users               seeded demo users for the user picker
 */
export interface AgentApi {
  listUsers(): Promise<UserItem[]>;
  sendMessage(customerUserId: string, sessionId: string, message: string): Promise<ChatResult>;
  getSession(sessionId: string): Promise<ChatSession>;
}

function errorText(error: unknown, status: number): string {
  const detail = (error as { detail?: unknown } | undefined)?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map((d) => d?.msg ?? JSON.stringify(d)).join('; ');
  return `HTTP ${status}`;
}

/**
 * A failed request. `kind` tells the chat what to say:
 * network = the server can't be reached, timeout = no answer in time, http = the server answered with an error.
 */
export class ApiError extends Error {
  constructor(
    readonly kind: 'network' | 'timeout' | 'http',
    message: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

/** Per request, in ms. A chat reply is up to a few model calls plus an Adapty read. */
export const DEFAULT_TIMEOUTS = { chat: 60_000, read: 10_000 };

/** Real backend, called through a client typed by the OpenAPI contract. */
export class HttpAgentApi implements AgentApi {
  private client;
  private timeouts: typeof DEFAULT_TIMEOUTS;

  constructor(
    readonly baseUrl: string,
    fetchImpl?: (req: Request) => Promise<Response>,
    timeouts: Partial<typeof DEFAULT_TIMEOUTS> = {},
  ) {
    this.timeouts = { ...DEFAULT_TIMEOUTS, ...timeouts };
    this.client = createClient<paths>({
      baseUrl: baseUrl.replace(/\/$/, ''),
      // Resolve the global at call time so tests can replace it.
      fetch: fetchImpl ?? ((req: Request) => globalThis.fetch(req)),
    });
  }

  /** fetch has no timeout of its own (on Android a hung request never ends), so abort it ourselves. */
  private async call<T>(
    ms: number,
    run: (signal: AbortSignal) => Promise<{ data?: T; error?: unknown; response: Response }>,
  ): Promise<T> {
    const controller = new AbortController();
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, ms);
    try {
      const { data, error, response } = await run(controller.signal);
      if (data === undefined) throw new ApiError('http', errorText(error, response.status), response.status);
      return data;
    } catch (e) {
      if (e instanceof ApiError) throw e;
      if (timedOut) throw new ApiError('timeout', `no answer in ${Math.round(ms / 1000)} s`);
      throw new ApiError('network', (e as Error)?.message || 'network error');
    } finally {
      clearTimeout(timer);
    }
  }

  listUsers() {
    return this.call(this.timeouts.read, (signal) => this.client.GET('/api/users', { signal }));
  }

  sendMessage(customerUserId: string, sessionId: string, message: string) {
    return this.call(this.timeouts.chat, (signal) =>
      this.client.POST('/chat', { body: { customer_user_id: customerUserId, session_id: sessionId, message }, signal }),
    );
  }

  getSession(sessionId: string) {
    return this.call(this.timeouts.read, (signal) =>
      this.client.GET('/api/chat/{session_id}', { params: { path: { session_id: sessionId } }, signal }),
    );
  }
}

type MockSession = ChatSession & { replyDueAt?: number };

/**
 * Offline stand-in that follows the same contract. The FAQ (TRIAGE DB)
 * answers what it can; anything else is handed off, and a simulated
 * support teammate replies later using the user's billing history.
 */
export class MockAgentApi implements AgentApi {
  private sessions = new Map<string, MockSession>();
  private ticketSeq = 0;
  /** Per-instance copy, so refunds in one run don't leak into another. */
  private accounts: Record<string, AccountContext> = structuredClone(MOCK_ACCOUNTS);

  constructor(private opts: { replyMs?: number; now?: () => number } = {}) {}

  private now() {
    return this.opts.now?.() ?? Date.now();
  }

  private ts() {
    return new Date(this.now()).toISOString();
  }

  async listUsers() {
    return structuredClone(MOCK_USERS);
  }

  async sendMessage(customerUserId: string, sessionId: string, message: string): Promise<ChatResult> {
    let s = this.sessions.get(sessionId);
    if (!s) {
      s = { session_id: sessionId, customer_user_id: customerUserId, outcome: 'open', messages: [], events: [] };
      this.sessions.set(sessionId, s);
    }
    s.messages.push({ role: 'user', content: message, ts: this.ts() });

    let result: ChatResult;
    const answer = s.outcome === 'handed_off' ? null : triage(message, TRIAGE_DB);
    if (answer?.canHelp) {
      result = { reply: answer.answer, handed_off: false, actions: [] };
    } else if (s.outcome === 'handed_off') {
      result = { reply: 'A teammate has your conversation and will reply here.', handed_off: true, ticket_id: s.ticket_id };
    } else {
      s.outcome = 'handed_off';
      s.ticket_id = `T-${1001 + this.ticketSeq++}`;
      result = {
        reply: "I've passed this to a teammate who can help with this; they will reply here.",
        handed_off: true,
        ticket_id: s.ticket_id,
        actions: [],
      };
    }
    if (result.handed_off) s.replyDueAt = this.now() + (this.opts.replyMs ?? 5000);
    s.messages.push({ role: 'agent', content: result.reply, ts: this.ts() });
    return result;
  }

  async getSession(sessionId: string): Promise<ChatSession> {
    const s = this.sessions.get(sessionId);
    if (!s) throw new ApiError('http', `no session ${sessionId}`, 404);
    if (s.replyDueAt !== undefined && this.now() >= s.replyDueAt) {
      s.replyDueAt = undefined;
      // The teammate answers the latest user message, with the account as context.
      const last = [...s.messages].reverse().find((m) => m.role === 'user');
      s.messages.push({
        role: 'support',
        content: resolveIssue(last?.content ?? '', this.accounts[s.customer_user_id]),
        ts: this.ts(),
      });
    }
    const { replyDueAt: _due, ...session } = s;
    return structuredClone(session);
  }
}

/** Base URL of the Resolve API from the env; empty = offline mock. */
export const AGENT_API_URL = process.env.EXPO_PUBLIC_AGENT_API_URL ?? '';

export function createAgentApi(): AgentApi {
  return AGENT_API_URL ? new HttpAgentApi(AGENT_API_URL) : new MockAgentApi();
}
