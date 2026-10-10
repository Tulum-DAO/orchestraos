/**
 * gm msg_75776be5: the transition renamed the live seat <id> -> <id>-transition BEFORE spawning, and a
 * failed spawn left it renamed (then reaped an hour later): the seat was lost. A failed spawn must give
 * the old seat its name back.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { transitionSeat, type TransitionDeps, type TransitionResult } from './seatTransition.js';

function fakeTmux(sessions: Set<string>, spawnErr: Error | null, opts: { spawnTakesName?: boolean } = {}) {
  const calls: string[][] = [];
  const deps: TransitionDeps = {
    tmux: (args) => {
      calls.push(args);
      if (args[0] === 'rename-session') {
        const [, , from, to] = args;
        if (!sessions.has(from)) throw new Error(`no session ${from}`);
        sessions.delete(from); sessions.add(to);
      }
    },
    hasSession: (name) => sessions.has(name),
    spawn: (id, cb) => {
      if (!spawnErr || opts.spawnTakesName) sessions.add(id);
      setImmediate(() => cb(spawnErr));
    },
  };
  return { deps, calls };
}

function run(deps: TransitionDeps): Promise<TransitionResult> {
  return new Promise((resolve) => transitionSeat('builder', deps, resolve));
}

test('a successful spawn leaves the old seat aside under <id>-transition', async () => {
  const s = new Set(['builder']);
  const r = await run(fakeTmux(s, null).deps);
  assert.deepEqual(r, { ok: true });
  assert.ok(s.has('builder') && s.has('builder-transition'));
});

test('a FAILED spawn gives the old seat its own name back', async () => {
  const s = new Set(['builder']);
  const r = await run(fakeTmux(s, new Error('spawn-agent.sh: ENOENT')).deps);
  assert.equal(r.ok, false);
  assert.equal(r.restored, true);
  assert.deepEqual([...s], ['builder'], 'the seat is back where it was');
});

test('a failed spawn that still created a session under the name does not clobber it', async () => {
  const s = new Set(['builder']);
  const r = await run(fakeTmux(s, new Error('timed out'), { spawnTakesName: true }).deps);
  assert.equal(r.ok, false);
  assert.equal(r.restored, false);
  assert.ok(s.has('builder') && s.has('builder-transition'), 'both kept; nothing renamed over a live session');
});

test('when the session was not under the agent id, nothing is moved and nothing is restored', async () => {
  const s = new Set(['builder-g7']);
  const { deps, calls } = fakeTmux(s, new Error('boom'));
  const r = await run(deps);
  assert.equal(r.ok, false);
  assert.equal(r.restored, false);
  assert.equal(calls.filter((c) => c[0] === 'rename-session').length, 1, 'only the first, failed rename was tried');
  assert.deepEqual([...s], ['builder-g7']);
});

test('the transition route goes through transitionSeat and never renames the seat inline', async () => {
  const { readFileSync } = await import('fs');
  const { join } = await import('path');
  const src = readFileSync(join(import.meta.dirname, '..', 'routes', 'agent-state.ts'), 'utf8');
  assert.match(src, /transitionSeat\(agentId,/);
  assert.doesNotMatch(src, /'rename-session'/, 'renames live in lib/seatTransition.ts, with the rollback');
});
