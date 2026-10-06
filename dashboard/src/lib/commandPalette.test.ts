import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  shouldOpenPalette, isPaletteChord, filterEntries, pushRecent, pruneRecents, RECENTS_MAX,
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

test('ON A MAC, Ctrl-K is NOT the palette — it is kill-to-end-of-line', () => {
  // Cocoa binds Ctrl-K in every native text field. Claiming it would silently eat a standard
  // editing key: the operator's half-written message survives untouched under a palette they
  // did not ask for. This is the one that goes RED on an unconditional `metaKey || ctrlKey`.
  assert.equal(shouldOpenPalette({ key: 'k', ctrlKey: true, isMac: true, target: target('TEXTAREA') }), false);
  assert.equal(shouldOpenPalette({ key: 'k', ctrlKey: true, isMac: true, target: target('DIV') }), false);
  // The POSITIVE CONTROL: Cmd-K on the same Mac still opens it, so the rule above is a
  // platform distinction and not the palette refusing everything.
  assert.equal(shouldOpenPalette({ key: 'k', metaKey: true, isMac: true, target: target('TEXTAREA') }), true);
});

test('the chord is the chord, with none of the open-time refusals attached', () => {
  // isPaletteChord answers the second question the component asks: close on the SAME keys.
  // It must ignore modalOpen and the terminal, which apply only to opening.
  assert.equal(isPaletteChord({ key: 'k', metaKey: true }), true);
  assert.equal(isPaletteChord({ key: 'k', ctrlKey: true }), true);
  assert.equal(isPaletteChord({ key: 'k', ctrlKey: true, isMac: true }), false);
  assert.equal(isPaletteChord({ key: 'j', metaKey: true }), false);
  assert.equal(isPaletteChord({ key: 'k' }), false);
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

/**
 * The fixtures are ADVERSARIAL BY ARRANGEMENT, because the obvious version of this list cannot
 * fail. With one row per query the assertion `out[0].id === X` holds for ANY scoring function
 * that returns a positive number, so it would pass with the ranking deleted.
 *
 * So for each ranked query there is a row that matches the SAME query the WEAKER way, and
 * the weaker-matching row is placed BEFORE the stronger one on purpose, in BOTH pairs:
 * `rebuilder-bot` before `orchestraos-builder` (word-start vs substring) and `deploy-q` before
 * `quest-orchestra` (prefix vs word-start). Strip either tier and the two collapse to equal
 * scores, the tie falls back to input order, and the expected ordering INVERTS. The tests
 * below assert an ORDER, never an identity.
 *
 * Checked by deleting each tier in turn and watching the suite go red — the first draft of
 * this fixture had the pair in the other order and did NOT catch a deleted prefix tier.
 */
const entries: PaletteEntry[] = [
  { kind: 'agent', id: 'gm', label: 'gm', to: '/agent/gm' },
  { kind: 'agent', id: 'rebuilder-bot', label: 'rebuilder-bot', to: '/agent/rebuilder-bot' },
  { kind: 'agent', id: 'orchestraos-builder', label: 'orchestraos-builder', to: '/agent/orchestraos-builder' },
  { kind: 'agent', id: 'deploy-q', label: 'deploy-q', to: '/agent/deploy-q' },
  { kind: 'agent', id: 'quest-orchestra', label: 'quest-orchestra', to: '/agent/quest-orchestra' },
  { kind: 'agent', id: 'sqa-runner', label: 'sqa-runner', to: '/agent/sqa-runner' },
  { kind: 'page', id: 'approvals', label: 'Approvals', to: '/approvals' },
];

test('an empty query offers everything, in order', () => {
  assert.equal(filterEntries(entries, '').length, entries.length);
  assert.equal(filterEntries(entries, '')[0].id, 'gm');
});

test('all three tiers are ordered: prefix, then word-start, then substring', () => {
  // Every one of these matches 'q', so only the RANKING separates them — unlike asserting the
  // identity of a sole survivor, which holds for any score() returning a positive number.
  // Precisely: this goes RED if the PREFIX or the SUBSTRING tier is removed. Removing the
  // WORD-START tier leaves this order unchanged; the test below is the one that covers it.
  const out = filterEntries(entries, 'q');
  assert.deepEqual(out.map((e) => e.id), ['quest-orchestra', 'deploy-q', 'sqa-runner']);
});

test('a word-start inside a hyphenated name OUTRANKS a bare substring', () => {
  // rebuilder-bot contains 'builder' and comes FIRST in input order. If the word-start tier
  // were dropped, both would score as substrings and rebuilder-bot would win the tie.
  const out = filterEntries(entries, 'builder');
  assert.deepEqual(out.map((e) => e.id), ['orchestraos-builder', 'rebuilder-bot']);
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

test('the component actually PASSES the platform to the chord test', async () => {
  // Same class as the data-terminal pair below, and the same reason: isPaletteChord's isMac
  // branch is proven against a fixture, which proves the RULE and not the WIRING. If
  // CommandPalette stops passing isMac, every unit test here stays green while Ctrl-K silently
  // starts eating kill-to-end-of-line on every Mac again. Pin it where it can break silently.
  const { readFileSync } = await import('node:fs');
  const src = readFileSync(new URL('../components/CommandPalette.tsx', import.meta.url), 'utf-8');
  assert.match(src, /isMac:\s*IS_MAC/,
    'CommandPalette stopped passing isMac — the Mac Ctrl-K refusal is now dead code');
  assert.match(src, /navigator/,
    'IS_MAC no longer derives from the platform at all');
});

test('the palette looks for that same attribute', async () => {
  // Both halves of the contract, so renaming one without the other fails here rather than
  // silently in front of someone typing into a terminal.
  const { readFileSync } = await import('node:fs');
  const src = readFileSync(new URL('./commandPalette.ts', import.meta.url), 'utf-8');
  assert.match(src, /\[data-terminal\]/);
});
