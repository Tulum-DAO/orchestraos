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
import { test, before } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { tmpdir } from 'node:os';
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
  realtimeDir = mkdtempSync(join(tmpdir(), 'rt-'));
  process.env.ORCHESTRA_REALTIME_DIR = realtimeDir;
  const ftmp = mkdtempSync(join(tmpdir(), 'flags-'));
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

test('tenant scoping: admin sees all; a client scope is filtered', async () => {
  const scope = await import('../src/services/telemetry-scope.js');
  const admin = { headers: {} } as any;                  // no client scope => '*'
  assert.equal(scope.canAccessSession(admin, 'gm'), true);
  // A client-scoped requester for a session with NO matching client tag => deny.
  const scoped = { headers: { 'x-orchestra-client': 'no-such-client' } } as any;
  assert.equal(scope.canAccessSession(scoped, 'gm'), false);
  // scopeSeats drops the out-of-scope seats
  const seats = { gm: { session: 'gm' }, 'codex-x': { session: 'codex-x' } };
  assert.deepEqual(scope.scopeSeats(scoped, seats), {});
  assert.deepEqual(Object.keys(scope.scopeSeats(admin, seats)).sort(), ['codex-x', 'gm']);
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
    // scoped user with no matching client tag => 403 on BOTH the delta stream
    // and the per-agent status, before any court bridge / log relay.
    const scoped = { 'x-orchestra-client': 'no-such-client' };
    const s1 = await fetch(`${base}/transcript/gm/stream`, { headers: scoped });
    assert.equal(s1.status, 403);
    assert.equal((await s1.json()).error, 'forbidden');
    const s2 = await fetch(`${base}/status/gm`, { headers: scoped });
    assert.equal(s2.status, 403);
    // admin (no scope) sees the clean seat's status
    const a = await fetch(`${base}/status/gm`);
    assert.equal(a.status, 200);
    assert.equal((await a.json()).lineage_root, 'clean-root');
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
