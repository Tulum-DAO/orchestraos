/**
 * REAL-ARTIFACT integration test for the R5 web convergence (SPEC
 * all-model-parity §2.3/§5/§6-NOTE). This is the mandatory pre-cutover proof
 * that REPLACES the static string-placeholder in
 * tests/parity/test_provider_parity_red.py::test_unified_web_api_reads_canonical_store.
 *
 * It boots the ACTUAL route handlers (unified-approvals.ts + the legacy
 * approvals.ts) on a fresh express app, wired to a SCRATCH tasks.db + SCRATCH
 * orchestra dir, and drives them over HTTP. Nothing live is read or written:
 *   ORCHESTRA_DIR         -> a throwaway tmp dir (filesystem stores, inbox)
 *   ORCHESTRA_SCRIPTS_DIR -> the REAL scripts/ (so the REAL approval.py service runs)
 *   APPROVAL_DB_PATH      -> a scratch tasks.db (the canonical store under test)
 *
 * Proves, hermetically:
 *   (a) canonical approval_requests rows appear in the web feed;
 *   (b) a web answer transitions the canonical row pending->answered and NEVER
 *       writes the filesystem store or queue/inbox/;
 *   (c) resolve-everywhere: a row answered via the gateway lane disappears from
 *       the web feed on the next poll, and a web answer disappears from the
 *       canonical (gateway) feed — both directions, no surface-local state.
 *
 * Run:  node_modules/.bin/tsx --test test/unified-approvals-canonical.test.mjs
 */
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, existsSync, readdirSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = join(HERE, '..', '..');           // api/test -> repo root
const SCRIPTS = join(REPO, 'scripts');

let SCRATCH;
let server;
let base;

function py(code) {
  return execFileSync('python3', ['-c', code], { encoding: 'utf-8' }).trim();
}

// Seed a canonical pending row on the scratch DB (test precondition — NOT the
// artifact under assertion; the web route's read/write behaviour is what we assert).
function seed(question, opKey) {
  return py(
    `import os,sys; sys.path.insert(0, ${JSON.stringify(SCRIPTS)});` +
    `from approval_schema import ApprovalStore;` +
    `s=ApprovalStore(os.environ['APPROVAL_DB_PATH']); s.migrate();` +
    `print(s.create(from_agent='tester', question=${JSON.stringify(question)}, worker_kind='node', op_key=${JSON.stringify(opKey)}))`
  );
}

function rowStatus(rid) {
  return py(
    `import os,sys; sys.path.insert(0, ${JSON.stringify(SCRIPTS)});` +
    `from approval_schema import ApprovalStore;` +
    `s=ApprovalStore(os.environ['APPROVAL_DB_PATH']);` +
    `r=s.get(${JSON.stringify(rid)}); print(r['status'] if r else 'MISSING')`
  );
}

// The canonical (gateway) feed as the REAL service emits it.
function canonicalPendingIds() {
  const out = execFileSync('python3', [join(SCRIPTS, 'approval.py'), 'pending', '--json'],
    { encoding: 'utf-8' });
  return JSON.parse(out.trim()).map((r) => r.id);
}

// Answer via the gateway lane (the SAME core the watch/iOS surface uses).
function gatewayAnswer(rid, answer) {
  execFileSync('python3', [join(SCRIPTS, 'approval.py'), 'answer', '--id', rid, '--answer', answer],
    { encoding: 'utf-8' });
}

before(async () => {
  SCRATCH = mkdtempSync(join(tmpdir(), 'r5-webconverge-'));
  mkdirSync(join(SCRATCH, 'state'), { recursive: true });
  process.env.ORCHESTRA_DIR = SCRATCH;
  process.env.ORCHESTRA_SCRIPTS_DIR = SCRIPTS;
  process.env.APPROVAL_DB_PATH = join(SCRATCH, 'state', 'tasks.db');
  process.env.NTFY_TOKEN_FILE = join(SCRATCH, 'no-token');
  delete process.env.G3_ACCEPT_NOTIFY_ENABLED;

  // Import AFTER env is set — the route modules read process.env at load.
  const express = (await import('express')).default;
  const unified = (await import('../src/routes/unified-approvals.ts')).default;
  const legacy = (await import('../src/routes/approvals.ts')).default;
  const app = express();
  app.use(express.json());
  app.use('/api/approvals/unified', unified);
  app.use('/api/approvals', legacy);
  await new Promise((resolve) => { server = app.listen(0, resolve); });
  base = `http://127.0.0.1:${server.address().port}`;
});

