/**
 * Registry-scoped dashboard (gm ruling msg_9f04c5f0) — RED-first.
 *
 * tmux is host-global: /api/agents unioned every tmux session on the machine as an
 * `unregistered:<name>` chip, so a second instance beside a live fleet showed (and could
 * message) foreign seats. The union is now OFF unless [dashboard]
 * show_unregistered_sessions = true.
 *
 * Run: cd api && npx tsx --test tests/agents-unregistered-gate.test.ts
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { discoverUnregistered } from '../src/routes/agents-identity.js';

const LOCAL = new Set(['gm', 'foreign-shell', 'session-12', 'ob']);
const REGISTERED = new Set(['gm', 'ob']);

test('gate off (default): no unregistered chips at all', () => {
  assert.deepEqual(discoverUnregistered(LOCAL, REGISTERED, false), []);
});

test('gate on: foreign sessions become unregistered chips; session-* and registered skipped', () => {
  const out = discoverUnregistered(LOCAL, REGISTERED, true);
  assert.deepEqual(out.map((a) => a.id), ['unregistered:foreign-shell']);
  assert.equal(out[0].unregistered, true);
  assert.equal(out[0].tmux_session, 'foreign-shell');
});
