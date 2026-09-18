/**
 * RED ALERT ra_2653f9bf (2026-09-17 23:30 Tulum): the harness bottom-bar ^Z button
 * mapped to `tmux send-keys C-z`, which suspended the gm CLI (STAT T). Shaw: remove
 * ^Z, replace with ^U (clear input), confirm ^C. The API refuses ctrl-z so NO client
 * (web, iOS, watch, curl) can suspend a seat again, and ctrl-u maps to C-u.
 *
 * Run: npx tsx --test src/lib/special-keys.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { resolveSpecialKey, FORBIDDEN_KEYS } from './special-keys.js';

test('ctrl-z is refused with the RED ALERT reason', () => {
  const r = resolveSpecialKey('ctrl-z');
  assert.equal(r.ok, false);
  if (!r.ok) assert.match(r.reason, /suspend/i);
  assert.ok(FORBIDDEN_KEYS.includes('ctrl-z'));
});

test('ctrl-u clears the composer (C-u)', () => {
  assert.deepEqual(resolveSpecialKey('ctrl-u'), { ok: true, tmux: ['C-u'] });
});

test('known keys map, case-insensitive', () => {
  assert.deepEqual(resolveSpecialKey('Escape'), { ok: true, tmux: ['Escape'] });
  assert.deepEqual(resolveSpecialKey('ctrl-c'), { ok: true, tmux: ['C-c'] });
  assert.deepEqual(resolveSpecialKey('shift-tab'), { ok: true, tmux: ['BTab'] });
});

test('unknown keys pass through verbatim (single printable tokens)', () => {
  assert.deepEqual(resolveSpecialKey('7'), { ok: true, tmux: ['7'] });
  assert.deepEqual(resolveSpecialKey('F5'), { ok: true, tmux: ['F5'] });
});

test('raw C-z spelling and the empty key are refused too', () => {
  assert.equal(resolveSpecialKey('C-z').ok, false);
  assert.equal(resolveSpecialKey('').ok, false);
});
