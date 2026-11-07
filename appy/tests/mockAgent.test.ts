import { describe, expect, it } from '@jest/globals';

import { MOCK_ACCOUNTS } from '../src/data/mockAccounts';
import { classifyIssue, resolveIssue } from '../src/lib/mockAgent';

const ctx = () => structuredClone(MOCK_ACCOUNTS['user-42']);

describe('mock agent 2', () => {
  it.each([
    ['I was charged twice this month', 'duplicate_charge'],
    ['I want a refund for a wrong charge', 'wrong_amount'],
    ['You charged me the yearly price', 'wrong_amount'],
    ['I paid but Premium is not activated', 'not_activated'],
    ['Please give me my money back', 'refund'],
    ['The app crashes on start', 'other'],
  ])('classifies "%s" as %s', (msg, type) => {
    expect(classifyIssue(msg)).toBe(type);
  });

  it('refunds the charge that does not match the plan price', () => {
    const c = ctx();
    const reply = resolveIssue('wrong charge on my card', c);
    expect(reply).toContain('ch_505');
    expect(reply).toContain('$59.99');
    expect(c.charges.find((x) => x.id === 'ch_505')?.status).toBe('refunded');
  });

  it('activates premium when payment went through', () => {
    const c = ctx();
    expect(resolveIssue('Premium not activated', c)).toMatch(/activated it now/);
    expect(c.premiumActive).toBe(true);
  });
});
