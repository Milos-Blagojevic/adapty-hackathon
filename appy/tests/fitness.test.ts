import { describe, expect, it } from '@jest/globals';
import { computeStreak, countThisWeek } from '../src/lib/fitness';

const DAY = 86_400_000;
const now = new Date(2026, 10, 7, 12).getTime(); // Saturday

describe('fitness stats', () => {
  it('counts consecutive days ending today', () => {
    const h = [0, 1, 2, 4].map((d) => ({ workoutId: 'x', completedAt: now - d * DAY }));
    expect(computeStreak(h, now)).toBe(3);
  });
  it('keeps the streak if the last workout was yesterday', () => {
    expect(computeStreak([{ workoutId: 'x', completedAt: now - DAY }], now)).toBe(1);
  });
  it('counts workouts since Monday', () => {
    const h = [0, 5, 6].map((d) => ({ workoutId: 'x', completedAt: now - d * DAY }));
    expect(countThisWeek(h, now)).toBe(2);
  });
});
