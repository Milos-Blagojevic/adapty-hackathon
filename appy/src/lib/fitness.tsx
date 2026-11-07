import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';

import { WORKOUTS, type Workout } from '../data/workouts';

export type CompletedWorkout = { workoutId: string; completedAt: number };

const DAY = 24 * 60 * 60 * 1000;

const dayKey = (ts: number) => {
  const d = new Date(ts);
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
};

/** Consecutive days (ending today or yesterday) with at least one workout. */
export function computeStreak(history: CompletedWorkout[], now = Date.now()): number {
  const days = new Set(history.map((h) => dayKey(h.completedAt)));
  let cursor = days.has(dayKey(now)) ? now : now - DAY;
  let streak = 0;
  while (days.has(dayKey(cursor))) {
    streak++;
    cursor -= DAY;
  }
  return streak;
}

/** Workouts completed since Monday 00:00 of the current week. */
export function countThisWeek(history: CompletedWorkout[], now = Date.now()): number {
  const d = new Date(now);
  const monday = new Date(d.getFullYear(), d.getMonth(), d.getDate() - ((d.getDay() + 6) % 7)).getTime();
  return history.filter((h) => h.completedAt >= monday).length;
}

type FitnessState = {
  workouts: Workout[];
  history: CompletedWorkout[];
  weeklyGoal: number;
  setWeeklyGoal: (n: number) => void;
  complete: (workoutId: string) => void;
  thisWeek: number;
  streak: number;
  totalMinutes: number;
};

const FitnessContext = createContext<FitnessState | null>(null);

export function FitnessProvider({ children }: { children: ReactNode }) {
  const [history, setHistory] = useState<CompletedWorkout[]>([]);
  const [weeklyGoal, setGoal] = useState(3);

  const complete = useCallback(
    (workoutId: string) => setHistory((h) => [...h, { workoutId, completedAt: Date.now() }]),
    [],
  );
  const setWeeklyGoal = useCallback((n: number) => setGoal(Math.max(1, Math.min(14, n))), []);

  const value = useMemo(() => {
    const totalMinutes = history.reduce(
      (sum, h) => sum + (WORKOUTS.find((w) => w.id === h.workoutId)?.minutes ?? 0),
      0,
    );
    return {
      workouts: WORKOUTS,
      history,
      weeklyGoal,
      setWeeklyGoal,
      complete,
      thisWeek: countThisWeek(history),
      streak: computeStreak(history),
      totalMinutes,
    };
  }, [history, weeklyGoal, setWeeklyGoal, complete]);

  return <FitnessContext.Provider value={value}>{children}</FitnessContext.Provider>;
}

export function useFitness() {
  const ctx = useContext(FitnessContext);
  if (!ctx) throw new Error('useFitness must be used inside <FitnessProvider>');
  return ctx;
}
