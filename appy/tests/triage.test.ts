import { describe, expect, it } from '@jest/globals';
import { TRIAGE_DB } from '../src/data/triageDb';
import { triage } from '../src/lib/triage';

describe('triage (can help?)', () => {
  it.each([
    ['I forgot my password, how do I reset it?', 'reset-password'],
    ['How can I log a workout?', 'log-workout'],
    ['I want to cancel my premium subscription', 'cancel-subscription'],
    ['how does the streak counter work', 'streak'],
  ])('answers "%s" from the triage DB', (msg, id) => {
    const r = triage(msg, TRIAGE_DB);
    expect(r.canHelp).toBe(true);
    if (r.canHelp) expect(r.entryId).toBe(id);
  });

  it.each([
    'I was charged twice this month and need a refund',
    'I want to cancel my subscription and get a refund',
    'The app crashes when I open my profile photo',
    'hello',
    '',
  ])('cannot help with "%s" -> ticket', (msg) => {
    expect(triage(msg, TRIAGE_DB).canHelp).toBe(false);
  });
});
