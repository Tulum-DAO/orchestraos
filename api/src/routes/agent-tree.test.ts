/**
 * GET /api/agents/:id/tree (+ /tree/stream), the subagent tree. Handlers driven directly with fake
 * req/res and injected deps (agent-send.test.ts pattern): no HTTP server, no python.
 *
 * Run: npx tsx --test src/routes/agent-tree.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'events';
import { PassThrough } from 'stream';
import type { Request, Response } from 'express';
import { existsSync } from 'fs';
import { handleTree, subscribeTree, liveWatchCount, END_FRAME, publicTree, treeArgs, TREE_PY, type TreeDeps, type WatchChild } from './agent-tree.js';
import { codePath } from '../lib/codeRoot.js';

function fakeRes() {
  const calls: { status?: number; json?: any } = {};
  const res = {
    status(code: number) { calls.status = code; return res; },
    json(body: any) { calls.json = body; return res; },
  } as unknown as Response;
  return { res, calls };
}

const req = (id: string, query: Record<string, string> = {}) => ({ params: { id }, query } as unknown as Request);

function fakeChild() {
  const ee = new EventEmitter() as EventEmitter & WatchChild & { killed: boolean };
  const out = new PassThrough();
  (ee as any).stdout = out;
  ee.killed = false;
  (ee as any).kill = () => { ee.killed = true; return true; };
  return { child: ee, out };
}

const TREE = { agent_id: 'sid', depth: 0, type: 'seat', label: 'pm-x', state: 'waiting', children: [] };

test('snapshot: the tree as agent_tree.py returns it', async () => {
  const { res, calls } = fakeRes();
  await handleTree(req('pm-x'), res, { runTree: async () => TREE, spawnWatch: () => { throw new Error('no'); } });
  assert.equal(calls.status, undefined);
  assert.deepEqual(calls.json, { ...TREE, all_finished: false });
});

test('snapshot: ?all=1 asks agent_tree.py for every finished node and says so on the root', async () => {
  const asked: boolean[] = [];
  const deps: TreeDeps = { runTree: async (_id, all) => { asked.push(all); return TREE; }, spawnWatch: () => { throw new Error('no'); } };
  const a = fakeRes();
  await handleTree(req('pm-x', { all: '1' }), a.res, deps);
  const b = fakeRes();
  await handleTree(req('pm-x', { all: 'yes' }), b.res, deps);
  assert.deepEqual(asked, [true, false], 'only all=1 turns it on');
  assert.equal(a.calls.json.all_finished, true);
  assert.equal(b.calls.json.all_finished, false);
});

test('publicTree drops transcript_path (absolute host paths) at every depth', () => {
  const t = { ...TREE, transcript_path: '/home/u/.claude/x.jsonl', children: [{ ...TREE, depth: 1, transcript_path: '/home/u/y', children: [] }] };
  const p = publicTree(t, false) as any;
  assert.equal('transcript_path' in p, false);
  assert.equal('transcript_path' in p.children[0], false);
  assert.equal(p.all_finished, false);
  assert.equal('all_finished' in p.children[0], false, 'root only');
  assert.equal(publicTree(null, true), null);
  assert.equal(t.transcript_path, '/home/u/.claude/x.jsonl', 'the input is not mutated');
});

test('treeArgs: --all-finished only for ?all=1, --watch for the stream child', () => {
  assert.deepEqual(treeArgs('pm-x', false), [TREE_PY, 'pm-x', '--json']);
  assert.deepEqual(treeArgs('pm-x', true), [TREE_PY, 'pm-x', '--json', '--all-finished']);
  assert.deepEqual(treeArgs('pm-x', true, 2), [TREE_PY, 'pm-x', '--json', '--all-finished', '--watch', '2']);
});

test('TREE_PY is code: it resolves in the checkout, not the data dir', () => {
  assert.equal(TREE_PY, codePath('scripts', 'agent_tree.py'));
  assert.ok(existsSync(TREE_PY), TREE_PY);
});

test('snapshot: no Claude session -> 404, unsafe id -> 400 and nothing runs', async () => {
  const ran: string[] = [];
  const deps: TreeDeps = { runTree: async (id) => { ran.push(id); return null; }, spawnWatch: () => { throw new Error('no'); } };
  const a = fakeRes();
  await handleTree(req('codex-y'), a.res, deps);
  assert.equal(a.calls.status, 404);
  const b = fakeRes();
  await handleTree(req('../../etc/passwd'), b.res, deps);
  assert.equal(b.calls.status, 400);
  assert.deepEqual(ran, ['codex-y']);
});

test('stream: one child per seat, frames only on change, late joiner gets the current tree, last leave kills it', async () => {
  const { child, out } = fakeChild();
  let spawned = 0;
  const deps: TreeDeps = { runTree: async () => null, spawnWatch: () => { spawned++; return child; } };
  const a: string[] = [];
  const b: string[] = [];
  const offA = subscribeTree('pm-x', (f) => a.push(f), deps);
  out.write(JSON.stringify(TREE) + '\n');
  out.write(JSON.stringify(TREE) + '\n');                         // unchanged: no frame
  await new Promise((r) => setImmediate(r));
  const offB = subscribeTree('pm-x', (f) => b.push(f), deps);
  const changed = { ...TREE, state: 'running' };
  out.write(JSON.stringify(changed).slice(0, 20));               // a split line is held until whole
  await new Promise((r) => setImmediate(r));
  out.write(JSON.stringify(changed).slice(20) + '\n');
  await new Promise((r) => setImmediate(r));
  assert.equal(spawned, 1);
  assert.equal(a.length, 2);
  assert.ok(a[0].startsWith('event: snapshot\ndata: ') && a[1].includes('"running"'));
  assert.ok(a[0].includes('"all_finished":false'));
  assert.equal(b.length, 2);                                     // the current tree at once, then the change
  assert.ok(b[0].includes('"waiting"') && b[1].includes('"running"'));
  offA();
  assert.equal(child.killed, false);
  offB();
  assert.equal(child.killed, true);
  assert.equal(liveWatchCount(), 0);
});

test('stream: ?all=1 is its own watch child (--all-finished), frames say all_finished, paths dropped', async () => {
  const kids: { all: boolean; c: ReturnType<typeof fakeChild> }[] = [];
  const deps: TreeDeps = { runTree: async () => null, spawnWatch: (_id, all) => { const c = fakeChild(); kids.push({ all, c }); return c.child; } };
  const plain: string[] = [];
  const all: string[] = [];
  const off1 = subscribeTree('pm-w', (f) => plain.push(f), deps);
  const off2 = subscribeTree('pm-w', (f) => all.push(f), deps, true);
  assert.deepEqual(kids.map((k) => k.all), [false, true]);
  kids[1].c.out.write(JSON.stringify({ ...TREE, transcript_path: '/home/u/z' }) + '\n');
  kids[1].c.out.write('null\n');
  await new Promise((r) => setImmediate(r));
  assert.equal(plain.length, 0);
  assert.equal(all.length, 2);
  assert.ok(all[0].includes('"all_finished":true') && !all[0].includes('transcript_path'));
  assert.equal(all[1], 'event: snapshot\ndata: null\n\n', 'no session: a null frame');
  off1(); off2();
  assert.equal(liveWatchCount(), 0);
});

test('stream: the child exiting ends every client and frees the seat', async () => {
  const { child } = fakeChild();
  const got: string[] = [];
  subscribeTree('pm-z', (f) => got.push(f), { runTree: async () => null, spawnWatch: () => child });
  child.emit('exit');
  assert.ok(got.at(-1)?.startsWith('event: end'));
  assert.equal(liveWatchCount(), 0);
});

test('stream: a watcher that cannot spawn (error event) ends its clients once and frees the seat, no crash', async () => {
  const { child } = fakeChild();
  const got: string[] = [];
  subscribeTree('pm-e', (f) => got.push(f), { runTree: async () => null, spawnWatch: () => child });
  child.emit('error', Object.assign(new Error('spawn python3 ENOENT'), { code: 'ENOENT' }));
  child.emit('exit');                                            // node may also emit exit: still one end frame
  assert.deepEqual(got, [END_FRAME]);
  assert.equal(liveWatchCount(), 0);
});
