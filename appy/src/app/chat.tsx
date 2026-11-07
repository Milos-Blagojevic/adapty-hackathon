import { useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  ScrollView,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

import { colors } from '../components/ui';
import { TRIAGE_DB } from '../data/triageDb';
import { useSupport } from '../lib/support';
import type { ChatMessage } from '../lib/types';

const SUGGESTIONS = [
  TRIAGE_DB[0].question,
  TRIAGE_DB[1].question,
  // Billing problems: these always go to a support agent.
  'I was charged twice this month',
  'I want a refund for a wrong charge',
  'I paid but Premium is not activated',
];

function Bubble({ message }: { message: ChatMessage }) {
  if (message.author === 'system') {
    return <Text style={styles.system}>{message.text}</Text>;
  }
  const mine = message.author === 'user';
  const support = message.author === 'support';
  return (
    <View style={[styles.bubbleWrap, mine && { alignItems: 'flex-end' }]}>
      {support && <Text style={styles.author}>Support teammate · {message.ticketId}</Text>}
      <View style={[styles.bubble, mine ? styles.mine : support ? styles.agent : styles.bot]}>
        <Text style={[styles.text, mine && { color: '#fff' }]}>{message.text}</Text>
      </View>
    </View>
  );
}

export default function ChatScreen() {
  const { messages, sending, sendMessage, users, userId, selectUser } = useSupport();
  const ready = !!userId && !sending;
  const [draft, setDraft] = useState('');
  const list = useRef<FlatList<ChatMessage>>(null);

  useEffect(() => {
    const t = setTimeout(() => list.current?.scrollToEnd({ animated: true }), 50);
    return () => clearTimeout(t);
  }, [messages.length]);

  const send = (text: string) => {
    if (!text.trim() || !ready) return;
    setDraft('');
    sendMessage(text);
  };

  return (
    <KeyboardAvoidingView
      style={{ flex: 1 }}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      keyboardVerticalOffset={90}
    >
      {users.length > 0 && (
        <View style={styles.userBar}>
          <Text style={styles.userLabel}>Chat as</Text>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 6 }}>
            {users.map((u) => {
              const active = u.customer_user_id === userId;
              return (
                <Pressable
                  key={u.customer_user_id}
                  accessibilityRole="button"
                  accessibilityState={{ selected: active }}
                  onPress={() => !active && selectUser(u.customer_user_id)}
                  style={[styles.userChip, active && styles.userChipActive]}
                >
                  <Text style={[styles.userChipText, active && { color: '#fff' }]}>
                    {u.customer_user_id} · {u.badge}
                  </Text>
                </Pressable>
              );
            })}
          </ScrollView>
        </View>
      )}

      <FlatList
        ref={list}
        data={messages}
        keyExtractor={(m) => m.id}
        renderItem={({ item }) => <Bubble message={item} />}
        contentContainerStyle={styles.list}
        ListFooterComponent={sending ? <ActivityIndicator color={colors.primary} style={{ marginTop: 8 }} /> : undefined}
      />

      {messages.length <= 1 && (
        <View style={styles.suggestions}>
          {SUGGESTIONS.map((q) => (
            <Pressable key={q} style={styles.chip} onPress={() => send(q)}>
              <Text style={styles.chipText}>{q}</Text>
            </Pressable>
          ))}
        </View>
      )}

      <View style={styles.inputRow}>
        <TextInput
          value={draft}
          onChangeText={setDraft}
          placeholder="Ask anything…"
          placeholderTextColor={colors.muted}
          style={styles.input}
          onSubmitEditing={() => send(draft)}
          returnKeyType="send"
          accessibilityLabel="Message"
        />
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Send"
          onPress={() => send(draft)}
          disabled={!draft.trim() || !ready}
          style={[styles.send, (!draft.trim() || !ready) && { opacity: 0.5 }]}
        >
          <Text style={styles.sendText}>Send</Text>
        </Pressable>
      </View>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  list: { padding: 16, gap: 10 },
  userBar: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingHorizontal: 16,
    paddingVertical: 8,
    borderBottomWidth: 1,
    borderColor: colors.border,
  },
  userLabel: { color: colors.muted, fontSize: 13, fontWeight: '600', flexShrink: 0 },
  userChip: { borderRadius: 999, paddingVertical: 5, paddingHorizontal: 10, backgroundColor: colors.border },
  userChipActive: { backgroundColor: colors.text },
  userChipText: { fontSize: 13, color: colors.text, fontWeight: '600' },
  bubbleWrap: { alignItems: 'flex-start' },
  bubble: { maxWidth: '82%', borderRadius: 16, paddingVertical: 10, paddingHorizontal: 14 },
  mine: { backgroundColor: colors.primary, borderBottomRightRadius: 4 },
  bot: { backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, borderBottomLeftRadius: 4 },
  agent: { backgroundColor: '#E8F0FF', borderBottomLeftRadius: 4 },
  author: { fontSize: 12, color: colors.info, fontWeight: '700', marginBottom: 2 },
  text: { fontSize: 15, color: colors.text, lineHeight: 21 },
  system: { textAlign: 'center', color: colors.muted, fontSize: 13, paddingHorizontal: 24 },
  suggestions: { paddingHorizontal: 16, gap: 8, marginBottom: 8 },
  chip: {
    borderWidth: 1,
    borderColor: colors.primary,
    borderRadius: 999,
    paddingVertical: 8,
    paddingHorizontal: 14,
    alignSelf: 'flex-start',
  },
  chipText: { color: colors.primary, fontWeight: '600' },
  inputRow: {
    flexDirection: 'row',
    gap: 8,
    padding: 12,
    borderTopWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.card,
  },
  input: {
    flex: 1,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 12,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 15,
    color: colors.text,
  },
  send: { backgroundColor: colors.primary, borderRadius: 12, paddingHorizontal: 16, justifyContent: 'center' },
  sendText: { color: '#fff', fontWeight: '700' },
});
