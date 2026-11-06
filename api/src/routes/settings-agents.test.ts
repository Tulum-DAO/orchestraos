/**
 * /api/settings/agents (routes/settings-agents.ts): the override counts from seat-settings.json, the process list for
 * members and admins, and Stop: admin only, re-checked, logged.
 * Run: npx tsx --test src/routes/settings-agents.test.ts   (from api/)
 */
import { testTmpDir } from '../lib/testTmp.js';
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import express from 'express';
import type { AddressInfo } from 'node:net';
import { mkdirSync, writeFileSync, readFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { createSettingsAgentsRouter, STOP_LOG_KEY } from './settings-agents.js';
import { commandHash } from '../lib/backgroundProcesses.js';
import type { ProcInfo, ProcSource } from '../lib/backgroundProcesses.js';

const d = testTmpDir('settings-agents-');
mkdirSync(join(d, 'state'));
writeFileSync(join(d, 'state', 'seat-settings.json'), JSON.stringify({ seats: {
  'dev-a': { auto_compact: true, compact_at: 70 },
  'qa-b': { compact_at: 99, show_compaction: false },          // 99 is out of range: not its own value
  'gone-seat': { auto_compact: true },                          // not registered any more: not counted
  'grok-c': { auto_compact: true },                             // a runtime without our compaction: not counted
} }));
writeFileSync(join(d, 'state', 'seat-settings-default.json'), JSON.stringify({ default: { auto_compact: false } }));
const registry = { agents: {
  'dev-a': { runtime: 'claude', tmux_session: 'dev-a' }, 'qa-b': {}, 'gm': { runtime: 'claude' }, 'grok-c': { runtime: 'grok' }, arturo: { runtime: 'claude' },
} };

const procs = new Map<number, ProcInfo>([
  [20, { pid: 20, ppid: 1, uid: 1000, startTicks: 5, state: 'S', argv: ['-bash'], cwd: '/', cgroup: '', agentEnv: null }],
  [21, { pid: 21, ppid: 20, uid: 1000, startTicks: 6, state: 'S', argv: ['claude'], cwd: '/', cgroup: '', agentEnv: 'dev-a' }],
  [100, { pid: 100, ppid: 21, uid: 1000, startTicks: 777, state: 'S', argv: ['node', 'vite', '--port', '5173', '--token=s3cret'], cwd: '/tmp/app', cgroup: '', agentEnv: 'dev-a' }],
]);
const src: ProcSource = {
  uid: 1000, selfPid: 9999, bootTimeSec: 1_800_000_000, clkTck: 100, nowTicks: () => 1000,
  listeners: () => new Map([...new Map([[100, [5173]]])].filter(([p]) => procs.has(p))), proc: (pid) => procs.get(pid) ?? null,
  tmuxPanes: async () => { reads++; await gate; return new Map([[20, 'dev-a']]); },
};
let role: 'admin' | 'member' = 'admin';
let clockMs = 1_000_000;
let reads = 0;
let gate: Promise<void> = Promise.resolve();
const HASH = commandHash(['node', 'vite', '--port', '5173', '--token=s3cret']);
const sent: [number, string][] = [];
const app = express();
app.use(express.json());
app.use('/api/settings/agents', createSettingsAgentsRouter({
  dataDir: () => d, registry: () => registry, role: () => role, who: () => 'sam@example.com',
  source: () => src, supported: () => true, options: () => ({ codeRoot: '/opt/orchestraos', installPorts: [8888] }),
  kill: (pid, sig) => { sent.push([pid, sig]); procs.delete(pid); }, sleep: async () => {},
  now: () => new Date('2026-11-03T10:00:00.000Z'), graceMs: 500, pollMs: 250, settleMs: 0, clock: () => clockMs,
}));
const server = app.listen(0);
after(() => server.close());
const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}/api/settings/agents`;
const getJson = async (p: string) => (await fetch(`${base}${p}`)).json() as Promise<any>;
const stop = async (body: unknown) => {
  const r = await fetch(`${base}/processes/stop`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
  return { status: r.status, body: await r.json() as any };
};
const log = () => existsSync(join(d, 'state', 'settings-log.jsonl'))
  ? readFileSync(join(d, 'state', 'settings-log.jsonl'), 'utf-8').trim().split('\n').map((l) => JSON.parse(l)) : [];

test('override counts: registered rotating agents only; a seat counts for a field only with a valid value of its own', async () => {
  const o = await getJson('/overrides');
  assert.equal(o.ok, true);
  assert.deepEqual(o.agents, ['dev-a', 'gm', 'qa-b']);          // not grok-c (no rotation), not arturo, not gone-seat
  assert.equal(o.total, 3);
  assert.deepEqual(o.rows['agents.autoCompact'], [{ id: 'dev-a', value: true }]);
  assert.deepEqual(o.rows['agents.compactAt'], [{ id: 'dev-a', value: 70 }]);
  assert.deepEqual(o.rows['agents.showCompaction'], [{ id: 'qa-b', value: false }]);
  assert.ok(!('agents.defaultModel' in o.rows));
  assert.match(o.notCounted['agents.defaultModel'], /keeps the model it was made with/);
});

test('a member sees the list without Stop, and a member\'s stop is refused with 403 (nothing sent, nothing logged)', async () => {
  role = 'member';
  try {
    const l = await getJson('/processes');
    assert.deepEqual([l.ok, l.supported, l.canStop, l.processes.length], [true, true, false, 1]);
    assert.deepEqual([l.processes[0].pid, l.processes[0].agent, l.processes[0].ports, l.processes[0].program], [100, 'dev-a', [5173], 'node']);
    // a member gets the program name and ports, never the command line or its hash
    assert.ok(!('command' in l.processes[0]) && !('commandHash' in l.processes[0]) && !('detail' in l.processes[0]));
    assert.doesNotMatch(JSON.stringify(l), /s3cret|--token/);
    const r = await stop({ pid: 100, startTicks: 777, commandHash: HASH });
    assert.deepEqual([r.status, r.body.error], [403, 'Only an admin can stop a program']);
    assert.deepEqual(sent, []);
    assert.equal(log().length, 0);
  } finally { role = 'admin'; }
});

test('an admin sees the masked command and its hash; a stale start time or another hash is refused (409); the right one stops, logged masked', async () => {
  clockMs += 10_000;
  const l = await getJson('/processes');
  assert.equal(l.canStop, true);
  assert.deepEqual([l.processes[0].command, l.processes[0].commandHash, l.processes[0].detail], ['node vite --port 5173 --token=***', HASH, 'node']);
  assert.equal((await stop({ pid: 100, startTicks: 776, commandHash: HASH })).status, 409);
  assert.equal((await stop({ pid: 100, startTicks: 777, commandHash: 'nope' })).status, 409);
  assert.equal((await stop({ pid: 100, startTicks: 777, command: 'node vite --port 5173 --token=s3cret' })).status, 400);   // the raw command is not accepted
  assert.deepEqual(sent, []);
  const r = await stop({ pid: 100, startTicks: 777, commandHash: HASH });
  assert.deepEqual([r.status, r.body.result], [200, 'stopped']);
  assert.deepEqual(sent, [[100, 'SIGTERM']]);
  const last = log().at(-1);
  assert.deepEqual([last.key, last.scope, last.by, last.at, last.value.action, last.value.pid, last.value.agent, last.value.result],
    [STOP_LOG_KEY, 'everyone', 'sam@example.com', '2026-11-03T10:00:00.000Z', 'stop', 100, 'dev-a', 'stopped']);
  assert.equal(last.value.command, 'node vite --port 5173 --token=***');
  assert.doesNotMatch(readFileSync(join(d, 'state', 'settings-log.jsonl'), 'utf-8'), /s3cret/);
  // gone now: a second stop finds nothing to stop, and the list (cache cleared by the stop) is empty at once
  assert.equal((await stop({ pid: 100, startTicks: 777, commandHash: HASH })).status, 409);
  assert.equal((await getJson('/processes')).processes.length, 0);
});

test('the list is read at most every 5 s', async () => {
  procs.set(100, { pid: 100, ppid: 21, uid: 1000, startTicks: 778, state: 'S', argv: ['node', 'vite'], cwd: '/tmp/app', cgroup: '', agentEnv: 'dev-a' });
  assert.equal((await getJson('/processes')).processes.length, 0);   // cached from the read above
  clockMs += 5_001;
  assert.equal((await getJson('/processes')).processes.length, 1);
  procs.delete(100);
});

test('concurrent GETs share one read; a read that was in flight when a Stop ran is not cached', async () => {
  clockMs += 10_000;
  procs.set(100, { pid: 100, ppid: 21, uid: 1000, startTicks: 900, state: 'S', argv: ['node', 'vite'], cwd: '/tmp/app', cgroup: '', agentEnv: 'dev-a' });
  let open!: () => void;
  gate = new Promise((r) => { open = r; });
  reads = 0;
  const a = getJson('/processes'), b = getJson('/processes');
  await new Promise((r) => setTimeout(r, 50));
  assert.equal(reads, 1);                                   // one read for both
  // a Stop now (refused: nothing listed, but it still bumps the generation)
  const st = stop({ pid: 4242, startTicks: 1, commandHash: 'x' });
  await new Promise((r) => setTimeout(r, 50));
  open();
  gate = Promise.resolve();
  const [ra, rb] = await Promise.all([a, b]);
  assert.equal((await st).status, 409);
  assert.deepEqual([ra.ok, rb.ok], [true, true]);
  reads = 0;
  await getJson('/processes');
  assert.equal(reads, 1);                                   // the in-flight result was not cached past the Stop
  await getJson('/processes');
  assert.equal(reads, 1);                                   // and this read is
  procs.delete(100);
});
