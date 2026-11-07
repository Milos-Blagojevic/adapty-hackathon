import { router } from 'expo-router';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import { Button, Card, colors } from '../components/ui';
import { useFitness } from '../lib/fitness';

export default function HomeScreen() {
  const { weeklyGoal, setWeeklyGoal, thisWeek, streak, totalMinutes, workouts } = useFitness();
  const progress = Math.min(1, thisWeek / weeklyGoal);
  const suggestion = workouts[thisWeek % workouts.length];

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <Text style={styles.hello}>Let&apos;s move today 💪</Text>

      <Card>
        <Text style={styles.label}>Weekly goal</Text>
        <View style={styles.goalRow}>
          <Text style={styles.big}>
            {thisWeek}/{weeklyGoal}
          </Text>
          <View style={styles.stepper}>
            <Pressable accessibilityLabel="Decrease goal" style={styles.step} onPress={() => setWeeklyGoal(weeklyGoal - 1)}>
              <Text style={styles.stepText}>−</Text>
            </Pressable>
            <Pressable accessibilityLabel="Increase goal" style={styles.step} onPress={() => setWeeklyGoal(weeklyGoal + 1)}>
              <Text style={styles.stepText}>+</Text>
            </Pressable>
          </View>
        </View>
        <View style={styles.track}>
          <View style={[styles.fill, { width: `${progress * 100}%` }]} />
        </View>
        <Text style={styles.muted}>
          {progress >= 1 ? 'Goal reached — great job!' : `${weeklyGoal - thisWeek} workout(s) to go this week`}
        </Text>
      </Card>

      <View style={styles.statsRow}>
        <Card style={styles.stat}>
          <Text style={styles.big}>🔥 {streak}</Text>
          <Text style={styles.muted}>day streak</Text>
        </Card>
        <Card style={styles.stat}>
          <Text style={styles.big}>{totalMinutes}</Text>
          <Text style={styles.muted}>minutes trained</Text>
        </Card>
      </View>

      <Card>
        <Text style={styles.label}>Suggested next</Text>
        <Text style={styles.title}>{suggestion.title}</Text>
        <Text style={[styles.muted, { marginBottom: 12 }]}>
          {suggestion.minutes} min · {suggestion.focus} · {suggestion.level}
        </Text>
        <Button label="Go to workouts" onPress={() => router.navigate('/workouts')} />
      </Card>

      <Card>
        <Text style={styles.label}>Need help?</Text>
        <Text style={[styles.muted, { marginBottom: 12 }]}>
          Our assistant answers common questions instantly and hands anything else to a support agent.
        </Text>
        <Button label="Open assistant" variant="ghost" onPress={() => router.navigate('/chat')} />
      </Card>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { padding: 16, gap: 12 },
  hello: { fontSize: 24, fontWeight: '800', color: colors.text, marginBottom: 4 },
  label: { fontSize: 13, fontWeight: '700', color: colors.muted, textTransform: 'uppercase', marginBottom: 6 },
  big: { fontSize: 28, fontWeight: '800', color: colors.text },
  title: { fontSize: 18, fontWeight: '700', color: colors.text },
  muted: { color: colors.muted, marginTop: 4 },
  goalRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  stepper: { flexDirection: 'row', gap: 8 },
  step: {
    width: 36,
    height: 36,
    borderRadius: 18,
    backgroundColor: colors.primarySoft,
    alignItems: 'center',
    justifyContent: 'center',
  },
  stepText: { fontSize: 20, color: colors.primary, fontWeight: '700' },
  track: { height: 10, borderRadius: 5, backgroundColor: colors.border, marginTop: 12, overflow: 'hidden' },
  fill: { height: '100%', backgroundColor: colors.primary },
  statsRow: { flexDirection: 'row', gap: 12 },
  stat: { flex: 1 },
});
