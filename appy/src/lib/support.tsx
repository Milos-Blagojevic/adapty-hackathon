import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';

import { createAgentApi, type AgentApi, type UserItem } from './agentApi';
import type { ChatAuthor, ChatMessage } from './types';

export const POLL_INTERVAL_MS = 3000;

type SupportState = {
  users: UserItem[];
  userId: string | null;
  selectUser: (id: string) => void;
  sessionId: string | null;
  messages: ChatMessage[];
  sending: boolean;
  /** Set after a handoff; the app polls the session for support replies. */
  ticketId: string | null;
  sendMessage: (text: string) => Promise<void>;
};

const SupportContext = createContext<SupportState | null>(null);

let msgSeq = 0;
const makeMessage = (author: ChatAuthor, text: string, ticketId?: string | null): ChatMessage => ({
  id: `m${++msgSeq}`,
  author,
  text,
  createdAt: Date.now(),
  ticketId,
});

const welcome = () =>
  makeMessage('assistant', "Hi! I'm your FitBuddy assistant. Ask me about workouts, your subscription, charges or refunds.");

const newSessionId = (userId: string) => `appy-${userId}-${Date.now().toString(36)}`;

export function SupportProvider({
  children,
  api: apiProp,
  pollMs = POLL_INTERVAL_MS,
}: {
  children: ReactNode;
  api?: AgentApi;
  pollMs?: number;
}) {
  const api = useMemo(() => apiProp ?? createAgentApi(), [apiProp]);
  const [users, setUsers] = useState<UserItem[]>([]);
  const [userId, setUserId] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>(() => [welcome()]);
  const [sending, setSending] = useState(false);
  const [ticketId, setTicketId] = useState<string | null>(null);
  // How many "support" messages of the current session are already shown.
  const supportSeen = useRef(0);

  const push = useCallback((m: ChatMessage) => setMessages((prev) => [...prev, m]), []);

  const selectUser = useCallback((id: string) => {
    setUserId(id);
    setSessionId(newSessionId(id));
    setTicketId(null);
    supportSeen.current = 0;
    setMessages([welcome()]);
  }, []);

  // GET /api/users → pick the first user so the chat is usable right away.
  useEffect(() => {
    let cancelled = false;
    api
      .listUsers()
      .then((list) => {
        if (cancelled) return;
        setUsers(list);
        if (list.length > 0) selectUser(list[0].customer_user_id);
        else push(makeMessage('system', 'The support server has no users to chat as.'));
      })
      .catch((e: Error) => {
        if (!cancelled) push(makeMessage('system', `Can't reach the support server: ${e.message}`));
      });
    return () => {
      cancelled = true;
    };
  }, [api, selectUser, push]);

  const sendMessage = useCallback(
    async (raw: string) => {
      const text = raw.trim();
      if (!text || !userId || !sessionId) return;
      push(makeMessage('user', text));
      setSending(true);
      try {
        const result = await api.sendMessage(userId, sessionId, text);
        push(makeMessage('assistant', result.reply));
        if (result.handed_off && result.ticket_id && result.ticket_id !== ticketId) {
          setTicketId(result.ticket_id);
          push(makeMessage('system', `Passed to a support teammate · ticket ${result.ticket_id}`, result.ticket_id));
        }
      } catch (e) {
        push(makeMessage('system', `Something went wrong: ${(e as Error).message}. Please try again.`));
      } finally {
        setSending(false);
      }
    },
    [api, userId, sessionId, ticketId, push],
  );

  // After a handoff, poll GET /api/chat/{session_id} and show new support replies.
  useEffect(() => {
    if (!ticketId || !sessionId) return;
    const poll = async () => {
      try {
        const session = await api.getSession(sessionId);
        const support = session.messages.filter((m) => m.role === 'support');
        for (const m of support.slice(supportSeen.current)) push(makeMessage('support', m.content, ticketId));
        supportSeen.current = support.length;
      } catch {
        // Keep the chat as is and retry on the next tick.
      }
    };
    const id = setInterval(poll, pollMs);
    return () => clearInterval(id);
  }, [api, sessionId, ticketId, pollMs, push]);

  const value = useMemo(
    () => ({ users, userId, selectUser, sessionId, messages, sending, ticketId, sendMessage }),
    [users, userId, selectUser, sessionId, messages, sending, ticketId, sendMessage],
  );
  return <SupportContext.Provider value={value}>{children}</SupportContext.Provider>;
}

export function useSupport() {
  const ctx = useContext(SupportContext);
  if (!ctx) throw new Error('useSupport must be used inside <SupportProvider>');
  return ctx;
}
