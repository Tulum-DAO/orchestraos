/**
 * emojiPicker.ts — the full emoji picker's rules (components/kit/EmojiPicker.tsx), pure: skin tones, search, and the
 * tone the viewer last chose (kept in this browser). The dataset is lib/emojiData.gen.ts (scripts/emoji-data.mjs).
 */
import type { EmojiGroup } from './emojiData.gen';

/** No tone, then light to dark (U+1F3FB..U+1F3FF). */
export const SKIN_TONES = ['', '\u{1F3FB}', '\u{1F3FC}', '\u{1F3FD}', '\u{1F3FE}', '\u{1F3FF}'] as const;
export type SkinTone = (typeof SKIN_TONES)[number];

/** The emoji in a skin tone: the modifier right after its first code point (a variation selector there is dropped,
 *  the tone replaces it), so 👍 -> 👍🏽 and 🧑‍💻 -> 🧑🏽‍💻. No tone, or an emoji that takes none: as it is. */
export function withSkinTone(emoji: string, tones: 0 | 1, tone: SkinTone): string {
  if (!tone || !tones) return emoji;
  const cps = [...emoji];
  const rest = cps[1] === '️' ? cps.slice(2) : cps.slice(1);
  return cps[0] + tone + rest.join('');
}

export interface EmojiHit { emoji: string; name: string; tones: 0 | 1 }

/** Emoji whose name holds every word typed (any order, from a word's start), first those whose name starts with the
 *  query. Empty query: none. */
export function searchEmoji(groups: readonly EmojiGroup[], query: string, limit = 120): EmojiHit[] {
  const words = query.toLowerCase().split(/[\s_:]+/).filter(Boolean);
  if (!words.length) return [];
  const q = words.join(' ');
  const starts: EmojiHit[] = [];
  const rest: EmojiHit[] = [];
  for (const g of groups) {
    for (const [emoji, name, tones] of g.emojis) {
      const n = name.toLowerCase();
      const nameWords = n.split(/[\s:,-]+/);
      if (!words.every((w) => nameWords.some((nw) => nw.startsWith(w)))) continue;
      (n.startsWith(q) ? starts : rest).push({ emoji, name, tones });
    }
  }
  return [...starts, ...rest].slice(0, limit);
}

const TONE_KEY = 'orchestra.emojiSkinTone';
type Store = Pick<Storage, 'getItem' | 'setItem'>;
const browser = (): Store | null => { try { return typeof localStorage !== 'undefined' ? localStorage : null; } catch { return null; } };

/** The tone the viewer last picked in this browser ('' when none, or storage is closed). */
export function readTone(s: Store | null = browser()): SkinTone {
  try {
    const v = s?.getItem(TONE_KEY) ?? '';
    return (SKIN_TONES as readonly string[]).includes(v) ? (v as SkinTone) : '';
  } catch { return ''; }
}

export function writeTone(tone: SkinTone, s: Store | null = browser()): void {
  try { s?.setItem(TONE_KEY, tone); } catch { /* private mode: the picker still uses it this time */ }
}
