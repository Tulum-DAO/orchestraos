// The full emoji picker's data and rules (lib/emojiData.gen.ts, lib/emojiPicker.ts): bundled data, every emoji one the
// API takes, skin tones, search, and the tone remembered in this browser.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { EMOJI_GROUPS } from './emojiData.gen.ts';
import { readTone, searchEmoji, withSkinTone, writeTone, SKIN_TONES } from './emojiPicker.ts';
import { generated, MAX_VERSION } from '../../scripts/emoji-data.mjs';

test('the bundled dataset is what the generator gives (run node scripts/emoji-data.mjs after a bump)', () => {
  const file = readFileSync(new URL('./emojiData.gen.ts', import.meta.url), 'utf8');
  assert.equal(file, generated());
  assert.equal(MAX_VERSION, 15.1);
});

test('nine categories, about nineteen hundred emoji, every one a single grapheme with no letters (the API takes it)', () => {
  assert.deepEqual(EMOJI_GROUPS.map((g) => g.slug), ['smileys_emotion', 'people_body', 'animals_nature', 'food_drink',
    'travel_places', 'activities', 'objects', 'symbols', 'flags']);
  const all = EMOJI_GROUPS.flatMap((g) => g.emojis);
  assert.ok(all.length > 1800 && all.length < 2000, String(all.length));
  const seg = new Intl.Segmenter('en', { granularity: 'grapheme' });
  for (const [e] of all) {
    assert.equal([...seg.segment(e)].length, 1, e);
    assert.equal(/\p{L}/u.test(e), false, e);
  }
  assert.equal(all.some(([e]) => e === 'ℹ️'), false);
});

test('a skin tone goes after the first code point; one that takes none, or no tone, stays as it is', () => {
  assert.equal(withSkinTone('👍', 1, '\u{1F3FD}'), '👍🏽');
  assert.equal(withSkinTone('☝️', 1, '\u{1F3FB}'), '☝🏻');
  assert.equal(withSkinTone('🧑‍💻', 1, '\u{1F3FF}'), '🧑🏿‍💻');
  assert.equal(withSkinTone('😀', 0, '\u{1F3FD}'), '😀');
  assert.equal(withSkinTone('👍', 1, ''), '👍');
  const toned = new Map(EMOJI_GROUPS.flatMap((g) => g.emojis).map(([e, , t]) => [e, t]));
  assert.equal(toned.get('👍'), 1);
  assert.equal(toned.get('🧑‍🤝‍🧑'), 0);   // a tone per person: not offered
  assert.equal(SKIN_TONES.length, 6);
});

test('search: every word typed, from a word\'s start, names starting with the query first', () => {
  const hits = searchEmoji(EMOJI_GROUPS, 'thumbs');
  assert.deepEqual(hits.slice(0, 2).map((h) => h.emoji), ['👍', '👎']);
  assert.ok(searchEmoji(EMOJI_GROUPS, 'face grin').some((h) => h.emoji === '😀'));
  assert.ok(searchEmoji(EMOJI_GROUPS, 'party').some((h) => h.emoji === '🎉'));
  assert.deepEqual(searchEmoji(EMOJI_GROUPS, '   '), []);
  assert.deepEqual(searchEmoji(EMOJI_GROUPS, 'zzzzqx'), []);
  assert.equal(searchEmoji(EMOJI_GROUPS, 'a', 10).length, 10);
});

test('the chosen tone is remembered in this browser; a closed or odd store reads as none', () => {
  const mem = new Map<string, string>();
  const store = { getItem: (k: string) => mem.get(k) ?? null, setItem: (k: string, v: string) => { mem.set(k, v); } };
  assert.equal(readTone(store), '');
  writeTone('\u{1F3FE}', store);
  assert.equal(readTone(store), '\u{1F3FE}');
  mem.set('orchestra.emojiSkinTone', 'nonsense');
  assert.equal(readTone(store), '');
  const broken = { getItem: () => { throw new Error('denied'); }, setItem: () => { throw new Error('denied'); } };
  assert.equal(readTone(broken), '');
  assert.doesNotThrow(() => writeTone('', broken));
});
