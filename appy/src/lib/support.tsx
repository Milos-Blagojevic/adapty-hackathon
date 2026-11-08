import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react';

import { ApiError, createAgentApi, HttpAgentApi, type AgentApi, type ChatSession, type UserItem } from './agentApi';
import type { ChatAuthor, ChatMessage } from './types';

export const POLL_INTERVAL_MS = 3000;
export const USERS_RETRY_MS = 5000;

/** One conversation with the backend (one session_id). */
export type Session = {
  id: string;
  userId: string;
  messages: ChatMessage[];
  sending: boolean;
  /** Set after a handoff; the app polls the session for support replies. */
  ticketId: string | null;
  /** How many agent / support messages of the server session are already shown. */
  agentSeen: number;
  supportSeen: number;
};

type SupportState = {
  /** true = the real backend, false = the offline mock. */
  live: boolean;
  /** Where the app looks for the backend (empty for the mock). */
  serverUrl: string;
  users: UserItem[];
  /** Why the user list could not be loaded; it is retried every few seconds. */
  usersError: string | null;
  reloadUsers: () => void;
  userId: string | null;
  selectUser: (id: string) => void;
  /** Starts a fresh conversation for the current user. */
  newChat: () => void;
  sessionId: string | null;
  messages: ChatMessage[];
  sending: boolean;
  ticketId: string | null;
  sendMessage: (text: string) => Promise<void>;
  /** Re-sends the message behind a failed request (an error message with `retryText`). */
  retry: (messageId: string) => Promise<void>;
};

const SupportContext = createContext<SupportState | null>(null);

let msgSeq = 0;
const makeMessage = (author: ChatAuthor, text: string, extra: Partial<ChatMessage> = {}): ChatMessage => ({
  id: `m${++msgSeq}`,
  author,
  text,
  createdAt: Date.now(),
  ...extra,
});

const welcome = () =>
  makeMessage('assistant', "Hi! I'm your FitBuddy assistant. Ask me about workouts, your subscription, charges or refunds.");

let sessionSeq = 0;
const newSessionId = (userId: string) => `appy-${userId}-${Date.now().toString(36)}${(++sessionSeq).toString(36)}`;

const newSession = (userId: string): Session => ({
  id: newSessionId(userId),
  userId,
  messages: [welcome()],
  sending: false,
  ticketId: null,
  agentSeen: 0,
  supportSeen: 0,
});

/** What the person in the chat sees when a request fails. */
export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.kind === 'network') return "Can't reach the support server. Check the Wi-Fi connection and try again.";
    if (e.kind === 'timeout') return 'The assistant is taking too long to answer. Try again in a moment.';
    return `The support server could not answer (${e.message}). Try again in a moment.`;
  }
  return `Something went wrong: ${(e as Error)?.message ?? e}. Try again in a moment.`;
}

const handoffNote = (ticketId: string) =>
  makeMessage('system', `Passed to a support teammate · ticket ${ticketId}`, { ticketId });

