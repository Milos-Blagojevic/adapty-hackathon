import { Linking, Platform, StyleSheet, Text, View, type TextStyle } from 'react-native';

import { colors } from './ui';

/**
 * The model writes light markdown: **bold**, `code`, [links](https://…), bullet and numbered lists,
 * the odd heading, blank lines between paragraphs. Shown as styled text instead of raw asterisks.
 */
export type Inline = { text: string; bold?: boolean; italic?: boolean; code?: boolean; url?: string };
export type Block =
  | { type: 'p'; parts: Inline[] }
  | { type: 'heading'; parts: Inline[] }
  | { type: 'bullet'; marker: string; parts: Inline[] }
  | { type: 'gap' };

// **bold** | __bold__ | `code` | [text](url) | *italic* (not "* " list markers or a lone star)
const INLINE = /\*\*([^*\n]+?)\*\*|__([^_\n]+?)__|`([^`\n]+)`|\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)|\*([^*\s](?:[^*\n]*?[^*\s])?)\*/g;

export function parseInline(line: string): Inline[] {
  const out: Inline[] = [];
  let last = 0;
  for (const m of line.matchAll(INLINE)) {
    const at = m.index ?? 0;
    if (at > last) out.push({ text: line.slice(last, at) });
    if (m[1] ?? m[2]) out.push({ text: (m[1] ?? m[2]) as string, bold: true });
    else if (m[3]) out.push({ text: m[3], code: true });
    else if (m[4]) out.push({ text: m[4], url: m[5] });
    else if (m[6]) out.push({ text: m[6], italic: true });
    last = at + m[0].length;
  }
  if (last < line.length) out.push({ text: line.slice(last) });
  return out;
}

export function parseBlocks(text: string): Block[] {
  const blocks: Block[] = [];
  for (const raw of text.replace(/\r\n?/g, '\n').split('\n')) {
    const line = raw.trimEnd();
    if (!line.trim()) {
      if (blocks.length && blocks[blocks.length - 1].type !== 'gap') blocks.push({ type: 'gap' });
      continue;
    }
    const heading = /^\s*#{1,6}\s+(.*)$/.exec(line);
    const bullet = /^\s*[-*•]\s+(.*)$/.exec(line);
    const numbered = /^\s*(\d{1,2}[.)])\s+(.*)$/.exec(line);
    if (heading) blocks.push({ type: 'heading', parts: parseInline(heading[1].replace(/\*\*/g, '')) });
    else if (bullet) blocks.push({ type: 'bullet', marker: '•', parts: parseInline(bullet[1]) });
    else if (numbered) blocks.push({ type: 'bullet', marker: numbered[1], parts: parseInline(numbered[2]) });
    else blocks.push({ type: 'p', parts: parseInline(line) });
  }
  while (blocks.length && blocks[blocks.length - 1].type === 'gap') blocks.pop();
  return blocks;
}

function Parts({ parts, style }: { parts: Inline[]; style: TextStyle[] }) {
  return (
    <Text style={style}>
      {parts.map((p, i) => (
        <Text
          key={i}
          style={[p.bold && styles.bold, p.italic && styles.italic, p.code && styles.code, !!p.url && styles.link]}
          onPress={p.url ? () => Linking.openURL(p.url as string) : undefined}
        >
          {p.text}
        </Text>
      ))}
    </Text>
  );
}

export function RichText({ text, style }: { text: string; style?: TextStyle | TextStyle[] }) {
  const base = ([] as TextStyle[]).concat(style ?? []);
  const blocks = parseBlocks(text);
  return (
    <View>
      {blocks.map((b, i) => {
        if (b.type === 'gap') return <View key={i} style={styles.gap} />;
        if (b.type === 'heading') return <Parts key={i} parts={b.parts} style={[...base, styles.bold]} />;
        if (b.type === 'bullet')
          return (
            <View key={i} style={styles.bulletRow}>
              <Text style={[...base, styles.marker]}>{b.marker}</Text>
              <View style={styles.bulletBody}>
                <Parts parts={b.parts} style={base} />
              </View>
            </View>
          );
        return <Parts key={i} parts={b.parts} style={base} />;
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  bold: { fontWeight: '700' },
  italic: { fontStyle: 'italic' },
  code: { fontFamily: Platform.select({ ios: 'Menlo', default: 'monospace' }), backgroundColor: '#0000000D' },
  link: { color: colors.info, textDecorationLine: 'underline' },
  gap: { height: 8 },
  bulletRow: { flexDirection: 'row', gap: 6 },
  marker: { minWidth: 14 },
  bulletBody: { flex: 1 },
});
