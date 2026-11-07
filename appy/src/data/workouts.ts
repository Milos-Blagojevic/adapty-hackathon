export type Workout = {
  id: string;
  title: string;
  minutes: number;
  level: 'Beginner' | 'Intermediate' | 'Advanced';
  focus: string;
  exercises: string[];
};

export const WORKOUTS: Workout[] = [
  {
    id: 'full-body',
    title: 'Full Body Starter',
    minutes: 25,
    level: 'Beginner',
    focus: 'Strength',
    exercises: ['10 squats', '8 push-ups', '12 glute bridges', '30s plank', 'Repeat ×3'],
  },
  {
    id: 'hiit',
    title: 'HIIT Burner',
    minutes: 20,
    level: 'Intermediate',
    focus: 'Cardio',
    exercises: ['40s jumping jacks', '40s mountain climbers', '40s burpees', '20s rest', 'Repeat ×5'],
  },
  {
    id: 'core',
    title: 'Core Crusher',
    minutes: 15,
    level: 'Intermediate',
    focus: 'Core',
    exercises: ['20 crunches', '30s side plank each side', '15 leg raises', '20 bicycle crunches', 'Repeat ×3'],
  },
  {
    id: 'mobility',
    title: 'Mobility Flow',
    minutes: 15,
    level: 'Beginner',
    focus: 'Recovery',
    exercises: ["1 min cat-cow", "1 min child's pose", '1 min hip openers', '1 min thoracic twists', 'Repeat ×2'],
  },
  {
    id: 'legs',
    title: 'Leg Day Power',
    minutes: 35,
    level: 'Advanced',
    focus: 'Strength',
    exercises: ['12 jump squats', '10 lunges each leg', '15 calf raises', '10 Bulgarian split squats each leg', 'Repeat ×4'],
  },
];
