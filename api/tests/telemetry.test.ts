/**
 * Build B telemetry API — RED-first integration tests.
 *
 * The headline: the COURT-SCRUB DISPOSITION at the API boundary, proven THROUGH
 * the real B1 Python bridge (telemetry-court -> api_bridge.py -> LineageFlagStore
 * / token_extractor). This is the "reuse, don't re-implement" RED: a flagged
 * lineage streamed through the API surfaces a block frame with ZERO verbatim
 * bytes — even when court-contaminated bytes sit in the delta log on disk.
 *
 * Also: BLOCKING-1 (unresolved lineage => block), BLOCKING-3 (tenant 403 on the
 * stream endpoints), cross-runtime matrix (claude + codex), latency budget.
 *
 * Run: cd api && npx tsx --test tests/telemetry.test.ts
 * (env ORCHESTRA_DIR is pointed at the worktree root so the bridge resolves.)
 */
import { testTmpDir } from '../src/lib/testTmp.js';
import { test, before } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync, readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const WORKTREE = join(HERE, '..', '..');                 // api/tests -> repo root
const FIX = join(WORKTREE, 'contract', 'transcript', 'fixtures');

// Point the API + bridge at the worktree BEFORE importing the services.
process.env.ORCHESTRA_DIR = WORKTREE;

let realtimeDir: string;
let flagsPath: string;

function courtBody(provider: 'claude' | 'codex' | 'gemini'): string {
  const red = JSON.parse(readFileSync(join(FIX, provider, 'a0-court-red.json'), 'utf-8'));
  const i = red.input;
  return i.assistant_text_block || i.response_item_text || i.step_payload_decoded_text;
}

function writeStatus(seats: Record<string, any>) {
  writeFileSync(join(realtimeDir, 'status.json'),
    JSON.stringify({ schema: 'realtime-status/v1', ts: Date.now() / 1000, seats }));
}
function writeDeltaLog(session: string, frames: { seq: number; text: string }[]) {
  mkdirSync(join(realtimeDir, 'deltas'), { recursive: true });
  writeFileSync(join(realtimeDir, 'deltas', session + '.log'),
    frames.map((f) => JSON.stringify({ seq: f.seq, ts: 1, text: f.text })).join('\n') + '\n');
}

before(() => {
  realtimeDir = testTmpDir('rt-');
  process.env.ORCHESTRA_REALTIME_DIR = realtimeDir;
  const ftmp = testTmpDir('flags-');
  flagsPath = join(ftmp, 'lineage_flags.json');
  writeFileSync(flagsPath, JSON.stringify({
    schema: 'lineage-flags/v1', flagged: { 'dirty-root': { reason: 'court' } },
  }));
  process.env.LINEAGE_FLAGS_PATH = flagsPath;

  // A claude clean seat, a codex FLAGGED seat (cross-runtime matrix).
  writeStatus({
    'gm': { session: 'gm', lineage_root: 'clean-root', runtime: 'claude', status: 'streaming', ts: 1 },
    'codex-x': { session: 'codex-x', lineage_root: 'dirty-root', runtime: 'codex', status: 'streaming', ts: 1 },
  });
  writeDeltaLog('gm', [{ seq: 1, text: 'hello' }, { seq: 2, text: 'world' }]);
  // Simulate a hypothetical leak: contaminated bytes ON DISK for the flagged
  // seat. The API boundary MUST still block (defense-in-depth over the write-gate).
  writeDeltaLog('codex-x', [{ seq: 1, text: courtBody('codex') }]);
});

// --- store reads (body-free status + court-gated frames) --------------------

test('readFleetStatus + resolveLineageRoot from the snapshot', async () => {
  const store = await import('../src/services/telemetry-store.js');
  const snap = store.readFleetStatus();
  assert.equal(snap?.schema, 'realtime-status/v1');
  assert.deepEqual(Object.keys(snap!.seats).sort(), ['codex-x', 'gm']);
  assert.equal(store.resolveLineageRoot('gm'), 'clean-root');
  assert.equal(store.resolveLineageRoot('codex-x'), 'dirty-root');
  // BLOCKING-1: absent session => null (route must fail closed)
  assert.equal(store.resolveLineageRoot('ghost'), null);
});

