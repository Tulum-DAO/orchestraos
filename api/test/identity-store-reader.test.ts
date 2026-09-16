// Readers-DB-first (Piece 3, dashboard) — the identity-store-reader reads the
// canonical live head DB-first so the dashboard suppresses phantom `unregistered:`
// chips and points a chip click at the current live head. Covers:
//   R2  a live agent present in the DB canonical is discoverable (=> no unregistered mint)
//   R3  the click target resolves to the DB canonical tmux_session (current live head)
//   R13 a readonly reader coexists with a concurrent open write txn (WAL) without blocking
//
// Run: npx tsx --test test/identity-store-reader.test.ts   (from api/)
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import Database from 'better-sqlite3';

import {
  isCutoverActive,
  getCanonicalAgents,
  canonicalTmuxSession,
} from '../src/services/identity-store-reader.js';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, '..', '..');

let orch: string;
let dbp: string;

function seedSchema(dbPath: string) {
  // Init the REAL schema via python so the reader's JOIN runs against faithful tables.
  execFileSync('python3', ['-c',
    'import sys; from scripts.identity_store import orchestra_db; orchestra_db.init_db(sys.argv[1])',
    dbPath], { cwd: REPO, encoding: 'utf-8' });
}

function insertCanonical(dbPath: string, root: string, gen: number, sid: string,
                         tmux: string, status = 'online') {
  const db = new Database(dbPath);
  try {
    db.prepare(`INSERT INTO lineages(root,tier,runtime,always_on,machine,cwd)
                VALUES(?,?,?,?,?,?)`).run(root, 'T2', 'claude', 1, 'vps', '/x');
    const info = db.prepare(`INSERT INTO generations(root,generation,session_id,model)
                             VALUES(?,?,?,?)`).run(root, gen, sid, 'fable-5-1');
    db.prepare(`INSERT INTO canonical(root,generation_id,tmux_session,status)
                VALUES(?,?,?,?)`).run(root, info.lastInsertRowid, tmux, status);
  } finally {
    db.close();
  }
}

before(() => {
  orch = fs.mkdtempSync(path.join(os.tmpdir(), 'idstore-'));
  fs.mkdirSync(path.join(orch, 'state'), { recursive: true });
  dbp = path.join(orch, 'state', 'orchestra-registry.db');
  seedSchema(dbp);
  // motion-graphics: the real flap case — canonical online in the DB, real sid.
  insertCanonical(dbp, 'motion-graphics', 1, '9c9442be', 'motion-graphics');
});

after(() => {
  try { fs.rmSync(orch, { recursive: true, force: true }); } catch { /* ignore */ }
});

test('isCutoverActive: env flag on', () => {
  assert.equal(isCutoverActive('/nonexistent'), process.env.IDENTITY_STORE_CUTOVER === '1');
  // flag-file form
  const flag = path.join(orch, 'state', 'identity-store-cutover.flag');
  fs.writeFileSync(flag, 'armed');
  assert.equal(isCutoverActive(orch), true);
  fs.rmSync(flag);
});

test('R2: DB canonical live head is discoverable (=> dashboard suppresses unregistered:)', () => {
  const canon = getCanonicalAgents(orch);
  assert.ok(canon, 'canonical map should be non-null when the DB is present');
  assert.ok(canon!['motion-graphics'], 'a live canonical agent must be discoverable via the DB');
  assert.equal(canon!['motion-graphics'].tmux_session, 'motion-graphics');
  assert.equal(canon!['motion-graphics'].status, 'online');
});

test('R3: click target resolves to the DB canonical tmux_session (live head)', () => {
  assert.equal(canonicalTmuxSession('motion-graphics', orch), 'motion-graphics');
  assert.equal(canonicalTmuxSession('not-a-root', orch), null); // unknown -> caller keeps its own
});

test('absent DB -> null (caller falls back to registry.json)', () => {
  assert.equal(getCanonicalAgents('/nonexistent-orch-dir'), null);
});

test('R13: readonly reader coexists with an open write transaction (WAL)', () => {
  // Hold an IMMEDIATE write transaction open on a separate connection...
  const writer = new Database(dbp);
  writer.pragma('busy_timeout = 2000');
  writer.exec('BEGIN IMMEDIATE');
  try {
    // ...the readonly reader still returns last-committed without blocking/erroring.
    const canon = getCanonicalAgents(orch);
    assert.ok(canon && canon['motion-graphics'], 'readonly read must succeed under an open writer (WAL)');
  } finally {
    writer.exec('ROLLBACK');
    writer.close();
  }
});
