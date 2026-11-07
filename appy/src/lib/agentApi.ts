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

/** Real backend, called through a client typed by the OpenAPI contract. */
export class HttpAgentApi implements AgentApi {
  private client;

  constructor(baseUrl: string, fetchImpl?: (req: Request) => Promise<Response>) {
    this.client = createClient<paths>({
      baseUrl: baseUrl.replace(/\/$/, ''),
      // Resolve the global at call time so tests can replace it.
      fetch: fetchImpl ?? ((req: Request) => globalThis.fetch(req)),
    });
  }

  async listUsers() {
    const { data, error, response } = await this.client.GET('/api/users');
    if (!data) throw new Error(errorText(error, response.status));
    return data;
  }

  async sendMessage(customerUserId: string, sessionId: string, message: string) {
    const { data, error, response } = await this.client.POST('/chat', {
      body: { customer_user_id: customerUserId, session_id: sessionId, message },
    });
    if (!data) throw new Error(errorText(error, response.status));
    return data;
  }

  async getSession(sessionId: string) {
    const { data, error, response } = await this.client.GET('/api/chat/{session_id}', {
      params: { path: { session_id: sessionId } },
    });
    if (!data) throw new Error(errorText(error, response.status));
    return data;
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
    if (!s) throw new Error(`no session ${sessionId}`);
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

export function createAgentApi(): AgentApi {
  const url = process.env.EXPO_PUBLIC_AGENT_API_URL;
  return url ? new HttpAgentApi(url) : new MockAgentApi();
}
