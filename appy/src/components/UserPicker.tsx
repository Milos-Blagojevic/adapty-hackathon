import { useState } from 'react';
import { FlatList, Modal, Pressable, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import type { UserItem } from '../lib/agentApi';
import { colors } from './ui';

const BADGE: Record<UserItem['badge'], { label: string; color: string }> = {
  active: { label: 'active', color: colors.success },
  expired: { label: 'expired', color: colors.warning },
  lifetime: { label: 'lifetime', color: colors.info },
  none: { label: 'no plan', color: colors.muted },
  unknown: { label: 'not loaded', color: colors.muted },
};
const ORDER: UserItem['badge'][] = ['active', 'expired', 'lifetime', 'none', 'unknown'];

/**
 * The part of an id that tells users apart: seeded ids share a long prefix ("app_123456_00428"),
 * which would push the distinguishing digits out of the narrow "Chat as" bar.
 */
export function sharedPrefix(ids: string[]): string {
  if (ids.length < 2) return '';
  let p = ids[0];
  for (const id of ids) while (!id.startsWith(p)) p = p.slice(0, -1);
  const cut = Math.max(p.lastIndexOf('_'), p.lastIndexOf('-'), p.lastIndexOf('.'), p.lastIndexOf(':'));
  p = p.slice(0, cut + 1);
  return ids.every((id) => id.length > p.length) ? p : '';
}

export function UserBadge({ badge }: { badge: UserItem['badge'] }) {
  const b = BADGE[badge] ?? BADGE.unknown;
  return (
    <View style={[styles.badge, { backgroundColor: b.color + '22' }]}>
      <Text style={[styles.badgeText, { color: b.color }]}>{b.label}</Text>
    </View>
  );
}

/** "Chat as": the current demo user; tap to pick another one from GET /api/users. */
export function UserPicker({
  users,
  userId,
  onSelect,
}: {
  users: UserItem[];
  userId: string | null;
  onSelect: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const insets = useSafeAreaInsets();
  const selected = users.find((u) => u.customer_user_id === userId);
  const prefix = sharedPrefix(users.map((u) => u.customer_user_id));
  const sorted = [...users].sort(
    (a, b) => ORDER.indexOf(a.badge) - ORDER.indexOf(b.badge) || a.customer_user_id.localeCompare(b.customer_user_id),
  );

  return (
    <>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel="Chat as"
        onPress={() => setOpen(true)}
        style={({ pressed }) => [styles.current, pressed && { opacity: 0.6 }]}
      >
        <Text style={styles.currentId} numberOfLines={1} ellipsizeMode="middle">
          {selected ? selected.customer_user_id.slice(prefix.length) : 'Pick a user'}
        </Text>
        {selected && <UserBadge badge={selected.badge} />}
        <Text style={styles.chevron}>▾</Text>
      </Pressable>

      <Modal visible={open} transparent animationType="slide" onRequestClose={() => setOpen(false)}>
        <Pressable style={styles.backdrop} onPress={() => setOpen(false)} accessibilityLabel="Close" />
        <View style={[styles.sheet, { paddingBottom: insets.bottom + 8 }]}>
          <View style={styles.sheetHeader}>
            <Text style={styles.sheetTitle}>Chat as</Text>
            <Pressable onPress={() => setOpen(false)} hitSlop={12} accessibilityRole="button">
              <Text style={styles.done}>Done</Text>
            </Pressable>
          </View>
          <FlatList
            data={sorted}
            keyExtractor={(u) => u.customer_user_id}
            renderItem={({ item }) => {
              const active = item.customer_user_id === userId;
              return (
                <Pressable
                  accessibilityRole="button"
                  accessibilityState={{ selected: active }}
                  onPress={() => {
                    setOpen(false);
                    if (!active) onSelect(item.customer_user_id);
                  }}
                  style={({ pressed }) => [styles.row, active && styles.rowActive, pressed && { opacity: 0.6 }]}
                >
                  <View style={{ flex: 1 }}>
                    <Text style={styles.rowId}>{item.customer_user_id}</Text>
                    {!!item.product && <Text style={styles.rowProduct}>{item.product}</Text>}
                  </View>
                  <UserBadge badge={item.badge} />
                </Pressable>
              );
            }}
          />
        </View>
      </Modal>
    </>
  );
}

const styles = StyleSheet.create({
  current: {
    flexShrink: 1,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    borderRadius: 999,
    paddingVertical: 5,
    paddingLeft: 12,
    paddingRight: 10,
    backgroundColor: colors.card,
    borderWidth: 1,
    borderColor: colors.border,
  },
  currentId: { flexShrink: 1, fontSize: 13, color: colors.text, fontWeight: '600' },
  chevron: { color: colors.muted, fontSize: 12 },
  badge: { borderRadius: 999, paddingHorizontal: 8, paddingVertical: 2 },
  badgeText: { fontSize: 12, fontWeight: '700' },
  backdrop: { flex: 1, backgroundColor: '#00000055' },
  sheet: {
    maxHeight: '70%',
    backgroundColor: colors.card,
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
  },
  sheetHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: 16,
    borderBottomWidth: 1,
    borderColor: colors.border,
  },
  sheetTitle: { fontSize: 17, fontWeight: '700', color: colors.text },
  done: { fontSize: 15, fontWeight: '700', color: colors.primary },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    paddingVertical: 12,
    paddingHorizontal: 16,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
  },
  rowActive: { backgroundColor: colors.primarySoft },
  rowId: { fontSize: 15, color: colors.text, fontWeight: '600' },
  rowProduct: { fontSize: 13, color: colors.muted, marginTop: 2 },
});
