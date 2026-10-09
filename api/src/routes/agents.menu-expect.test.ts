/**
 * The web card answers a menu with `expect` = the menu it rendered ({question, context}). The
 * gateway then answers only while that same menu is on screen, so a tap meant for prompt A never
 * answers prompt B. The web has no served record at the gateway (it renders menus from
 * /api/agents, not from the gateway's feeds), so without `expect` its permission taps are refused.
 *
 * Run: npx tsx --test src/routes/agents.menu-expect.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { menuExpectOf } from './agents.js';

test('no expect passes through as none', () => {
  assert.equal(menuExpectOf(undefined), null);
  assert.equal(menuExpectOf(null), null);
});

test('a rendered menu passes through, context defaulting to empty', () => {
  assert.deepEqual(menuExpectOf({ question: 'Do you want to proceed? [Bash]', context: 'Bash command\nls' }),
    { question: 'Do you want to proceed? [Bash]', context: 'Bash command\nls' });
  assert.deepEqual(menuExpectOf({ question: 'Which?' }), { question: 'Which?', context: '' });
});

test('a malformed expect is refused, never silently dropped', () => {
  for (const bad of ['x', 3, { context: 'c' }, { question: 7 }, { question: 'q', context: 5 },
                     { question: 'q'.repeat(65537) }, { question: 'q', context: 'c'.repeat(65537) }]) {
    assert.equal(menuExpectOf(bad), 'bad', JSON.stringify(bad).slice(0, 60));
  }
});

test('a long permission command still fits (the detector keeps up to 120 pane lines)', () => {
  const context = 'Bash command\n' + 'x'.repeat(20_000);
  assert.deepEqual(menuExpectOf({ question: 'Do you want to proceed? [Bash]', context }),
    { question: 'Do you want to proceed? [Bash]', context });
});
