import { useState } from 'react';
import { FlatList, Pressable, StyleSheet, Text, View } from 'react-native';

import { Badge, Button, Card, colors } from '../components/ui';
import { useFitness } from '../lib/fitness';

export default function WorkoutsScreen() {
  const { workouts, history, complete } = useFitness();
  const [openId, setOpenId] = useState<string | null>(null);

  return (
    <FlatList
      data={workouts}
      keyExtractor={(w) => w.id}
      contentContainerStyle={styles.container}
      renderItem={({ item }) => {
        const open = openId === item.id;
        const doneCount = history.filter((h) => h.workoutId === item.id).length;
        return (
          <Card>
            <Pressable onPress={() => setOpenId(open ? null : item.id)} accessibilityRole="button">
              <View style={styles.header}>
                <Text style={styles.title}>{item.title}</Text>
                <Badge label={item.focus} color={colors.info} />
              </View>
              <Text style={styles.muted}>
                {item.minutes} min · {item.level}
                {doneCount > 0 ? ` · done ${doneCount}×` : ''}
              </Text>
            </Pressable>
            {open && (
              <View style={styles.details}>
                {item.exercises.map((e) => (
                  <Text key={e} style={styles.exercise}>
                    • {e}
                  </Text>
                ))}
                <Button label="Mark as done" onPress={() => complete(item.id)} />
              </View>
            )}
          </Card>
        );
      }}
    />
  );
}

const styles = StyleSheet.create({
  container: { padding: 16, gap: 12 },
  header: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  title: { fontSize: 17, fontWeight: '700', color: colors.text, flexShrink: 1 },
  muted: { color: colors.muted, marginTop: 4 },
  details: { marginTop: 12, gap: 6 },
  exercise: { color: colors.text, fontSize: 15 },
});
