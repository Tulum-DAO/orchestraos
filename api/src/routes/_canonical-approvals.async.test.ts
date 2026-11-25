/**
 * The canonical pending feed is read WITHOUT blocking the event loop, once at a time, and the list routes
 * keep it only while tasks.db is unchanged (at most 3 s). Live finding (2026-11-25): every GET /api/approvals
 * ran `approval.py pending --json` with execFileSync (hundreds of ms), so every other request on the API,
 * SSE flushes included, stalled for that long on each dashboard poll.
 *
 * RED-first: readCanonicalPendingCached did not exist and both list routes called the sync reader.
 * Hermetic: ORCHESTRA_SCRIPTS_DIR points at a tmp dir whose approval.py is a fake that counts its calls,
 * sleeps 400 ms and prints two rows; tasks.db is a plain file in the tmp ORCHESTRA_DIR (only its stamp is read).
 * The tmp dir is removed at the end. Run: npx tsx --test <this file> (from api/)
 */
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, chmodSync, rmSync, appendFileSync, renameSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { Server } from 'node:http';

const dir = mkdtempSync(join(tmpdir(), 'canonical-async-'));
const counter = join(dir, 'calls');
const db = join(dir, 'state', 'tasks.db');
mkdirSync(join(dir, 'state'));
writeFileSync(counter, '0');
writeFileSync(db, 'not a real db: only its mtime and size are read\n');
writeFileSync(join(dir, 'approval.py'), `#!/usr/bin/env python3
import json, sys, time
n = int(open(${JSON.stringify(counter)}).read() or 0) + 1
open(${JSON.stringify(counter)}, "w").write(str(n))
time.sleep(0.4)
row = {"from_agent": "pm-demo", "options": ["approve", "deny"], "status": "pending", "kind": None, "summary": None,
       "risk_level": None, "reversibility": None, "feature": None, "provider": None, "menu": None}
rows = [dict(row, id="apr_async0001_0000001", question="ship?", op_key="k1", created_at="2026-11-25T00:00:00Z"),
        dict(row, id="apr_async0002_0000002", question="again?", op_key="k2", created_at="2026-11-25T00:00:01Z")]
print(json.dumps(rows))
`);
chmodSync(join(dir, 'approval.py'), 0o755);
process.env.ORCHESTRA_SCRIPTS_DIR = dir;          // read at import time by _canonical-approvals.ts
process.env.ORCHESTRA_DIR = dir;
delete process.env.APPROVAL_DB_PATH;

const calls = () => Number(readFileSync(counter, 'utf-8'));
let server: Server | undefined;
after(async () => {
  if (server) await new Promise<void>((r) => server!.close(() => r()));
  rmSync(dir, { recursive: true, force: true });
});

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

async function boot(): Promise<string> {
  const express = (await import('express')).default;
  const unified = (await import('./unified-approvals.js')).default;
  const legacy = (await import('./approvals.js')).default;
  const app = express();
  app.use(express.json());
  app.use('/api/approvals/unified', unified);
  app.use('/api/approvals', legacy);
  await new Promise<void>((resolve) => { server = app.listen(0, '127.0.0.1', () => resolve()); });
  const addr = server!.address() as { port: number };
  return `http://127.0.0.1:${addr.port}`;
}

test('readCanonicalPendingAsync returns the rows and leaves the event loop running', async () => {
  const mod = await import('./_canonical-approvals.js');
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

test('both list routes keep the loop running and share one CLI run while tasks.db is unchanged', async () => {
  const base = await boot();
  const before = calls();
  // four concurrent requests across both routes with an empty cache: one approval.py run, not four
  const { ticks, result } = await ticksDuring(() => Promise.all([
    fetch(`${base}/api/approvals/unified?type=agent`), fetch(`${base}/api/approvals/unified/stats`),
    fetch(`${base}/api/approvals`), fetch(`${base}/api/approvals/unified?type=agent`),
  ]));
  for (const r of result) assert.equal(r.status, 200);
  const unifiedBody = await result[0].json();
  assert.deepEqual(unifiedBody.items.map((i: { id: string }) => i.id).sort(),
    ['apr_async0001_0000001', 'apr_async0002_0000002']);
  assert.ok(ticks >= 5, `the loop ticked only ${ticks} times while the routes read the feed`);
  assert.equal(calls() - before, 1, 'concurrent requests shared one approval.py run');
  // a fifth request within the TTL and with the same db stamp runs nothing
  assert.equal((await fetch(`${base}/api/approvals/unified?type=agent`)).status, 200);
  assert.equal(calls() - before, 1, 'a read within the TTL ran nothing');
});

test('a write to tasks.db by any process drops the kept feed at once (resolve-everywhere)', async () => {
  const base = `http://127.0.0.1:${(server!.address() as { port: number }).port}`;
  const before = calls();
  appendFileSync(db, 'x');                         // what a gateway answer or the python CLI does to the db
  assert.equal((await fetch(`${base}/api/approvals/unified?type=agent`)).status, 200);
  assert.equal(calls() - before, 1, 'a changed db stamp forced a fresh read inside the TTL');
});

test('an answer through this process drops the kept feed, and a failed read keeps the last good rows', async () => {
  const mod = await import('./_canonical-approvals.js');
  mod.invalidateCanonicalPendingCache();
  const before = calls();
  await mod.readCanonicalPendingCached();            // 1: nothing kept
  await mod.readCanonicalPendingCached();            // kept
  mod.invalidateCanonicalPendingCache();
  await mod.readCanonicalPendingCached();            // 2: dropped by the answer path
  assert.equal(calls() - before, 2, 'invalidate forced a re-read');
  renameSync(join(dir, 'approval.py'), join(dir, 'approval.py.off'));   // the next run fails (no such file)
  mod.invalidateCanonicalPendingCache();
  const rows = await mod.readCanonicalPendingCached();
  renameSync(join(dir, 'approval.py.off'), join(dir, 'approval.py'));
  assert.equal(rows.length, 2, 'a failed read returned the last good rows, not []');
  const again = await mod.readCanonicalPendingCached();
  assert.equal(again.length, 2);
  assert.ok(calls() - before >= 3, 'the failure was not kept: the read after it ran the CLI again');
});
