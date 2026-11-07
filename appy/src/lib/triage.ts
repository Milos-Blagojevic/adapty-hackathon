import type { TriageEntry, TriageResult } from './types';

const STOP_WORDS = new Set([
  'a', 'an', 'the', 'i', 'my', 'me', 'to', 'do', 'does', 'is', 'it', 'how', 'what', 'can', 'of',
  'in', 'on', 'for', 'and', 'or', 'you', 'your', 'should', 'with', 'be', 'are', 'am', 'this', 'that',
]);

export function tokenize(text: string): string[] {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, ' ')
    .split(/\s+/)
    .filter((w) => w.length > 1 && !STOP_WORDS.has(w));
}

/**
 * Account-specific billing problems always go to agent 2, which can see the
 * user's charges and issue refunds; the FAQ must never answer them.
 */
export const ESCALATE_KEYWORDS = new Set([
  'refund', 'refunds', 'charged', 'charge', 'charges', 'twice', 'double', 'duplicate',
  'overcharged', 'billing', 'billed', 'payment', 'paid', 'money',
]);

/** Minimum share of message tokens that must hit an entry's keywords. */
export const MATCH_THRESHOLD = 0.34;
/** At least this many keyword hits are required, so one generic word isn't enough. */
export const MIN_HITS = 2;

/**
 * "Can help?" step: score the message against every TRIAGE DB entry and
 * return the best answer, or canHelp=false so the caller creates a ticket.
 */
export function triage(message: string, db: TriageEntry[]): TriageResult {
  const tokens = tokenize(message);
  if (tokens.length === 0) return { canHelp: false, score: 0 };
  if (tokens.some((t) => ESCALATE_KEYWORDS.has(t))) return { canHelp: false, score: 0 };

  let best: { entry: TriageEntry; hits: number; score: number } | null = null;
  for (const entry of db) {
    const keywords = new Set(entry.keywords);
    const hits = tokens.filter((t) => keywords.has(t)).length;
    const score = hits / tokens.length;
    if (!best || hits > best.hits || (hits === best.hits && score > best.score)) {
      best = { entry, hits, score };
    }
  }

  if (best && best.hits >= Math.min(MIN_HITS, tokens.length) && best.score >= MATCH_THRESHOLD) {
    return { canHelp: true, entryId: best.entry.id, answer: best.entry.answer, score: best.score };
  }
  return { canHelp: false, score: best?.score ?? 0 };
}
