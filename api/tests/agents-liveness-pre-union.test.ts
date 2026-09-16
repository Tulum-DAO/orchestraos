/**
 * Liveness-pre-union reader bug (gm msg_7d936165, same class as R3) — RED-first.
 *
 * BUG: the route computes `isLocalAlive = machine === 'vps' ? localSessions.has(...)
 * : false`, so a DB-union seat whose machine resolves undefined/'unknown' (NULL
 * lineages.machine, or a flat row without the field) reads alive:false even while
 * its tmux session is LIVE on this box. Liveness must be derived from observed
 * local liveness FIRST; the machine string is a label, not evidence.
 *
 * BUILD-AND-HOLD: prepped in parallel per gm; deploy waits for the operator's greenlight
 * on the codex-dev-1 orphan repair + gm's gate.
 *
 * Run: cd api && npx tsx --test tests/agents-liveness-pre-union.test.ts
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { resolveMachineAndLiveness } from '../src/routes/agents-identity.js';

const LOCAL = new Set(['seat-a', 'gm']);

test('undefined machine + locally live tmux => alive, machine=vps (the bug case)', () => {
  const r = resolveMachineAndLiveness(undefined, 'seat-a', LOCAL);
  assert.equal(r.alive, true);
  assert.equal(r.machine, 'vps');
});

test("machine 'unknown' + locally live => alive true", () => {
  const r = resolveMachineAndLiveness('unknown', 'gm', LOCAL);
  assert.equal(r.alive, true);
  assert.equal(r.machine, 'vps');
});

test('vps machine + not live => alive false, label kept', () => {
  const r = resolveMachineAndLiveness('vps', 'dead-seat', LOCAL);
  assert.equal(r.alive, false);
  assert.equal(r.machine, 'vps');
});

test('mac machine + not locally live => alive false, mac label preserved (unchanged semantics)', () => {
  const r = resolveMachineAndLiveness('mac', 'mac-seat', LOCAL);
  assert.equal(r.alive, false);
  assert.equal(r.machine, 'mac');
});

test('mac machine but session IS locally live => local evidence wins', () => {
  const r = resolveMachineAndLiveness('mac', 'seat-a', LOCAL);
  assert.equal(r.alive, true);
});

test('undefined machine + not live => machine=unknown, alive false (no fabricated label)', () => {
  const r = resolveMachineAndLiveness(undefined, 'nowhere-seat', LOCAL);
  assert.equal(r.alive, false);
  assert.equal(r.machine, 'unknown');
});