after(async () => {
  if (server) await new Promise((r) => server.close(r));
});

test('(a) canonical rows appear in the unified web feed', async () => {
  const rid = seed('web parity A?', 'opk-a');
  const resp = await fetch(`${base}/api/approvals/unified?type=agent`);
  assert.equal(resp.status, 200);
  const body = await resp.json();
  const ids = body.items.map((i) => i.id);
  assert.ok(ids.includes(rid), `canonical row ${rid} must appear in the web feed; got ${ids}`);
  const row = body.items.find((i) => i.id === rid);
  assert.equal(row.status, 'pending');
  assert.equal(row.type, 'agent');
});

test('(a2) canonical rows appear in the legacy /api/approvals feed', async () => {
  const rid = seed('web parity A2?', 'opk-a2');
  const resp = await fetch(`${base}/api/approvals`);
  assert.equal(resp.status, 200);
  const body = await resp.json();
  const ids = body.pending.map((i) => i.id);
  assert.ok(ids.includes(rid), `canonical row ${rid} must appear in the legacy feed; got ${ids}`);
});

test('(b) a web answer transitions the canonical row and writes NO filesystem/inbox', async () => {
  const rid = seed('web parity B?', 'opk-b');
  const resp = await fetch(`${base}/api/approvals/unified/${rid}/approve`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({}),
  });
  assert.equal(resp.status, 200, `approve should succeed; body=${await resp.clone().text()}`);
  const body = await resp.json();
  assert.equal(body.status, 'approved');
  assert.equal(body.type, 'agent');

  // Canonical row transitioned pending -> answered (never silent).
  assert.equal(rowStatus(rid), 'answered');

  // NEVER wrote the deprecated queue/inbox/ lane, nor a filesystem resolved store.
  const inbox = join(SCRATCH, 'queue', 'inbox');
  assert.ok(!existsSync(inbox) || readdirSync(inbox).length === 0,
    'web answer must NOT write queue/inbox/');
  for (const p of [join(SCRATCH, 'state', 'approvals', 'resolved'),
                   join(SCRATCH, 'approvals', 'resolved')]) {
    assert.ok(!existsSync(p) || readdirSync(p).length === 0,
      `web answer must NOT write the filesystem store (${p})`);
  }
});

test('(c1) resolve-everywhere: a WEB answer drops from the canonical (gateway) feed', async () => {
  const rid = seed('web parity C1?', 'opk-c1');
  assert.ok(canonicalPendingIds().includes(rid), 'row present in gateway feed before answer');
  const resp = await fetch(`${base}/api/approvals/unified/${rid}/deny`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ reason: 'not now' }),
  });
  assert.equal(resp.status, 200);
  assert.ok(!canonicalPendingIds().includes(rid),
    'a web answer must resolve on the gateway/canonical feed too (no split-brain)');
});

test('(c2) resolve-everywhere: a GATEWAY answer drops from the web feed', async () => {
  const rid = seed('web parity C2?', 'opk-c2');
  // present on the web feed first
  let body = await (await fetch(`${base}/api/approvals/unified?type=agent`)).json();
  assert.ok(body.items.map((i) => i.id).includes(rid), 'row present on web feed before answer');

  gatewayAnswer(rid, 'approve');   // answered via the SAME core the watch uses

  body = await (await fetch(`${base}/api/approvals/unified?type=agent`)).json();
  assert.ok(!body.items.map((i) => i.id).includes(rid),
    'a gateway answer must resolve on the web feed too (no surface-local state)');
});
