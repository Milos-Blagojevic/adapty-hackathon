import type { TriageEntry } from '../lib/types';

/**
 * Local copy of the TRIAGE DB: questions the chatbot can answer on its own.
 * Anything that doesn't match becomes a support ticket for agent 2.
 */
export const TRIAGE_DB: TriageEntry[] = [
  {
    id: 'reset-password',
    question: 'How do I reset my password?',
    keywords: ['reset', 'password', 'forgot', 'login', 'sign'],
    answer: 'Go to Profile → Account → "Reset password". We will email you a reset link within a minute.',
  },
  {
    id: 'log-workout',
    question: 'How do I log a workout?',
    keywords: ['log', 'workout', 'record', 'track', 'add', 'session'],
    answer: 'Open the Workouts tab, pick a workout and tap "Mark as done". It will count towards your weekly goal and streak.',
  },
  {
    id: 'weekly-goal',
    question: 'How do I change my weekly goal?',
    keywords: ['weekly', 'goal', 'target', 'change', 'workouts', 'week'],
    answer: 'On the Home tab tap the "−" / "+" buttons next to your weekly goal. Most people start with 3 workouts a week.',
  },
  {
    id: 'streak',
    question: 'How does the streak work?',
    keywords: ['streak', 'days', 'lost', 'counter', 'row'],
    answer: 'Your streak counts consecutive days with at least one completed workout. Missing a full day resets it to 0.',
  },
  {
    id: 'warmup',
    question: 'Should I warm up before training?',
    keywords: ['warm', 'warmup', 'stretch', 'before', 'injury'],
    answer: 'Yes — 5–10 minutes of light cardio plus dynamic stretches (leg swings, arm circles) lowers injury risk.',
  },
  {
    id: 'rest-days',
    question: 'How many rest days do I need?',
    keywords: ['rest', 'recovery', 'day', 'off', 'sore', 'soreness'],
    answer: 'Beginners usually do best with 1–2 rest days per week. If a muscle is still very sore, train a different muscle group.',
  },
  {
    id: 'cancel-subscription',
    question: 'How do I cancel my subscription?',
    keywords: ['cancel', 'subscription', 'unsubscribe', 'premium', 'plan', 'stop'],
    answer: 'Subscriptions are managed by the store: iOS → Settings → Apple ID → Subscriptions; Android → Play Store → Payments & subscriptions.',
  },
];
