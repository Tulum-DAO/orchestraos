/**
 * sessionOwners and lineageChainOf are read on every transcript request. Each opened the registry
 * and ran its query every time (14 ms + 8 ms per request on a 100-generation lineage). Both now
 * keep their answer while the store's file signature (size + mtime of the db and its WAL) is
 * unchanged, and read again the moment it changes.
 *
 * Run: npx tsx --test src/services/identity-store-reader.cache.test.ts   (from api/)
 */
import { testTmpDir } from '../lib/testTmp.js';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, utimesSync } from 'fs';
import { join } from 'path';
import Database from 'better-sqlite3';

if (!process.env.ORCHESTRA_CONFIG) {
  process.env.ORCHESTRA_CONFIG = join(new URL('../../..', import.meta.url).pathname, 'orchestra.example.toml');
}
const orch = testTmpDir('reader-cache-');
mkdirSync(join(orch, 'state'), { recursive: true });
const DB = join(orch, 'state', 'orchestra-registry.db');
const SID1 = '66666666-2222-4333-8444-000000000001';
const SID2 = '66666666-2222-4333-8444-000000000002';

function open() {
  const db = new Database(DB);
  db.exec(`CREATE TABLE IF NOT EXISTS lineages (root TEXT PRIMARY KEY, tier TEXT, runtime TEXT, reports_to TEXT,
             always_on INTEGER, purpose TEXT, machine TEXT, cwd TEXT);
           CREATE TABLE IF NOT EXISTS generations (id INTEGER PRIMARY KEY, root TEXT, generation INTEGER, session_id TEXT,
             conversation_path TEXT, model TEXT, spawned_at TEXT, spawned_by TEXT, promoted_at TEXT,
             promoted_by TEXT, retired_at TEXT, resume_command TEXT, note TEXT);
           CREATE TABLE IF NOT EXISTS canonical (root TEXT PRIMARY KEY, generation_id INTEGER, tmux_session TEXT, status TEXT);`);
  return db;
}
{
  const db = open();
  db.prepare('INSERT INTO lineages (root, cwd) VALUES (?, ?)').run('seat', '/repo');
  db.prepare('INSERT INTO generations (id, root, generation, session_id, promoted_at) VALUES (1, ?, 1, ?, ?)').run('seat', SID1, '2026-10-20T00:00:00Z');
  db.prepare("INSERT INTO canonical VALUES (?, 1, ?, 'online')").run('seat', 'seat');
  db.close();
}

const { sessionOwners, lineageChainOf, identityStoreReads } = await import('./identity-store-reader.js');

test('sessionOwners is read once while the store is unchanged, again when it changes', () => {
  const a = sessionOwners(orch)!;
  const n = identityStoreReads();
  const b = sessionOwners(orch)!;
  assert.equal(b, a, 'the same answer object');
  assert.equal(identityStoreReads(), n, 'no second read');
  assert.equal(a.sessions.get(SID1)?.root, 'seat');
  // A swap: a new generation takes over. The store's bytes change, so the next call reads again.
  const db = open();
  db.prepare('UPDATE generations SET retired_at = ? WHERE id = 1').run('2026-10-21T00:00:00Z');
  db.prepare('INSERT INTO generations (id, root, generation, session_id, promoted_at) VALUES (2, ?, 2, ?, ?)').run('seat', SID2, '2026-10-21T00:00:00Z');
  db.prepare('UPDATE canonical SET generation_id = 2 WHERE root = ?').run('seat');
  db.close();
  // mtime has 1 ms granularity on most filesystems and the write may land in the same tick as the
  // read above: the size changed too, but make the signature move for sure.
  const later = new Date(Date.now() + 2000);
  utimesSync(DB, later, later);
  const c = sessionOwners(orch)!;
  assert.notEqual(c, a);
  assert.ok(identityStoreReads() > n);
  assert.equal(c.sessions.get(SID2)?.current, true);
  assert.equal(c.sessions.get(SID1)?.current, false);
});

test('lineageChainOf is kept per head session while the store is unchanged', () => {
  const a = lineageChainOf(SID2, orch)!;
  const n = identityStoreReads();
  const b = lineageChainOf(SID2, orch)!;
  assert.equal(b, a);
  assert.equal(identityStoreReads(), n);
  assert.deepEqual(a.links.map((l) => l.session_id), [SID1, SID2]);
  assert.equal(lineageChainOf(SID1, orch), null, 'not a head: null, and that answer is kept too');
  const m = identityStoreReads();
  assert.equal(lineageChainOf(SID1, orch), null);
  assert.equal(identityStoreReads(), m);
});