// --- COURT BOUNDARY RED (headline) — through the real B1 python bridge -------

test('clean lineage streams clean deltas', async () => {
  const { DeltaStreamController } = await import('../src/services/telemetry-stream.js');
  const c = new DeltaStreamController('gm');
  const f = await c.open();
  assert.equal(f.type, 'delta');
  assert.deepEqual((f as any).frames.map((x: any) => x.text), ['hello', 'world']);
});

test('flagged lineage BLOCKS at the API boundary — zero verbatim leak (codex)', async () => {
  const { DeltaStreamController } = await import('../src/services/telemetry-stream.js');
  const body = courtBody('codex');
  const c = new DeltaStreamController('codex-x');
  const f = await c.open();
  assert.equal(f.type, 'block', 'flagged lineage must block');
  assert.equal((f as any).reason, 'flagged');
  // ZERO verbatim bytes of the contaminated body in the emitted frame
  assert.ok(!JSON.stringify(f).includes(body.slice(0, 24)));
  // ticking NEVER relays the on-disk contaminated frame
  const t = await c.tick();
  assert.equal(t, null);
  assert.ok(c.isBlocked);
});

test('BLOCKING-1: unresolved session fails CLOSED (block) before touching the store', async () => {
  const { DeltaStreamController } = await import('../src/services/telemetry-stream.js');
  const c = new DeltaStreamController('ghost-session');   // absent from snapshot
  const f = await c.open();
  assert.equal(f.type, 'block');
  assert.equal((f as any).reason, 'unresolved-lineage');
});

test('fail-CLOSED when the flag store is absent/corrupt', async () => {
  const saved = process.env.LINEAGE_FLAGS_PATH;
  process.env.LINEAGE_FLAGS_PATH = '/nope/does-not-exist.json';
  try {
    const { DeltaStreamController } = await import('../src/services/telemetry-stream.js');
    const c = new DeltaStreamController('gm');            // clean seat, but store gone
    const f = await c.open();
    assert.equal(f.type, 'block');
    assert.equal((f as any).reason, 'fail-closed');
  } finally {
    process.env.LINEAGE_FLAGS_PATH = saved;
  }
});

// --- BLOCKING-3: per-session tenant scoping ----------------------------------
//
// Scope comes from lib/principal.ts, which honours x-orchestra-* headers ONLY when
// ORCHESTRA_API_TRUST_IDENTITY_HEADERS is on (an authenticating proxy in front), and
// then only for a request that names its user (x-orchestra-user). Without the env,
// every caller is the configured operator and the headers are ignored by design.
// trustsIdentityHeaders() reads the env per call, so a test can switch it.

async function withTrustedHeaders(fn: () => Promise<void> | void): Promise<void> {
  const saved = process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS;
  process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS = '1';
  try {
    await fn();
  } finally {
    if (saved === undefined) delete process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS;
    else process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS = saved;
  }
}

// Proxy-asserted requesters, shaped as principal() reads them.
const ADMIN_H = { 'x-orchestra-user': 'op', 'x-orchestra-role': 'admin', 'x-orchestra-allowed-agents': '*' };
const SCOPED_H = { 'x-orchestra-user': 'eve', 'x-orchestra-client': 'no-such-client' };
const LISTED_H = { 'x-orchestra-user': 'bob', 'x-orchestra-allowed-agents': '["gm"]' };

test('tenant scoping (trusted proxy): admin sees all; a client scope is filtered', async () => {
  const scope = await import('../src/services/telemetry-scope.js');
  const seats = { gm: { session: 'gm' }, 'codex-x': { session: 'codex-x' } };
  await withTrustedHeaders(() => {
    const admin = { headers: ADMIN_H } as any;
    assert.equal(scope.canAccessSession(admin, 'gm'), true);
    assert.deepEqual(Object.keys(scope.scopeSeats(admin, seats)).sort(), ['codex-x', 'gm']);
    // A client-scoped requester for a session with NO matching client tag => deny.
    const scoped = { headers: SCOPED_H } as any;
    assert.equal(scope.canAccessSession(scoped, 'gm'), false);
    assert.deepEqual(scope.scopeSeats(scoped, seats), {});
    // An explicit allowlist sees only its own sessions.
    const listed = { headers: LISTED_H } as any;
    assert.equal(scope.canAccessSession(listed, 'gm'), true);
    assert.equal(scope.canAccessSession(listed, 'codex-x'), false);
    // No asserted user => no principal => deny (never admin).
    const anon = { headers: { 'x-orchestra-client': 'no-such-client' } } as any;
    assert.equal(scope.canAccessSession(anon, 'gm'), false);
    assert.equal(scope.canAccessSession({ headers: {} } as any, 'gm'), false);
  });
});

