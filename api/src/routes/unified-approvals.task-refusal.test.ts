/**
 * Approve / Deny on a task for you (kind 'human_task') is refused with a readable reason that names the verbs and the
 * dismiss, never a bare 400; and the ✕ (dismiss) is one endpoint for every card kind, carrying where and why to the
 * gateway with WHERE as this route's own word and never a client-supplied `by` (operator order 2026-11-25, gm
 * msg_b083c465: Deny on a commitment card showed "400" and nothing else; review of #631: a client could name who dismissed).
 *
 * The real router runs against a temp data dir (fixture, imported first): the refusal comes from the real approval.py on
 * a real pending human_task row; the dismiss reaches an http stub standing in for the gateway, which records what the
 * route sent it.
 *
 * Run: npx tsx --test src/routes/unified-approvals.task-refusal.test.ts   (from api/)
 */
import { DATA_DIR, TOKEN } from './unified-approvals.task-refusal.fixture.js';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import express from 'express';
import http from 'node:http';
import { execFileSync } from 'node:child_process';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { AddressInfo } from 'node:net';
import router, { refusalBody, dismissFieldsOf } from './unified-approvals.js';

const SCRIPTS = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..', 'scripts');
const TASK_VERDICT = { ok: false, status: 400, error: 'human_task verbs are done|cant|snooze', id: 'apr_task1' };

// ---- the pieces ----------------------------------------------------------------------------------------------------

test('the gateway\'s human_task verdict becomes a readable refusal naming the verbs and the dismiss', () => {
  const b = refusalBody(TASK_VERDICT as any, 'apr_task1', 'deny');
  assert.equal(b.kind, 'human_task');
  assert.deepEqual(b.verbs, ['done', 'cant', 'snooze']);
  assert.equal(b.dismiss, '/api/approvals/unified/apr_task1/dismiss');
  assert.match(String(b.error), /task for you/i);
  assert.match(String(b.error), /done, cant or snooze/);
  assert.match(String(b.error), /dismiss/i);
  assert.equal(b.id, 'apr_task1');
});

test('any other refusal keeps its own words (and its why)', () => {
  const b = refusalBody({ ok: false, status: 400, error: 'not an option', why: 'option 9 of 3' } as any, 'apr_x', 'approve');
  assert.equal(b.error, 'not an option');
  assert.equal(b.why, 'option 9 of 3');
  assert.ok(!('kind' in b) && !('verbs' in b));
});

test('the dismiss body to the gateway: this route\'s surface (web, always), the client\'s reason, never a by', () => {
  assert.deepEqual(dismissFieldsOf({ surface: ' web ', reason: ' old, remove ', by: 'operator' }), { surface: 'web', reason: 'old, remove' });
  assert.deepEqual(dismissFieldsOf({ surface: 'chat', reason: 'r' }), { surface: 'web', reason: 'r' }, 'no client hint is read, chat included');
  assert.deepEqual(dismissFieldsOf({ surface: 'watch', by: 'pm-x' }), { surface: 'web' }, 'a claim to be the watch is the web');
  assert.deepEqual(dismissFieldsOf({ surface: 7, reason: ['x'] }), { surface: 'web' });
  assert.deepEqual(dismissFieldsOf(undefined), { surface: 'web' });
  assert.equal(dismissFieldsOf({ reason: 'r'.repeat(900) }).reason?.length, 500);
});

// ---- the real router over HTTP ---------------------------------------------------------------------------------------

function py(code: string): string {
  return execFileSync('python3', ['-c', code], { encoding: 'utf-8', timeout: 20000, env: { ...process.env, PYTHONPATH: SCRIPTS } }).trim();
}

function pendingTask(): string {
  return py(`from approval_schema import ApprovalStore
import os
s = ApprovalStore(db_path=os.environ['APPROVAL_DB_PATH']); s.migrate()
print(s.create(from_agent='postcall-dev', question='Call Dana back', worker_kind='pane',
               options=['done', 'cant', 'snooze'], kind='human_task', block_task='Call Dana back'))`);
}

function rowStatus(id: string): string {
  return py(`from approval_schema import ApprovalStore
import os
print(ApprovalStore(db_path=os.environ['APPROVAL_DB_PATH']).get(${JSON.stringify(id)})['status'])`);
}

interface Seen { method?: string; url?: string; auth?: string; body?: string }

