import { test } from 'node:test';
import assert from 'node:assert/strict';
import { usageParts } from './usageLine.ts';

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
