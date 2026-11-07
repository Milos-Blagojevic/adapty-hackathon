export type TriageEntry = {
  id: string;
  question: string;
  keywords: string[];
  answer: string;
};

export type TriageResult =
  | { canHelp: true; entryId: string; answer: string; score: number }
  | { canHelp: false; score: number };

/**
 * assistant = the Level 2 agent's reply to POST /chat
 * support   = a teammate's reply after a handoff (role "support" in GET /api/chat/{session_id})
 */
export type ChatAuthor = 'user' | 'assistant' | 'support' | 'system';

export type ChatMessage = {
  id: string;
  author: ChatAuthor;
  text: string;
  createdAt: number;
  ticketId?: string | null;
};
