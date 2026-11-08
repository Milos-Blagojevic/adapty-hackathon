import { describe, expect, it } from '@jest/globals';

import { parseBlocks, parseInline } from '../src/components/RichText';
import { sharedPrefix } from '../src/components/UserPicker';

describe('RichText', () => {
  it('bold, code, links and italics; ids with underscores stay plain', () => {
    expect(parseInline('Renews on **Oct 19, 2027** for lumen_443814_00014')).toEqual([
      { text: 'Renews on ' },
      { text: 'Oct 19, 2027', bold: true },
      { text: ' for lumen_443814_00014' },
    ]);
    expect(parseInline('Open `Settings` or [Apple](https://reportaproblem.apple.com) *now*')).toEqual([
      { text: 'Open ' },
      { text: 'Settings', code: true },
      { text: ' or ' },
      { text: 'Apple', url: 'https://reportaproblem.apple.com' },
      { text: ' ' },
      { text: 'now', italic: true },
    ]);
    expect(parseInline('2 * 3 = 6')).toEqual([{ text: '2 * 3 = 6' }]);
  });

  it('paragraphs, lists and headings', () => {
    const blocks = parseBlocks('### Steps\n\n1. Open **Settings**\n- Tap Cancel\n\n\nДа, вы успеваете.\n\n');
    expect(blocks.map((b) => b.type)).toEqual(['heading', 'gap', 'bullet', 'bullet', 'gap', 'p']);
    expect(blocks[2]).toMatchObject({ marker: '1.', parts: [{ text: 'Open ' }, { text: 'Settings', bold: true }] });
    expect(blocks[3]).toMatchObject({ marker: '•' });
  });
});

describe('UserPicker', () => {
  it('drops the prefix all ids share, up to a separator', () => {
    expect(sharedPrefix(['lumen_443814_00014', 'lumen_443814_04855'])).toBe('lumen_443814_');
    expect(sharedPrefix(['user-42', 'user-7'])).toBe('user-');
    expect(sharedPrefix(['abc', 'abd'])).toBe('');
    expect(sharedPrefix(['only_one'])).toBe('');
  });
});
