import type { AccountContext, Charge } from '../data/mockAccounts';

/**
 * Stand-in for "agent 2": classifies the user's issue and answers it using
 * the account context (billing history). Only used by MockAgentApi.
 */
export type IssueType = 'duplicate_charge' | 'wrong_amount' | 'refund' | 'not_activated' | 'other';

const has = (text: string, words: string[]) => words.some((w) => text.includes(w));

export function classifyIssue(message: string): IssueType {
  const t = message.toLowerCase();
  if (has(t, ['twice', 'double', 'duplicate', 'two times', 'charged 2'])) return 'duplicate_charge';
  if (has(t, ['wrong amount', 'wrong charge', 'wrong price', 'overcharged', 'too much', 'yearly', 'more than'])) {
    return 'wrong_amount';
  }
  if (has(t, ['not activated', "didn't activate", 'not working', 'no premium', 'still free', 'not unlocked', "can't access"])) {
    return 'not_activated';
  }
  if (has(t, ['refund', 'money back', 'charge', 'charged', 'payment', 'billing'])) return 'refund';
  return 'other';
}

const usd = (n: number) => `$${n.toFixed(2)}`;
const fmt = (c: Charge) => `${c.product} ${usd(c.amount)} on ${c.date} (${c.id})`;

function findDuplicates(charges: Charge[]) {
  const seen = new Map<string, Charge>();
  const dups: Charge[] = [];
  for (const c of charges) {
    if (c.status !== 'paid') continue;
    const key = `${c.date}|${c.product}|${c.amount}`;
    if (seen.has(key)) dups.push(c);
    else seen.set(key, c);
  }
  return dups;
}

/** Charges that don't match the user's plan price. */
function findWrongAmounts(ctx: AccountContext) {
  return ctx.charges.filter((c) => c.status === 'paid' && c.amount !== ctx.planPrice);
}

/**
 * Resolves the issue against the context. Refunds are applied to the
 * context so a second ticket about the same charge sees it as refunded.
 */
export function resolveIssue(message: string, ctx: AccountContext | undefined): string {
  if (!ctx) {
    return "I couldn't find a billing account for your user. Could you send the email you used to purchase, or a receipt screenshot?";
  }
  const issue = classifyIssue(message);
  const refund = (charges: Charge[]) => charges.forEach((c) => (c.status = 'refunded'));

  switch (issue) {
    case 'duplicate_charge': {
      const dups = findDuplicates(ctx.charges);
      if (dups.length === 0) {
        return "I checked your billing history and don't see a duplicate charge. Sometimes a pending authorization looks like a second charge and drops off within 3–5 days. If it's still there after that, send us a bank statement screenshot.";
      }
      refund(dups);
      const total = dups.reduce((s, c) => s + c.amount, 0);
      return `You're right — you were charged twice. I've refunded the duplicate: ${dups.map(fmt).join(', ')}. ${usd(total)} will be back on your card in 5–10 business days. Sorry about that!`;
    }
    case 'wrong_amount': {
      const wrong = findWrongAmounts(ctx);
      if (wrong.length === 0) {
        return `All your charges match your plan (${ctx.plan}, ${usd(ctx.planPrice)}). Could you tell me which charge looks wrong (date and amount)?`;
      }
      refund(wrong);
      return `I found a charge that doesn't match your ${ctx.plan} plan (${usd(ctx.planPrice)}): ${wrong.map(fmt).join(', ')}. I've refunded it in full; it should appear in 5–10 business days. Your ${ctx.plan} plan stays as it is.`;
    }
    case 'not_activated': {
      ctx.premiumActive = true;
      return `I can see your payment for ${ctx.plan}, but Premium wasn't activated on your account. I've activated it now — restart the app and you should see all Premium workouts unlocked.`;
    }
    case 'refund': {
      const last = [...ctx.charges].reverse().find((c) => c.status === 'paid');
      if (!last) return 'All your charges have already been refunded. Is there anything else I can help with?';
      refund([last]);
      return `I've refunded your most recent charge: ${fmt(last)}. It takes 5–10 business days to reach your card. If you meant a different charge, reply with its date and amount.`;
    }
    default:
      return `Thanks for reaching out! I've looked into "${message}" and passed the details to our team. We'll follow up right here in the chat.`;
  }
}
