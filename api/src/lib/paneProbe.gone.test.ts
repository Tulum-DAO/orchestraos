/**
 * An open page on an agent with no tmux window (the assistant, a retired seat) must not flood the API's log or fork
 * tmux every second: tmux's stderr is never let through, a miss is cached per name for MISS_TTL_MS, and a window that
 * comes back is seen as soon as that window passes.
 *
 * Run: npx tsx --test src/lib/paneProbe.gone.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { paneInfo, paneGone, forgetPane, setPaneProbeDeps, MISS_TTL_MS, TMUX_EXEC, type PaneDeps } from './paneProbe.js';

function fake() {
  let t = 1_000_000;
  const state = { up: new Set<string>(), calls: [] as string[] };
  const d: PaneDeps = {
    tmux: (args) => {
      const name = args[args.indexOf('-t') + 1];
      state.calls.push(name);
      if (!state.up.has(name)) throw new Error(`can't find window: ${name}`);
      return '4242 %7 1\n';
    },
    readText: () => { throw new Error('ENOENT'); },
    readDir: (p) => { if (p === '/proc/4242/task') return ['4242']; throw new Error('ENOENT'); },
    readLink: () => { throw new Error('ENOENT'); },
    now: () => t,
  };
  setPaneProbeDeps(d);
  return { state, advance: (ms: number) => { t += ms; } };
}

test("tmux's stderr never reaches the API's log", () => {
  assert.deepEqual(TMUX_EXEC.stdio, ['ignore', 'pipe', 'ignore']);
});

test('a missing window costs one tmux call per MISS_TTL_MS, per name', () => {
  const f = fake();
  for (let i = 0; i < 20; i++) { assert.equal(paneGone('pm-demo'), true); f.advance(200); }   // 4 s of polling
  assert.deepEqual(f.state.calls, ['pm-demo']);
  assert.equal(paneGone('qa-demo'), true);                                                   // its own entry
  assert.deepEqual(f.state.calls, ['pm-demo', 'qa-demo']);
  f.advance(MISS_TTL_MS);
  paneGone('pm-demo');
  assert.equal(f.state.calls.filter((n) => n === 'pm-demo').length, 2);
});

test('a window that comes back is seen once the miss expires, and then cached as a hit', () => {
  const f = fake();
  assert.equal(paneGone('dev-demo'), true);
  f.state.up.add('dev-demo');
  assert.equal(paneGone('dev-demo'), true);                  // still inside the miss window
  f.advance(MISS_TTL_MS + 1);
  assert.deepEqual(paneInfo('dev-demo'), { panePid: '4242', paneId: '7' });
  const n = f.state.calls.length;
  paneInfo('dev-demo');
  assert.equal(f.state.calls.length, n);                     // a hit, not another call
});

test('forgetPane drops a cached miss too', () => {
  const f = fake();
  paneGone('dev-demo');
  forgetPane('dev-demo');
  paneGone('dev-demo');
  assert.equal(f.state.calls.length, 2);
});
