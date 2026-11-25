/**
 * The canonical pending feed is read WITHOUT blocking the event loop, and the unified list reads it at
 * most once per 3 s. Live finding (2026-11-25): every GET /api/approvals ran `approval.py pending --json`
 * with execFileSync (hundreds of ms), so every other request on the API, SSE flushes included, stalled
 * for that long on each dashboard poll.
 *
 * RED-first: readCanonicalPendingAsync did not exist and listItems called the sync reader.
 * Hermetic: ORCHESTRA_SCRIPTS_DIR points at a tmp dir whose approval.py is a fake that counts its calls,
 * sleeps 400 ms and prints two rows; nothing touches the live tree. Run: npx tsx --test <this file> (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, readFileSync, chmodSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const dir = mkdtempSync(join(tmpdir(), 'canonical-async-'));
const counter = join(dir, 'calls');
writeFileSync(counter, '0');
writeFileSync(join(dir, 'approval.py'), `#!/usr/bin/env python3
import json, sys, time
n = int(open(${JSON.stringify(counter)}).read() or 0) + 1
open(${JSON.stringify(counter)}, "w").write(str(n))
time.sleep(0.4)
rows = [{"id": "apr_async0001_0000001", "from_agent": "pm-demo", "question": "ship?", "op_key": "k1", "options": ["approve", "deny"],
         "status": "pending", "created_at": "2026-11-25T00:00:00Z", "kind": None, "summary": None, "risk_level": None,
         "reversibility": None, "feature": None, "provider": None, "menu": None},
        {"id": "apr_async0002_0000002", "from_agent": "pm-demo", "question": "again?", "op_key": "k2", "options": ["approve", "deny"],
         "status": "pending", "created_at": "2026-11-25T00:00:01Z", "kind": None, "summary": None, "risk_level": None,
         "reversibility": None, "feature": None, "provider": None, "menu": None}]
print(json.dumps(rows))
`);
chmodSync(join(dir, 'approval.py'), 0o755);
process.env.ORCHESTRA_SCRIPTS_DIR = dir;          // read at import time by _canonical-approvals.ts
process.env.ORCHESTRA_DIR = dir;

const calls = () => Number(readFileSync(counter, 'utf-8'));

/** How many 20 ms ticks the event loop manages while `work` runs: ~0 when blocked, many when not. */
async function ticksDuring<T>(work: () => Promise<T> | T): Promise<{ ticks: number; result: T }> {
  let ticks = 0;
  const timer = setInterval(() => { ticks += 1; }, 20);
  try {
    const result = await work();
    return { ticks, result };
  } finally {
    clearInterval(timer);
  }
}

test('readCanonicalPendingAsync returns the rows and leaves the event loop running', async () => {
  const mod = await import('./_canonical-approvals.js');
  assert.equal(typeof mod.readCanonicalPendingAsync, 'function', 'the async reader exists');
  const { ticks, result } = await ticksDuring(() => mod.readCanonicalPendingAsync());
  assert.equal(result?.length, 2);
  assert.ok(ticks >= 5, `the loop ticked only ${ticks} times during a 400 ms read: it was blocked`);
});

test('control: the synchronous reader blocks the loop for the whole read', async () => {
  const mod = await import('./_canonical-approvals.js');
  const { ticks, result } = await ticksDuring(() => mod.readCanonicalPendingOrNull());
  assert.equal(result?.length, 2);
  assert.ok(ticks <= 1, `expected a blocked loop, got ${ticks} ticks`);
});

test('the unified list reads the feed at most once per 3 s, then again after the TTL', async () => {
  const ua = await import('./unified-approvals.js');
  assert.equal(typeof ua.cachedCanonicalPending, 'function', 'the cached reader exists');
  const before = calls();
  const a = await ua.cachedCanonicalPending();
  const b = await ua.cachedCanonicalPending();
  const c = await ua.cachedCanonicalPending();
  assert.equal(a.length, 2);
  assert.deepEqual(b.map((r) => r.id), a.map((r) => r.id));
  assert.equal(calls() - before, 1, 'three reads within the TTL ran the CLI once');
  await new Promise((r) => setTimeout(r, 3100));
  await ua.cachedCanonicalPending();
  assert.equal(calls() - before, 2, 'a read after the TTL ran the CLI again');
  void c;
});
