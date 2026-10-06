import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  shouldOpenPalette, filterEntries, pushRecent, pruneRecents, RECENTS_MAX,
  type PaletteEntry,
} from './commandPalette.ts';

const K = (over: Record<string, unknown> = {}) => ({ key: 'k', metaKey: true, ...over });
const target = (tagName: string, extra: Record<string, unknown> = {}) =>
  ({ tagName, closest: () => null, ...extra });

/* ─── the hotkey, where the real hazards are ───────────────────────────── */

test('Cmd-K opens the palette from the page', () => {
  assert.equal(shouldOpenPalette(K({ target: target('DIV') })), true);
});

test('Ctrl-K opens it too, for people not on a Mac', () => {
  assert.equal(shouldOpenPalette({ key: 'k', ctrlKey: true, target: target('DIV') }), true);
});

test('Cmd-K opens it from INSIDE the composer', () => {
  // A text input must not refuse the modified combination — someone mid-message is exactly
  // who wants to jump somewhere.
  assert.equal(shouldOpenPalette(K({ target: target('TEXTAREA') })), true);
  assert.equal(shouldOpenPalette(K({ target: target('INPUT') })), true);
  assert.equal(shouldOpenPalette(K({ target: target('DIV', { isContentEditable: true }) })), true);
});

test('a BARE k never opens it, anywhere', () => {
  // No bare-letter shortcut is bound, so nothing can fire while somebody types a message.
  assert.equal(shouldOpenPalette({ key: 'k', target: target('TEXTAREA') }), false);
  assert.equal(shouldOpenPalette({ key: 'k', target: target('DIV') }), false);
});

test('a TERMINAL keeps every key it is given — even Cmd-K', () => {
  // WebTerminal already preventDefaults and stopPropagations keystrokes. A terminal that
  // silently loses a keystroke to a UI overlay is a worse bug than having no palette.
  const inTerminal = { tagName: 'DIV', closest: (sel: string) => (sel === '[data-terminal]' ? {} : null) };
  assert.equal(shouldOpenPalette(K({ target: inTerminal })), false);
});

test('a modal owns the focus trap — the palette does not open over it', () => {
  assert.equal(shouldOpenPalette(K({ target: target('DIV'), modalOpen: true })), false);
});

test('an unrelated combination does nothing', () => {
  for (const key of ['j', 'p', 'Enter', 'Escape', '1']) {
    assert.equal(shouldOpenPalette(K({ key, target: target('DIV') })), false, key);
  }
});

/* ─── what it offers ───────────────────────────────────────────────────── */

const entries: PaletteEntry[] = [
  { kind: 'agent', id: 'gm', label: 'gm', to: '/agent/gm' },
  { kind: 'agent', id: 'orchestraos-builder', label: 'orchestraos-builder', to: '/agent/orchestraos-builder' },
  { kind: 'agent', id: 'quest-orchestra', label: 'quest-orchestra', to: '/agent/quest-orchestra' },
  { kind: 'page', id: 'approvals', label: 'Approvals', to: '/approvals' },
];

test('an empty query offers everything, in order', () => {
  assert.equal(filterEntries(entries, '').length, 4);
});

test('a prefix outranks a substring', () => {
  const out = filterEntries(entries, 'q');
  assert.equal(out[0].id, 'quest-orchestra', 'the prefix match must come first');
});

test('a word-start inside a hyphenated name matches', () => {
  const out = filterEntries(entries, 'builder');
  assert.equal(out[0].id, 'orchestraos-builder');
});

test('a query matching nothing offers nothing', () => {
  // The control: a filter that always returns rows is not a filter.
  assert.deepEqual(filterEntries(entries, 'zzzznope'), []);
});

test('matching is case-insensitive', () => {
  assert.equal(filterEntries(entries, 'APPROV')[0].id, 'approvals');
});

/* ─── recents ──────────────────────────────────────────────────────────── */

test('a recent moves to the front rather than duplicating', () => {
  assert.deepEqual(pushRecent(['a', 'b', 'c'], 'c'), ['c', 'a', 'b']);
  assert.deepEqual(pushRecent(['a', 'b'], 'z'), ['z', 'a', 'b']);
});

test('recents are capped', () => {
  let ids: string[] = [];
  for (let i = 0; i < 20; i++) ids = pushRecent(ids, `a${i}`);
  assert.equal(ids.length, RECENTS_MAX);
  assert.equal(ids[0], 'a19', 'newest first');
});

test('a RETIRED agent is pruned out of recents', () => {
  // Offering a dead name is offering a dead end.
  assert.deepEqual(pruneRecents(['gm', 'gone-g3', 'quest-orchestra'], ['gm', 'quest-orchestra']),
    ['gm', 'quest-orchestra']);
});

test('an UNKNOWN live list leaves recents ALONE rather than emptying them', () => {
  // "We could not ask" is not "they are all gone". Emptying on a failed feed would delete
  // the operator's history every time the fleet hiccuped.
  assert.deepEqual(pruneRecents(['gm', 'x'], undefined), ['gm', 'x']);
  assert.deepEqual(pruneRecents(['gm', 'x'], []), [], 'but a KNOWN-empty fleet does prune');
});

/* ─── the guard must exist in the REAL component, not just in the fixture ──── */

test('WebTerminal actually carries the attribute the terminal refusal looks for', async () => {
  // The refusal above passes against a FIXTURE that fakes `closest()`. That proves the rule,
  // not the wiring — and the wiring was missing: WebTerminal had no `data-terminal`, so the
  // guard was dead code in production while its unit test was green. Pin the contract at the
  // only place it can break silently.
  const { readFileSync } = await import('node:fs');
  const src = readFileSync(new URL('../components/WebTerminal.tsx', import.meta.url), 'utf-8');
  assert.match(src, /data-terminal/,
    'WebTerminal lost data-terminal — the palette\'s terminal refusal is now dead code');
});

test('the palette looks for that same attribute', async () => {
  // Both halves of the contract, so renaming one without the other fails here rather than
  // silently in front of someone typing into a terminal.
  const { readFileSync } = await import('node:fs');
  const src = readFileSync(new URL('./commandPalette.ts', import.meta.url), 'utf-8');
  assert.match(src, /\[data-terminal\]/);
});