test('tenant scoping (no proxy): scope headers are ignored, the caller is the operator', async () => {
  const scope = await import('../src/services/telemetry-scope.js');
  assert.ok(!process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS, 'run without trusted headers');
  // principal() ignores every x-orchestra-* header here: nothing vouches for them.
  assert.equal(scope.canAccessSession({ headers: {} } as any, 'gm'), true);
  assert.equal(scope.canAccessSession({ headers: SCOPED_H } as any, 'gm'), true);
});

// --- BLOCKING-3 at the HTTP boundary (real Express route) --------------------

test('HTTP: stream + status endpoints 403 a scoped user before any relay', async () => {
  const express = (await import('express')).default;
  const router = (await import('../src/routes/telemetry.js')).default;
  const app = express();
  app.use('/api/telemetry', router);
  const server = app.listen(0);
  await new Promise((r) => server.once('listening', r));
  const port = (server.address() as any).port;
  const base = `http://127.0.0.1:${port}/api/telemetry`;
  try {
    await withTrustedHeaders(async () => {
      // scoped user with no matching client tag => 403 on BOTH the delta stream
      // and the per-agent status, before any court bridge / log relay.
      const s1 = await fetch(`${base}/transcript/gm/stream`, { headers: SCOPED_H });
      assert.equal(s1.status, 403);
      assert.equal((await s1.json()).error, 'forbidden');
      const s2 = await fetch(`${base}/status/gm`, { headers: SCOPED_H });
      assert.equal(s2.status, 403);
      // the fleet list drops the seat for the scoped user
      const l = await fetch(`${base}/status`, { headers: SCOPED_H });
      assert.deepEqual((await l.json()).seats, {});
      // no asserted user => 403, never admin
      const n = await fetch(`${base}/status/gm`, { headers: { 'x-orchestra-client': 'no-such-client' } });
      assert.equal(n.status, 403);
      // admin sees the clean seat's status
      const a = await fetch(`${base}/status/gm`, { headers: ADMIN_H });
      assert.equal(a.status, 200);
      assert.equal((await a.json()).lineage_root, 'clean-root');
    });
  } finally {
    server.close();
  }
});

// --- F1 route-order: /status/stream must not be shadowed by /status/:session --

test('HTTP: /status/stream reaches the SSE route, not the :session param route', async () => {
  const express = (await import('express')).default;
  const router = (await import('../src/routes/telemetry.js')).default;
  const app = express();
  app.use('/api/telemetry', router);
  const server = app.listen(0);
  await new Promise((r) => server.once('listening', r));
  const port = (server.address() as any).port;
  const base = `http://127.0.0.1:${port}/api/telemetry`;
  const ac = new AbortController();
  try {
    // Pre-fix, the param route '/status/:session' is declared first, so
    // '/status/stream' matched it as session='stream' -> 404 JSON no-telemetry
    // and the status SSE was UNREACHABLE. The literal route must win.
    const r = await fetch(`${base}/status/stream`, { signal: ac.signal });
    assert.equal(r.status, 200);
    assert.match(r.headers.get('content-type') || '', /text\/event-stream/);
  } finally {
    ac.abort();
    server.close();
  }
});

// --- latency budget ----------------------------------------------------------

test('latency: court-gated stream open (one bridge spawn) is under 1s', async () => {
  const { DeltaStreamController } = await import('../src/services/telemetry-stream.js');
  const t0 = Date.now();
  await new DeltaStreamController('gm').open();
  assert.ok(Date.now() - t0 < 1000, 'stream open must meet the <=1s delta budget');
});
