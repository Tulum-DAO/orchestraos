/**
 * POST /api/agents/:id/dev {on, force?}: the body the gateway's /agent-dev takes. Only a real boolean switches a
 * seat between its headless process and its TUI; force (Back to chat over a busy terminal) is opt-in.
 *
 * Run: npx tsx --test src/routes/agents.dev.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { devRequestOf } from './agents.js';

test('on is required and must be a boolean', () => {
  for (const bad of [undefined, null, {}, { on: 'true' }, { on: 1 }, 'x']) assert.equal(devRequestOf(bad), 'bad');
});

test('force only when it is exactly true', () => {
  assert.deepEqual(devRequestOf({ on: true }), { on: true, force: false });
  assert.deepEqual(devRequestOf({ on: false, force: 'yes' }), { on: false, force: false });
  assert.deepEqual(devRequestOf({ on: false, force: true }), { on: false, force: true });
});

test("a headless seat's Stop (Escape) goes to the CLI's interrupt, not into the pane", async () => {
  const { stopGoesToInterrupt } = await import('./agents.js');
  const hl = new Set(['dev-demo']);
  assert.equal(stopGoesToInterrupt('dev-demo', 'escape', hl, () => false), true);
  assert.equal(stopGoesToInterrupt('dev-demo', 'Esc', hl, () => false), true);
  assert.equal(stopGoesToInterrupt('dev-demo', 'escape', hl, () => true), false);    // in Dev: the TUI's own Esc
  assert.equal(stopGoesToInterrupt('dev-demo', 'enter', hl, () => false), false);
  assert.equal(stopGoesToInterrupt('pm-demo', 'escape', hl, () => false), false);    // a tmux seat
});

test('GET /dev: a headless seat says whether its terminal is open; a tmux seat is not headless', async () => {
  const { devInfoOf } = await import('./agents.js');
  const hl = new Set(['dev-demo']);
  assert.deepEqual(devInfoOf('dev-demo', hl, () => true), { headless: true, dev: true });
  assert.deepEqual(devInfoOf('dev-demo', hl, () => false), { headless: true, dev: false });
  assert.deepEqual(devInfoOf('pm-demo', hl, () => true), { headless: false, dev: false });
});
