/**
 * Fake billing history the simulated support teammate uses as context in
 * mock mode. With the real API, the backend reads the live Adapty profile.
 */
export type Charge = {
  id: string;
  date: string; // ISO date
  product: string;
  amount: number; // USD
  status: 'paid' | 'refunded';
};

export type AccountContext = {
  userId: string;
  plan: string;
  planPrice: number;
  premiumActive: boolean;
  charges: Charge[];
};

export const MOCK_ACCOUNTS: Record<string, AccountContext> = {
  'user-42': {
    userId: 'user-42',
    plan: 'Premium Monthly',
    planPrice: 9.99,
    premiumActive: false, // paid, but the entitlement didn't activate
    charges: [
      { id: 'ch_301', date: '2026-09-03', product: 'Premium Monthly', amount: 9.99, status: 'paid' },
      { id: 'ch_402', date: '2026-10-03', product: 'Premium Monthly', amount: 9.99, status: 'paid' },
      { id: 'ch_503', date: '2026-11-03', product: 'Premium Monthly', amount: 9.99, status: 'paid' },
      // Duplicate charge on the same day.
      { id: 'ch_504', date: '2026-11-03', product: 'Premium Monthly', amount: 9.99, status: 'paid' },
      // Charged the yearly price by mistake.
      { id: 'ch_505', date: '2026-11-05', product: 'Premium Yearly', amount: 59.99, status: 'paid' },
    ],
  },
};

/** What GET /api/users returns in mock mode. */
export const MOCK_USERS: { customer_user_id: string; badge: 'active' | 'expired' | 'lifetime' | 'none' | 'unknown'; product?: string | null }[] = [
  { customer_user_id: 'user-42', badge: 'active', product: 'Premium Monthly' },
  { customer_user_id: 'user-7', badge: 'none', product: null },
];
