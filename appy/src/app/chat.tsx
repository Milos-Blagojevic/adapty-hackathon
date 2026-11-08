import { HeaderHeightContext } from 'expo-router/react-navigation';
import { useContext, useRef, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  KeyboardAvoidingView,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

import { RichText } from '../components/RichText';
import { colors } from '../components/ui';
import { UserPicker } from '../components/UserPicker';
import { TRIAGE_DB } from '../data/triageDb';
import { useSupport } from '../lib/support';
import type { ChatMessage } from '../lib/types';

/** Offline mock: FAQ answers plus billing problems that go to a teammate. */
const MOCK_SUGGESTIONS = [
  TRIAGE_DB[0].question,
  TRIAGE_DB[1].question,
  'I was charged twice this month',
  'I want a refund for a wrong charge',
  'I paid but Premium is not activated',
];

/** Real backend: the agent answers from the user's subscription, so ask about that. */
const LIVE_SUGGESTIONS = [
  'When does my plan renew?',
  'How do I cancel my subscription?',
  'I paid but Premium is not activated',
  'I want a refund',
];

function Bubble({ message, onRetry }: { message: ChatMessage; onRetry: (id: string) => void }) {
  if (message.author === 'system') {
    return (
      <View style={styles.systemWrap}>
        <Text style={[styles.system, message.error && styles.systemError]}>{message.text}</Text>
        {!!message.retryText && (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Try again"
            onPress={() => onRetry(message.id)}
            style={({ pressed }) => [styles.retry, pressed && { opacity: 0.6 }]}
          >
            <Text style={styles.retryText}>Try again</Text>
          </Pressable>
        )}
      </View>
    );
  }
  const mine = message.author === 'user';
  const support = message.author === 'support';
  return (
    <View style={[styles.bubbleWrap, mine && { alignItems: 'flex-end' }]}>
      {support && <Text style={styles.author}>Support teammate · {message.ticketId}</Text>}
      <View style={[styles.bubble, mine ? styles.mine : support ? styles.agent : styles.bot]}>
        {mine ? (
          <Text style={[styles.text, { color: '#fff' }]}>{message.text}</Text>
        ) : (
          <RichText text={message.text} style={styles.text} />
        )}
      </View>
    </View>
  );
}

function Typing() {
  return (
    <View style={styles.bubbleWrap} accessibilityLabel="Assistant is typing">
      <View style={[styles.bubble, styles.bot, styles.typing]}>
        <ActivityIndicator size="small" color={colors.primary} />
        <Text style={styles.typingText}>Assistant is typing…</Text>
      </View>
    </View>
  );
}

export default function ChatScreen() {
  const {
    live,
    serverUrl,
    messages,
    sending,
    sendMessage,
    retry,
    users,
    usersError,
    reloadUsers,
    userId,
    selectUser,
    newChat,
  } = useSupport();
  const ready = !!userId && !sending;
  const [draft, setDraft] = useState('');
  const list = useRef<FlatList<ChatMessage>>(null);
  // Header height incl. the status bar, so the keyboard offset is right on every phone (0 outside a navigator).
  const headerHeight = useContext(HeaderHeightContext) ?? 0;
  const started = messages.some((m) => m.author === 'user');
  const suggestions = live ? LIVE_SUGGESTIONS : MOCK_SUGGESTIONS;

  const send = (text: string) => {
    if (!text.trim() || !ready) return;
    setDraft('');
    sendMessage(text);
  };

  return (
    <KeyboardAvoidingView style={{ flex: 1 }} behavior="padding" keyboardVerticalOffset={headerHeight}>
      <View style={styles.userBar}>
        <Text style={styles.userLabel}>Chat as</Text>
        {users.length > 0 ? (
          <>
            <UserPicker users={users} userId={userId} onSelect={selectUser} />
            <View style={{ flex: 1 }} />
            {started && (
              <Pressable accessibilityRole="button" onPress={newChat} hitSlop={8} disabled={sending}>
                <Text style={[styles.newChat, sending && { opacity: 0.5 }]}>New chat</Text>
              </Pressable>
            )}
          </>
        ) : (
          <Text style={styles.userLabel}>{usersError ? '—' : 'loading users…'}</Text>
        )}
      </View>

      {usersError && (
        <View style={styles.banner}>
          <Text style={styles.bannerText}>
            {usersError}
            {serverUrl ? `\nServer: ${serverUrl}` : ''}
          </Text>
          <Pressable accessibilityRole="button" onPress={reloadUsers} hitSlop={8}>
            <Text style={styles.retryText}>Retry now</Text>
          </Pressable>
        </View>
      )}

      <FlatList
        ref={list}
        data={messages}
        keyExtractor={(m) => m.id}
        renderItem={({ item }) => <Bubble message={item} onRetry={retry} />}
        contentContainerStyle={styles.list}
        keyboardShouldPersistTaps="handled"
        keyboardDismissMode="interactive"
        // The new content height, not scrollToEnd: its idea of the end can be one message behind.
        onContentSizeChange={(_w, h) => list.current?.scrollToOffset({ offset: h, animated: true })}
        ListFooterComponent={sending ? <Typing /> : undefined}
      />

      {!started && userId && (
        <View style={styles.suggestions}>
          {suggestions.map((q) => (
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
          submitBehavior="submit"
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
  newChat: { color: colors.primary, fontSize: 13, fontWeight: '700' },
  banner: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    margin: 12,
    marginBottom: 0,
    padding: 12,
    borderRadius: 12,
    backgroundColor: '#FFF4E0',
  },
  bannerText: { flex: 1, color: colors.text, fontSize: 13, lineHeight: 18 },
  bubbleWrap: { alignItems: 'flex-start' },
  bubble: { maxWidth: '82%', borderRadius: 16, paddingVertical: 10, paddingHorizontal: 14 },
  mine: { backgroundColor: colors.primary, borderBottomRightRadius: 4 },
  bot: { backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, borderBottomLeftRadius: 4 },
  agent: { backgroundColor: '#E8F0FF', borderBottomLeftRadius: 4 },
  author: { fontSize: 12, color: colors.info, fontWeight: '700', marginBottom: 2 },
  text: { fontSize: 15, color: colors.text, lineHeight: 21 },
  typing: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  typingText: { color: colors.muted, fontSize: 14 },
  systemWrap: { alignItems: 'center', gap: 6 },
  system: { textAlign: 'center', color: colors.muted, fontSize: 13, paddingHorizontal: 24 },
  systemError: { color: '#B42318' },
  retry: { borderRadius: 999, paddingVertical: 6, paddingHorizontal: 14, backgroundColor: colors.primarySoft },
  retryText: { color: colors.primary, fontWeight: '700', fontSize: 13 },
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