/** A gateway stand-in: records the one request it gets and answers as told. */
function gatewayStub(reply: { status: number; body: unknown }, seen: Seen): Promise<http.Server> {
  return new Promise((resolve) => {
    const srv = http.createServer((req, res) => {
      let data = '';
      req.on('data', (c) => (data += c));
      req.on('end', () => {
        seen.method = req.method; seen.url = req.url; seen.auth = req.headers.authorization; seen.body = data;
        res.writeHead(reply.status, { 'content-type': 'application/json' });
        res.end(JSON.stringify(reply.body));
      });
    });
    srv.listen(0, '127.0.0.1', () => resolve(srv));
  });
}

async function withApp(fn: (call: (method: string, path: string, body?: unknown) => Promise<{ status: number; body: any }>) => Promise<void>) {
  const app = express(); app.use(express.json()); app.use('/api/approvals/unified', router);
  const server = app.listen(0);
  const port = (server.address() as AddressInfo).port;
  try {
    await fn(async (method, path, body) => {
      const r = await fetch(`http://127.0.0.1:${port}/api/approvals/unified${path}`, {
        method, headers: { 'content-type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body),
        signal: AbortSignal.timeout(30000),
      });
      return { status: r.status, body: await r.json().catch(() => ({})) };
    });
  } finally { server.close(); }
}

test('the fence holds: the router reads the temp data dir', () => {
  assert.equal(process.env.ORCHESTRA_DIR, DATA_DIR);
  assert.equal(process.env.APPROVAL_DB_PATH, join(DATA_DIR, 'state', 'tasks.db'));
});

test('Deny and Approve on a real pending task for you answer 400 with the readable body, and the card stays pending', async () => {
  const id = pendingTask();
  await withApp(async (call) => {
    const deny = await call('POST', `/${id}/deny`, { reason: 'no' });
    assert.equal(deny.status, 400);
    assert.equal(deny.body.kind, 'human_task');
    assert.deepEqual(deny.body.verbs, ['done', 'cant', 'snooze']);
    assert.equal(deny.body.dismiss, `/api/approvals/unified/${id}/dismiss`);
    assert.match(deny.body.error, /task for you/i);
    assert.equal(deny.body.id, id);
    const approve = await call('POST', `/${id}/approve`, {});
    assert.equal(approve.status, 400);
    assert.equal(approve.body.kind, 'human_task');
    assert.match(approve.body.error, /approve does not apply/);
  });
  assert.equal(rowStatus(id), 'pending');
});

test('the dismiss route sends the gateway its own surface and the reason, with the bearer; a client by or surface never rides', async () => {
  const seen: Seen = {};
  const gw = await gatewayStub({ status: 200, body: { ok: true, applied: true, id: 'apr_task0001' } }, seen);
  process.env.WATCH_GATEWAY_URL = `http://127.0.0.1:${(gw.address() as AddressInfo).port}`;
  try {
    await withApp(async (call) => {
      const r = await call('POST', '/apr_task0001/dismiss', { surface: 'watch', by: 'operator', reason: ' old, remove ' });
      assert.deepEqual([r.status, r.body.status, r.body.id], [200, 'dismissed', 'apr_task0001']);
      assert.equal(seen.method, 'POST');
      assert.equal(seen.url, '/approvals/apr_task0001/discard');
      assert.equal(seen.auth, `Bearer ${TOKEN}`);
      assert.deepEqual(JSON.parse(seen.body!), { surface: 'web', reason: 'old, remove' });
      await call('POST', '/apr_task0001/dismiss');
      assert.deepEqual(JSON.parse(seen.body!), { surface: 'web' }, 'no body: still this route\'s surface, nothing else');
      await call('POST', '/apr_task0001/dismiss', { surface: 'chat', reason: 'answered in the thread' });
      assert.deepEqual(JSON.parse(seen.body!), { surface: 'web', reason: 'answered in the thread' }, 'chat is a client claim here: web');
    });
  } finally { gw.close(); delete process.env.WATCH_GATEWAY_URL; }
});

test('a menu on the agent\'s screen: the gateway\'s 423 reaches the client as it is, with its error, why and agent', async () => {
  const seen: Seen = {};
  const error = "This menu is on codex-gm's screen: dismiss it on the pane (Escape), which closes this card too.";
  const gw = await gatewayStub({ status: 423, body: { ok: false, error, id: 'apr_menu1', agent: 'codex-gm', why: 'the pane still shows this menu' } }, seen);
  process.env.WATCH_GATEWAY_URL = `http://127.0.0.1:${(gw.address() as AddressInfo).port}`;
  try {
    await withApp(async (call) => {
      const r = await call('POST', '/apr_menu1/dismiss', { reason: 'old' });
      assert.equal(r.status, 423);
      assert.deepEqual([r.body.error, r.body.agent, r.body.why], [error, 'codex-gm', 'the pane still shows this menu']);
    });
  } finally { gw.close(); delete process.env.WATCH_GATEWAY_URL; }
});
