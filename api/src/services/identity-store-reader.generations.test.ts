/**
 * A pre-allocated green slot is NOT a generation.
 *
 * The rotation machinery mints a generation row for the incoming green BEFORE it is promoted
 * (the PROMOTE notice calls this out: a root can carry "a blue canonical row plus a
 * pre-allocated green slot"). Such a row has no session, no promoted_at and no retired_at —
 * it names a seat that has never run.
 *
 * Shipped in #147/#149, the count and the list both treated those rows as history. On the live
 * fleet that is 84 rows across the fleet, and it made gm read "55 generations" with a "gen 3"
 * that never existed — which reads exactly like the lineage had reset to 3. The operator saw
 * that and reported it as missing generations.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';
import Database from 'better-sqlite3';
import { getGenerationCounts, getGenerations } from './identity-store-reader.js';

/** A tiny identity store shaped like the real one. */
function makeStore(): string {
  const orch = mkdtempSync(join(tmpdir(), 'identity-store-'));
  mkdirSync(join(orch, 'state'));
  const db = new Database(join(orch, 'state', 'orchestra-registry.db'));
  db.exec(`
    CREATE TABLE canonical (root TEXT, generation_id INTEGER, tmux_session TEXT, status TEXT);
    CREATE TABLE generations (
      id INTEGER PRIMARY KEY, root TEXT, generation INTEGER, session_id TEXT,
      conversation_path TEXT, model TEXT, spawned_at TEXT, spawned_by TEXT,
      promoted_at TEXT, promoted_by TEXT, retired_at TEXT, resume_command TEXT, note TEXT);
  `);
  const ins = db.prepare(
    'INSERT INTO generations (id,root,generation,session_id,spawned_at,promoted_at,retired_at) VALUES (?,?,?,?,?,?,?)',
  );
  // gen 1: ran, then retired.
  ins.run(1, 'seat', 1, 's1', '2026-09-26T15:00:00Z', '2026-09-26T15:43:36Z', '2026-09-26T17:57:09Z');
  // gen 2: ran, still live — this is the canonical head.
  ins.run(2, 'seat', 2, 's2', '2026-09-26T17:41:46Z', '2026-09-26T17:57:09Z', null);
  // gen 3: PRE-ALLOCATED SLOT — spawned row exists, never promoted, never retired, no session.
  ins.run(3, 'seat', 3, null, '2026-09-26T18:17:32Z', null, null);
  db.prepare('INSERT INTO canonical (root,generation_id) VALUES (?,?)').run('seat', 2);
  db.close();
  return orch;
}

test('a never-promoted slot is not counted as a generation', () => {
  const orch = makeStore();
  const counts = getGenerationCounts(orch);
  assert.ok(counts, 'store should be readable');
  // Two real generations (one retired, one live), not three.
  assert.equal(counts!['seat'], 2, 'the pre-allocated slot must not inflate the count');
});

test('the slot is still listed, but flagged rather than dressed up as history', () => {
  const orch = makeStore();
  const rows = getGenerations('seat', orch);
  assert.ok(rows);
  const byGen = new Map(rows!.map((r) => [r.generation, r]));

  // It is not hidden — a seat waiting to be promoted is worth seeing.
  assert.equal(rows!.length, 3);
  // ...but it says what it is.
  assert.equal(byGen.get(3)!.pending, true, 'never promoted, never retired -> pending');
  assert.equal(byGen.get(1)!.pending, false);
  assert.equal(byGen.get(2)!.pending, false);

  // The live head is still the canonical one, not simply the highest number.
  assert.equal(byGen.get(2)!.current, true);
  assert.equal(byGen.get(3)!.current, false, 'a slot is never "current"');

  // Ordering is unchanged: current first, then by time.
  assert.equal(rows![0].generation, 2);
});
