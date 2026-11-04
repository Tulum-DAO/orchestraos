import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  DISPLAY_DEFAULTS, DISPLAY_KEYS, TEXT_SCALES, readDisplay, writeDisplay, defaultSidebarWidth, clampSidebarWidth, sidebarWidth,
  drawerWidthVw, avatarFactor, textAvatarPx, displayVars, displayAttrs, applyDisplay,
} from './displaySettings.ts';

function mem(seed: Record<string, string> = {}) {
  const m = new Map(Object.entries(seed));
  return { m, getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => { m.set(k, v); }, removeItem: (k: string) => { m.delete(k); } };
}

test('sidebar default width follows text size, capped at 300 (the spec table)', () => {
  assert.deepEqual(TEXT_SCALES.map(defaultSidebarWidth), [210, 240, 270, 300, 300]);
});

test('dragging is held to 200–360; a stored width overrides the default; none means the default', () => {
  assert.deepEqual([clampSidebarWidth(100), clampSidebarWidth(264.4), clampSidebarWidth(999)], [200, 264, 360]);
  assert.equal(sidebarWidth({ ...DISPLAY_DEFAULTS, textScale: 1.5 }), 300);
  assert.equal(sidebarWidth({ ...DISPLAY_DEFAULTS, textScale: 1.5, sidebarWidth: 220 }), 220);
});

test('phone drawer: about 80% of the screen at 100% text, about 90% at 125–150%', () => {
  assert.deepEqual(TEXT_SCALES.map(drawerWidthVw), [80, 80, 85, 90, 90]);
});

test('avatars next to text follow text size at half the change, clamped [0.9, 1.25], never under 16 px', () => {
  assert.equal(avatarFactor(1), 1);
  assert.equal(avatarFactor(1.5), 1.25);
  assert.equal(avatarFactor(0.87), 0.935);
  assert.equal(avatarFactor(0.5), 0.9);
  assert.equal(avatarFactor(3), 1.25);
  assert.deepEqual([textAvatarPx(32, 0.87), textAvatarPx(32, 1), textAvatarPx(32, 1.5), textAvatarPx(20, 0.87), textAvatarPx(16, 0.87)], [30, 32, 40, 19, 16]);
});

test('reading: defaults when nothing or garbage is stored; writing a default removes the key', () => {
  assert.deepEqual(readDisplay(null), DISPLAY_DEFAULTS);
  assert.deepEqual(readDisplay(mem({ [DISPLAY_KEYS.textScale]: '7', [DISPLAY_KEYS.spacing]: 'huge', [DISPLAY_KEYS.sidebarWidth]: 'wide' })), DISPLAY_DEFAULTS);
  const s = mem();
  writeDisplay(s, 'textScale', 1.5);
  writeDisplay(s, 'motion', 'reduced');
  writeDisplay(s, 'sidebarWidth', 280);
  assert.deepEqual(readDisplay(s), { ...DISPLAY_DEFAULTS, textScale: 1.5, motion: 'reduced', sidebarWidth: 280 });
  writeDisplay(s, 'textScale', 1);
  writeDisplay(s, 'sidebarWidth', null);
  assert.equal(s.m.has(DISPLAY_KEYS.textScale), false);
  assert.equal(s.m.has(DISPLAY_KEYS.sidebarWidth), false);
  assert.equal(DISPLAY_KEYS.sidebarWidth, 'orchestra.sidebar.width');
});

test('the CSS variables and attributes the app reads', () => {
  const d = { ...DISPLAY_DEFAULTS, textScale: 1.12 as const, spacing: 'roomy' as const, messageWidth: 'wide' as const, timestamps: 'hover' as const };
  assert.deepEqual(displayVars(d), {
    '--ui-text-scale': '1.12', '--ui-gap': '1.25', '--ui-msg-width': '720px', '--ui-col-extra': '200px',
    '--sidebar-w': '270px', '--drawer-w': '85vw',
  });
  assert.deepEqual(displayAttrs(d), { 'data-motion': 'system', 'data-timestamps': 'hover' });
  const set: Record<string, string> = {};
  applyDisplay(d, { style: { setProperty: (k, v) => { set[k] = v; } }, setAttribute: (k, v) => { set[k] = v; } });
  assert.equal(set['--sidebar-w'], '270px');
  assert.equal(set['data-timestamps'], 'hover');
});
