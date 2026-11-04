/**
 * Settings › General › Send shortcut: which keys send a message, per device. Every message composer reads it through
 * composerKeys(<its id>, setting); unset, each keeps what it did before the setting existed.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { readdirSync, statSync } from 'node:fs';
import { composerKeyAction, composerKeys, sendShortcutNote, COMPOSERS, type ComposerId } from './composerGate.ts';
import { readDisplay, writeDisplay, DISPLAY_DEFAULTS, DISPLAY_KEYS } from './displaySettings.ts';
import { display, resetDisplay, setDeviceSetting } from './deviceSettings.ts';

const SRC = fileURLToPath(new URL('..', import.meta.url));
const read = (p: string) => readFileSync(join(SRC, p), 'utf-8');

type Ev = { key: string; shiftKey?: boolean; metaKey?: boolean; ctrlKey?: boolean; isComposing?: boolean; keyCode?: number; coarse?: boolean; anyFine?: boolean };
const ENTER: Ev = { key: 'Enter' }, SHIFT: Ev = { key: 'Enter', shiftKey: true };
const CMD: Ev = { key: 'Enter', metaKey: true }, CTRL: Ev = { key: 'Enter', ctrlKey: true };
const press = (id: ComposerId, setting: 'enter' | 'mod-enter' | null, e: Ev) => composerKeyAction({ ...e, ...composerKeys(id, setting) });

// The composers that send a chat message, where each lives, and what its keys were before this setting.
const PATHS: Record<ComposerId, { file: string; unset: 'enter' | 'mod-enter'; touchEnterSends: boolean }> = {
  agentChat: { file: 'components/chat/ChatInput.tsx', unset: 'enter', touchEnterSends: false },
  arturoPill: { file: 'components/arturo/ArturoPill.tsx', unset: 'enter', touchEnterSends: true },
  arturoHome: { file: 'pages/ArturoHome.tsx', unset: 'enter', touchEnterSends: true },
  agentCard: { file: 'components/AgentCard.tsx', unset: 'mod-enter', touchEnterSends: false },
  assistant: { file: 'components/assistant/AssistantInput.tsx', unset: 'mod-enter', touchEnterSends: false },
};

test('each composer says where it lives', () => {
  for (const [id, p] of Object.entries(PATHS) as [ComposerId, (typeof PATHS)[ComposerId]][]) assert.equal(COMPOSERS[id].file, p.file, id);
});

// Every textarea in the app whose onKeyDown acts on Enter, found in the source: each must be a listed composer, or one
// of the few that are not chat messages (named here with why). A new message box that skips the setting fails here.
const NOT_A_MESSAGE: Record<string, string> = {
  'components/chat/MultiPartCard.tsx': 'a questionnaire answer: Enter moves to the next part, it sends no chat message',
};
function tsxFiles(dir: string): string[] {
  return readdirSync(join(SRC, dir)).flatMap((n) => {
    const rel = `${dir}${dir ? '/' : ''}${n}`;
    return statSync(join(SRC, rel)).isDirectory() ? tsxFiles(rel) : rel.endsWith('.tsx') ? [rel] : [];
  });
}
test('every textarea that acts on Enter is a listed composer (or named as not a chat message)', () => {
  const found = new Set<string>();
  for (const f of tsxFiles('')) {
    const src = read(f);
    for (const m of src.matchAll(/<textarea\b[\s\S]*?(?:\/>|<\/textarea>)/g)) {
      const el = m[0];
      const handler = /onKeyDown=\{([A-Za-z_$][\w$]*)\}/.exec(el);
      // inline handler: its own text; a named one (onKeyDown={onKey}): that function's body in the same file
      const body = handler ? (new RegExp(`const ${handler[1]} = [\\s\\S]*?\\n  \\};`).exec(src)?.[0] ?? '') : /onKeyDown=\{[\s\S]*$/.exec(el)?.[0] ?? '';
      if (/onKeyDown/.test(el) && /'Enter'|composerKeyAction/.test(body)) found.add(f);
    }
  }
  const listed = new Set([...Object.values(COMPOSERS).map((c) => c.file), ...Object.keys(NOT_A_MESSAGE)]);
  assert.deepEqual([...found].sort(), [...listed].sort());
});

for (const [id, p] of Object.entries(PATHS) as [ComposerId, (typeof PATHS)[ComposerId]][]) {
  test(`${id} (${p.file}): "Enter to send" — Enter sends, Shift+Enter is a new line, ⌘/Ctrl+Enter sends`, () => {
    assert.equal(press(id, 'enter', ENTER), 'send');
    assert.equal(press(id, 'enter', SHIFT), 'none');
    assert.equal(press(id, 'enter', CMD), 'send');
    assert.equal(press(id, 'enter', CTRL), 'send');
  });
  test(`${id} (${p.file}): "⌘/Ctrl+Enter to send" — Enter and Shift+Enter are new lines, ⌘/Ctrl+Enter sends`, () => {
    assert.equal(press(id, 'mod-enter', ENTER), 'none');
    assert.equal(press(id, 'mod-enter', SHIFT), 'none');
    assert.equal(press(id, 'mod-enter', CMD), 'send');
    assert.equal(press(id, 'mod-enter', CTRL), 'send');
  });
  test(`${id} (${p.file}): unset keeps what it did before (${p.unset})`, () => {
    assert.equal(press(id, null, ENTER), p.unset === 'enter' ? 'send' : 'none');
    assert.equal(press(id, null, SHIFT), 'none');
    assert.equal(press(id, null, CMD), 'send');
    assert.equal(press(id, null, CTRL), 'send');
  });
  test(`${id} (${p.file}): on a touch screen the setting does not apply (Enter ${p.touchEnterSends ? 'sends' : 'is a new line'}, as before)`, () => {
    for (const s of ['enter', 'mod-enter', null] as const) {
      assert.equal(press(id, s, { ...ENTER, coarse: true }), p.touchEnterSends ? 'send' : 'none', String(s));
      assert.equal(press(id, s, { ...SHIFT, coarse: true }), 'none', String(s));
      assert.equal(press(id, s, { ...CTRL, coarse: true }), 'send', String(s));
    }
  });
  test(`${id} (${p.file}): Enter while an IME is composing never sends (isComposing, or Safari's keyCode 229)`, () => {
    for (const s of ['enter', 'mod-enter', null] as const) {
      assert.equal(press(id, s, { ...ENTER, isComposing: true }), 'none');
      assert.equal(press(id, s, { ...CMD, isComposing: true }), 'none');
      assert.equal(press(id, s, { ...ENTER, keyCode: 229 }), 'none');
      assert.equal(press(id, s, { ...CTRL, keyCode: 229 }), 'none');
      assert.equal(press(id, s, { ...ENTER, keyCode: 13 }), (s ?? p.unset) === 'enter' ? 'send' : 'none');
    }
  });
  test(`${id} (${p.file}): on a touch screen keyCode 229 is not an IME tell (some Android keyboards send Enter as 229): Enter does what it did`, () => {
    for (const s of ['enter', 'mod-enter', null] as const) {
      assert.equal(press(id, s, { ...ENTER, keyCode: 229, coarse: true }), p.touchEnterSends ? 'send' : 'none', String(s));
      assert.equal(press(id, s, { ...CTRL, keyCode: 229, coarse: true }), 'send', String(s));
      assert.equal(press(id, s, { ...ENTER, keyCode: 229, coarse: true, isComposing: true }), 'none', 'isComposing still blocks on touch');
    }
  });
  test(`${id} (${p.file}): a touch screen with a fine pointer too (iPad + keyboard): a chosen shortcut applies; unset keeps its touch Enter`, () => {
    const both = { coarse: true, anyFine: true };
    assert.equal(press(id, 'enter', { ...ENTER, ...both }), 'send');
    assert.equal(press(id, 'mod-enter', { ...ENTER, ...both }), 'none');
    assert.equal(press(id, 'mod-enter', { ...CMD, ...both }), 'send');
    assert.equal(press(id, null, { ...ENTER, ...both }), p.touchEnterSends ? 'send' : 'none');
  });
  test(`${id} (${p.file}): picking "Enter to send" from unset makes Enter send`, () => {
    assert.equal(press(id, 'enter', ENTER), 'send');
    assert.equal(press(id, 'enter', SHIFT), 'none');
  });
  test(`${id} (${p.file}) reads this device's Send shortcut and asks composerKeys('${id}', …)`, () => {
    const f = read(p.file);
    assert.match(f, /useDeviceSetting\('sendShortcut'\)/, p.file);
    assert.match(f, new RegExp(`composerKeys\\('${id}', `), p.file);
    assert.match(f, /composerKeyAction\(/, p.file);
    assert.match(f, /\.\.\.keyArgs\(e\)/, p.file);
  });
}

test('no composer keeps its own Enter rule beside the shared one', () => {
  for (const p of Object.values(PATHS)) {
    const f = read(p.file);
    assert.doesNotMatch(f, /e\.key === 'Enter' && (!e\.shiftKey|\(e\.metaKey \|\| e\.ctrlKey\))/, p.file);
  }
});

test('the row: unset checks neither option and says the boxes keep their own keys; picking either stores it', () => {
  const r = read('components/settings/DeviceRows.tsx');
  assert.match(r, /<Choice<SendShortcut> labelledBy="setting-general\.sendShortcut" value=\{value\}/, 'the stored value (null) goes through, not a stand-in');
  assert.doesNotMatch(r, /value \?\? 'enter'/);
  assert.equal(sendShortcutNote(null), 'Each message box keeps its own keys until you choose.');
  assert.equal(sendShortcutNote('enter'), 'Shift+Enter starts a new line.');
  assert.equal(sendShortcutNote('mod-enter'), 'Enter starts a new line.');
  const kit = read('components/settings/SettingsKit.tsx');
  // Choice: null = nothing checked; a click calls onChange whenever the option differs from the value (null differs
  // from every option), so "Enter to send" from unset is stored; the first option stays the Tab stop.
  assert.match(kit, /value: T \| null;/);
  assert.match(kit, /onClick=\{\(\) => \{ if \(o\.value !== value\) onChange\(o\.value\); \}\}/);
  assert.match(kit, /const stop = enabled\.find\(\(o\) => o\.value === value\) \?\? enabled\[0\];/);
  const m = new Map<string, string>();
  const s = { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => { m.set(k, v); }, removeItem: (k: string) => { m.delete(k); } };
  writeDisplay(s, 'sendShortcut', 'enter');
  assert.equal(readDisplay(s).sendShortcut, 'enter');
  assert.equal(press('agentCard', readDisplay(s).sendShortcut, ENTER), 'send', 'the Agents page box then sends on Enter');
  assert.equal(press('assistant', readDisplay(s).sendShortcut, ENTER), 'send');
});

test('the row and the composers share one test of the device: (any-pointer: fine)', () => {
  const g = read('lib/composerGate.ts');
  assert.match(g, /export const FINE_POINTER_QUERY = '\(any-pointer: fine\)';/);
  assert.match(g, /anyFine: hasFinePointer\(\)/);
  const r = read('components/settings/DeviceRows.tsx');
  assert.match(r, /useSyncExternalStore\(subscribeFine, hasFinePointer,/);
  assert.match(r, /window\.matchMedia\(FINE_POINTER_QUERY\)/);
  assert.match(r, /if \(!fine\) return null;/);
});

test('stored per device: unset by default, either choice kept, anything else is unset; Reset display leaves it', () => {
  assert.equal(DISPLAY_DEFAULTS.sendShortcut, null);
  const m = new Map<string, string>();
  const s = { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => { m.set(k, v); }, removeItem: (k: string) => { m.delete(k); } };
  writeDisplay(s, 'sendShortcut', 'enter');
  assert.equal(m.get(DISPLAY_KEYS.sendShortcut), 'enter', 'an explicit "Enter to send" is kept (it differs from unset)');
  writeDisplay(s, 'sendShortcut', 'mod-enter');
  assert.equal(readDisplay(s).sendShortcut, 'mod-enter');
  m.set(DISPLAY_KEYS.sendShortcut, 'space');
  assert.equal(readDisplay(s).sendShortcut, null);
  setDeviceSetting('sendShortcut', 'mod-enter');
  resetDisplay();
  assert.equal(display().sendShortcut, 'mod-enter', 'Reset display settings is about Display; the send keys stay');
  setDeviceSetting('sendShortcut', null);
});
