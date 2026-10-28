import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readThemeChoice, writeThemeChoice, effectiveMode, appearanceRows, THEME_KEY } from './theme.ts';

function memoryStorage(seed: Record<string, string> = {}) {
  const m = new Map(Object.entries(seed));
  return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => { m.set(k, v); } };
}

test('the menu shows the current choice, and System when nothing was chosen', () => {
  const checked = (s: ReturnType<typeof memoryStorage>) => appearanceRows(readThemeChoice(s)).filter((r) => r.checked).map((r) => r.label);
  assert.deepEqual(checked(memoryStorage()), ['System']);
  assert.deepEqual(checked(memoryStorage({ [THEME_KEY]: 'light' })), ['Light']);
  assert.deepEqual(checked(memoryStorage({ [THEME_KEY]: 'dark' })), ['Dark']);
  assert.deepEqual(appearanceRows('system').map((r) => r.label), ['System', 'Light', 'Dark']);
});

test('picking System, Light or Dark sets that mode', () => {
  for (const [choice, osDark, mode] of [['light', true, 'light'], ['dark', false, 'dark'], ['system', true, 'dark'], ['system', false, 'light']] as const) {
    assert.equal(effectiveMode(choice, osDark), mode, `${choice} with the OS ${osDark ? 'dark' : 'light'}`);
  }
});

test('the choice persists: what was picked is what a reload reads', () => {
  const s = memoryStorage();
  for (const c of ['light', 'dark', 'system'] as const) {
    writeThemeChoice(s, c);
    assert.equal(readThemeChoice(s), c);
  }
});

test('a junk or unreadable stored value falls back to System', () => {
  assert.equal(readThemeChoice(memoryStorage({ [THEME_KEY]: 'sepia' })), 'system');
  assert.equal(readThemeChoice({ getItem: () => { throw new Error('blocked'); }, setItem: () => {} }), 'system');
  assert.doesNotThrow(() => writeThemeChoice({ getItem: () => null, setItem: () => { throw new Error('quota'); } }, 'dark'));
});