export function SupportProvider({
  children,
  api: apiProp,
  pollMs = POLL_INTERVAL_MS,
  usersRetryMs = USERS_RETRY_MS,
}: {
  children: ReactNode;
  api?: AgentApi;
  pollMs?: number;
  usersRetryMs?: number;
}) {
  const api = useMemo(() => apiProp ?? createAgentApi(), [apiProp]);
  const [users, setUsers] = useState<UserItem[]>([]);
  const [usersError, setUsersError] = useState<string | null>(null);
  const [usersAttempt, setUsersAttempt] = useState(0);
  const [userId, setUserId] = useState<string | null>(null);
  // Every conversation of this app run, so switching users and back keeps each chat.
  const [sessions, setSessions] = useState<Record<string, Session>>({});
  const [current, setCurrent] = useState<Record<string, string>>({}); // userId → its open session
  // Mirrors for async code: the latest state without re-creating callbacks.
  const sessionsRef = useRef(sessions);
  const currentRef = useRef(current);
  const userIdRef = useRef(userId);
  useLayoutEffect(() => {
    sessionsRef.current = sessions;
    currentRef.current = current;
    userIdRef.current = userId;
  });
  const inFlight = useRef(new Set<string>());

  const [placeholder] = useState(() => [welcome()]); // shown until the user list has loaded
  const sessionId = userId ? (current[userId] ?? null) : null;
  const session = sessionId ? sessions[sessionId] : undefined;

  const update = useCallback((id: string, fn: (s: Session) => Session) => {
    setSessions((prev) => (prev[id] ? { ...prev, [id]: fn(prev[id]) } : prev));
  }, []);
  const push = useCallback(
    (id: string, ...ms: ChatMessage[]) => update(id, (s) => ({ ...s, messages: [...s.messages, ...ms] })),
    [update],
  );

  const openSession = useCallback((uid: string) => {
    const s = newSession(uid);
    currentRef.current = { ...currentRef.current, [uid]: s.id };
    setSessions((prev) => ({ ...prev, [s.id]: s }));
    setCurrent((prev) => ({ ...prev, [uid]: s.id }));
  }, []);

  const selectUser = useCallback(
    (id: string) => {
      setUserId(id);
      if (!currentRef.current[id]) openSession(id);
    },
    [openSession],
  );

  const newChat = useCallback(() => {
    if (userId) openSession(userId);
  }, [userId, openSession]);

  // GET /api/users → pick the first user so the chat is usable right away. Retried until it works.
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    api
      .listUsers()
      .then((list) => {
        if (cancelled) return;
        setUsers(list);
        setUsersError(list.length > 0 ? null : 'The support server has no users to chat as.');
        if (list.length === 0) return;
        const prev = userIdRef.current;
        selectUser(prev && list.some((u) => u.customer_user_id === prev) ? prev : list[0].customer_user_id);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setUsersError(errorMessage(e));
        timer = setTimeout(() => setUsersAttempt((n) => n + 1), usersRetryMs);
      });
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [api, usersAttempt, usersRetryMs, selectUser]);

  const reloadUsers = useCallback(() => setUsersAttempt((n) => n + 1), []);

  /** Shows a ticket note the first time a session is handed off. */
  const noteHandoff = useCallback(
    (id: string, ticketId: string | null | undefined) => {
      if (!ticketId) return;
      update(id, (s) =>
        s.ticketId === ticketId ? s : { ...s, ticketId, messages: [...s.messages, handoffNote(ticketId)] },
      );
    },
    [update],
  );

  /** One POST /chat for a user message that is already shown in the chat. */
  const deliver = useCallback(
    async (id: string, text: string) => {
      const s = sessionsRef.current[id];
      if (!s || inFlight.current.has(id)) return;
      inFlight.current.add(id);
      update(id, (x) => ({ ...x, sending: true }));
      try {
        const result = await api.sendMessage(s.userId, id, text);
        update(id, (x) => ({
          ...x,
          agentSeen: x.agentSeen + 1,
          messages: [...x.messages, makeMessage('assistant', result.reply)],
        }));
        if (result.handed_off) noteHandoff(id, result.ticket_id);
      } catch (e) {
        push(id, makeMessage('system', errorMessage(e), { error: true, retryText: text }));
      } finally {
        inFlight.current.delete(id);
        update(id, (x) => ({ ...x, sending: false }));
      }
    },
    [api, update, push, noteHandoff],
  );

  const sendMessage = useCallback(
    async (raw: string) => {
      const text = raw.trim();
      if (!text || !sessionId || inFlight.current.has(sessionId)) return;
      push(sessionId, makeMessage('user', text));
      await deliver(sessionId, text);
    },
    [sessionId, push, deliver],
  );

  /** Shows agent / support messages of the server session that the chat has not shown yet. */
  const catchUp = useCallback(
    (id: string, server: ChatSession, role: 'agent' | 'support') => {
      update(id, (s) => {
        const all = server.messages.filter((m) => m.role === role);
        const seen = role === 'agent' ? s.agentSeen : s.supportSeen;
        if (all.length <= seen) return s;
        const fresh = all
          .slice(seen)
          .map((m) =>
            role === 'agent'
              ? makeMessage('assistant', m.content)
              : makeMessage('support', m.content, { ticketId: server.ticket_id ?? s.ticketId }),
          );
        const counts = role === 'agent' ? { agentSeen: all.length } : { supportSeen: all.length };
        return { ...s, ...counts, messages: [...s.messages, ...fresh] };
      });
      if (server.outcome === 'handed_off') noteHandoff(id, server.ticket_id);
    },
    [update, noteHandoff],
  );

  /**
   * Retry after a failed request. A timeout or a dropped connection may still have reached the server,
   * so first look at the session: if the reply is there, show it instead of sending the message twice.
   */
  const retry = useCallback(
    async (messageId: string) => {
      if (!sessionId || inFlight.current.has(sessionId)) return;
      const s = sessionsRef.current[sessionId];
      const failed = s?.messages.find((m) => m.id === messageId);
      if (!s || !failed?.retryText) return;
      const id = sessionId;
      update(id, (x) => ({ ...x, messages: x.messages.filter((m) => m.id !== messageId) }));
      try {
        const server = await api.getSession(id);
        if (server.messages.filter((m) => m.role === 'agent').length > s.agentSeen) {
          catchUp(id, server, 'agent');
          return;
        }
      } catch {
        // No session yet (the first message never arrived) or still offline: just send again.
      }
      await deliver(id, failed.retryText);
    },
    [api, sessionId, update, catchUp, deliver],
  );

  // After a handoff, poll GET /api/chat/{session_id} and show new support replies.
  const ticketId = session?.ticketId ?? null;
  useEffect(() => {
    if (!ticketId || !sessionId) return;
    let busy = false;
    const poll = async () => {
      if (busy) return; // the previous request is still running
      busy = true;
      try {
        catchUp(sessionId, await api.getSession(sessionId), 'support');
      } catch {
        // Keep the chat as is and retry on the next tick.
      } finally {
        busy = false;
      }
    };
    const timer = setInterval(poll, pollMs);
    return () => clearInterval(timer);
  }, [api, sessionId, ticketId, pollMs, catchUp]);

  const value = useMemo<SupportState>(
    () => ({
      live: api instanceof HttpAgentApi,
      serverUrl: api instanceof HttpAgentApi ? api.baseUrl : '',
      users,
      usersError,
      reloadUsers,
      userId,
      selectUser,
      newChat,
      sessionId,
      messages: session?.messages ?? placeholder,
      sending: session?.sending ?? false,
      ticketId,
      sendMessage,
      retry,
    }),
    [api, users, usersError, reloadUsers, userId, selectUser, newChat, sessionId, session, placeholder, ticketId, sendMessage, retry],
  );
  return <SupportContext.Provider value={value}>{children}</SupportContext.Provider>;
}

export function useSupport() {
  const ctx = useContext(SupportContext);
  if (!ctx) throw new Error('useSupport must be used inside <SupportProvider>');
  return ctx;
}
