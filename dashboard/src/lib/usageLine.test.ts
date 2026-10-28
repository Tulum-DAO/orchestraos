import { test } from 'node:test';
import assert from 'node:assert/strict';
import { usageParts, usageLines } from './usageLine.ts';

const NOW = new Date(1792170500 * 1000);

test('a reading shows provider, percent and window', () => {
  const p = usageParts([{ provider: 'codex', window: '5h', used_pct: 41.4, resets_at: 1792185347, stale: false }], NOW);
  assert.equal(p.length, 1);
  assert.equal(p[0].text, 'Codex 41% 5 h');
  assert.match(p[0].title, /41% of the 5 h limit used, resets/);
  assert.equal(p[0].stale, false);
});

test('no reading is a dash with the reason, never a number', () => {
  for (const used of [null, undefined, '40', NaN, -1, 101]) {
    const p = usageParts([{ provider: 'gemini', used_pct: used as any, reason: 'Gemini CLI reports no usage we can read' }], NOW);
    assert.equal(p[0].text, 'Gemini —', String(used));
    assert.equal(p[0].missing, true);
    assert.equal(p[0].title, 'Gemini CLI reports no usage we can read');
  }
});

test('a stale reading says when it was taken', () => {
  const p = usageParts([{ provider: 'claude', window: 'weekly', used_pct: 80, as_of: 1792160000, stale: true }], NOW);
  assert.equal(p[0].stale, true);
  assert.match(p[0].title, /as of /);
  assert.match(p[0].text, /^Claude 80% weekly \(as of .+\)$/);   // visible, not only on hover
  assert.equal(p[0].high, true);
});

test('two Claude logins are told apart; one is not labelled', () => {
  const one = usageParts([{ provider: 'claude', account: 'default', window: '5h', used_pct: 10 }], NOW);
  assert.equal(one[0].text, 'Claude 10% 5 h');
  const two = usageParts([
    { provider: 'claude', account: 'default', window: '5h', used_pct: 10 },
    { provider: 'claude', account: 'work', window: '5h', used_pct: 20 },
  ], NOW);
  assert.deepEqual(two.map((x) => x.text), ['Claude (default) 10% 5 h', 'Claude (work) 20% 5 h']);
});

test('anything but an array is no parts', () => {
  assert.deepEqual(usageParts(undefined), []);
  assert.deepEqual(usageParts({ usage: [] }), []);
});

// What the panel shows: the live API's shape (two Claude windows, Codex with no reading, Gemini and Grok).
const LIVE = [
  { provider: 'claude', account: 'default', window: '5h', used_pct: 10 },
  { provider: 'claude', account: 'default', window: 'weekly', used_pct: 23 },
  { provider: 'codex', used_pct: null, reason: 'no Codex session has reported its limits yet' },
  { provider: 'gemini', used_pct: null, reason: 'Gemini CLI reports no usage we can read' },
  { provider: 'grok', used_pct: null, reason: 'Grok reports no usage we can read' },
];
const lineText = (l: { parts: { text: string }[] }) => l.parts.map((p) => p.text).join(' · ');

test('the panel is two lines: Claude with its name said once, then Codex; Gemini and Grok are not shown', () => {
  const lines = usageLines(LIVE, NOW);
  assert.deepEqual(lines.map((l) => l.provider), ['claude', 'codex']);
  assert.equal(lineText(lines[0]), 'Claude 10% 5 h · 23% weekly');
  assert.equal(lineText(lines[1]), 'Codex —');
  assert.equal(lines[1].parts[0].title, 'no Codex session has reported its limits yet');
});

test('Codex keeps its own line whatever order the API sends; a Claude window with no number is a dash', () => {
  const lines = usageLines([
    { provider: 'codex', window: '5h', used_pct: 41 },
    { provider: 'grok', used_pct: 5, window: '5h' },
    { provider: 'claude', used_pct: null, reason: 'no reading' },
  ], NOW);
  assert.deepEqual(lines.map(lineText), ['Claude —', 'Codex 41% 5 h']);
});

test('two Claude logins keep their names, since the name changes within the line', () => {
  const [claude] = usageLines([
    { provider: 'claude', account: 'default', window: '5h', used_pct: 10 },
    { provider: 'claude', account: 'default', window: 'weekly', used_pct: 23 },
    { provider: 'claude', account: 'work', window: '5h', used_pct: 20 },
  ], NOW);
  assert.equal(lineText(claude), 'Claude (default) 10% 5 h · 23% weekly · Claude (work) 20% 5 h');
});

test('a provider the API does not report has no line; nothing at all is no lines', () => {
  assert.deepEqual(usageLines([{ provider: 'claude', window: '5h', used_pct: 1 }], NOW).map((l) => l.provider), ['claude']);
  assert.deepEqual(usageLines(undefined), []);
});
