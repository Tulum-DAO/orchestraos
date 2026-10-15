/**
 * Lower API CPU (2026-10-15): the transcript route forked tmux + pgrep on every poll.
 * paneProbe answers the same questions from ONE cached tmux call and /proc reads. These pin:
 *  - the same panes as before (first pane's pid for Gemini, active pane's id for the hook file),
 *  - pgrep -P parity (children of every thread), Gemini sid found from child fds,
 *  - the cache: N polls in TTL = 1 tmux call; after TTL or a dead pane = asked again; misses not cached.
 *
 * Run: npx tsx --test src/lib/paneProbe.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { paneInfo, liveGeminiSid, childPids, setPaneProbeDeps, TTL_MS, type PaneDeps } from './paneProbe.js';

const CID = 'aaaaaaaa-0000-4000-8000-00000000000a';

/** A fake host: tmux answers `rows`, /proc holds `procs` (pid -> {tasks: tid -> children, fds}). */
function host(rows: string, procs: Record<string, { tasks: Record<string, string>; fds?: Record<string, string> }>) {
  const calls = { tmux: 0, children: 0 };
  let t = 1_000_000;
  const d: PaneDeps = {
    tmux: (args) => { calls.tmux++; assert.equal(args[0], 'list-panes'); if (rows === 'ERR') throw new Error('no session'); return rows; },
    readText: (p) => {
      const m = p.match(/^\/proc\/(\d+)\/task\/(\d+)\/children$/);
      if (!m || procs[m[1]]?.tasks[m[2]] === undefined) throw new Error('ENOENT ' + p);
      calls.children++;
      return procs[m[1]].tasks[m[2]];
    },
    readDir: (p) => {
      let m = p.match(/^\/proc\/(\d+)\/task$/);
      if (m) { if (!procs[m[1]]) throw new Error('ENOENT'); return Object.keys(procs[m[1]].tasks); }
      m = p.match(/^\/proc\/(\d+)\/fd$/);
      if (m) { if (!procs[m[1]]) throw new Error('ENOENT'); return Object.keys(procs[m[1]].fds || {}); }
      throw new Error('ENOENT ' + p);
    },
    readLink: (p) => {
      const m = p.match(/^\/proc\/(\d+)\/fd\/(\d+)$/);
      const v = m && procs[m[1]]?.fds?.[m[2]];
      if (!v) throw new Error('ENOENT'); return v;
    },
    now: () => t,
  };
  setPaneProbeDeps(d);
  return { calls, advance: (ms: number) => { t += ms; }, procs };
}

const TWO_PANES = '100 %7 0\n200 %9 1\n';
const GEMINI_HOST = {
  '100': { tasks: { '100': '300 ', '101': '301' } },          // pane shell; a 2nd thread forked 301
  '300': { tasks: { '300': '' }, fds: { '0': '/dev/null', '1': 'pipe:[1]' } },
  '301': { tasks: { '301': '' }, fds: { '5': `/home/x/.gemini/antigravity-cli/brain/${CID}/x.jsonl` } },
  '200': { tasks: { '200': '' } },
};

test('same panes as the two old calls: first pane pid (list-panes), active pane id (display-message)', () => {
  host(TWO_PANES, GEMINI_HOST);
  assert.deepEqual(paneInfo('seat'), { panePid: '100', paneId: '9' });
});

test('one pane: its pid and id; no active flag falls back to the first pane', () => {
  host('100 %7 0\n', GEMINI_HOST);
  assert.deepEqual(paneInfo('seat'), { panePid: '100', paneId: '7' });
});

test('children of EVERY thread, as pgrep -P lists them; a gone pid is null', () => {
  host(TWO_PANES, GEMINI_HOST);
  assert.deepEqual(childPids('100'), ['300', '301']);
  assert.deepEqual(childPids('200'), []);
  assert.equal(childPids('999'), null);
});

test('Gemini sid comes from a child fd (found on the 2nd thread\'s child)', () => {
  host(TWO_PANES, GEMINI_HOST);
  assert.equal(liveGeminiSid('seat'), CID);
});

test('no Gemini child: null (a claude-shaped pane)', () => {
  host(TWO_PANES, { '100': { tasks: { '100': '300' } }, '300': { tasks: { '300': '' }, fds: { '3': '/home/x/.claude/projects/p/s.jsonl' } }, '200': { tasks: { '200': '' } } });
  assert.equal(liveGeminiSid('seat'), null);
});

test('CACHE: 20 polls inside the TTL = ONE tmux call and one /proc scan; after the TTL, asked again', () => {
  const h = host(TWO_PANES, GEMINI_HOST);
  for (let i = 0; i < 20; i++) { assert.equal(liveGeminiSid('seat'), CID); paneInfo('seat'); }
  assert.equal(h.calls.tmux, 1);
  assert.equal(h.calls.children, 2);                         // the one scan read both threads' children
  h.advance(TTL_MS + 1);
  liveGeminiSid('seat');
  assert.equal(h.calls.tmux, 2);
});

test('CACHE: a dead pane process is asked of tmux again at once, inside the TTL', () => {
  const h = host(TWO_PANES, GEMINI_HOST);
  paneInfo('seat');
  delete h.procs['100'];                                       // pane respawned / killed
  paneInfo('seat');
  assert.equal(h.calls.tmux, 2);
});

test('CACHE: a miss (no such session) is never cached', () => {
  const h = host('ERR', GEMINI_HOST);
  for (let i = 0; i < 3; i++) assert.deepEqual(paneInfo('gone'), { panePid: null, paneId: null });
  assert.equal(h.calls.tmux, 3);
});

test('CONTROL (cache off = old cost): 20 polls at TTL+1 apart = 20 tmux calls', () => {
  const h = host(TWO_PANES, GEMINI_HOST);
  for (let i = 0; i < 20; i++) { liveGeminiSid('seat'); h.advance(TTL_MS + 1); }
  assert.equal(h.calls.tmux, 20);
});
