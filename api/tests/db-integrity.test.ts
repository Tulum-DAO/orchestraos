/**
 * db.ts never-wipe classifier — RED-first (DEC-1788702356376911 CONSENSUS_REACHED v2).
 *
 * The load-bearing safety logic: classify PRAGMA integrity_check output into
 * OK / BENIGN / INDEX / FATAL, so a benign freelist note ('Page N is never used')
 * can never again trigger the empty-recreate that wiped 16k messages (3rd incident).
 *
 * Pure classifier is unit-tested here; db.ts wires it into openAndVerify/initDb.
 *
 * Run: cd api && npx tsx --test tests/db-integrity.test.ts
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync } from 'fs';
import { join } from 'path';
import { tmpdir } from 'os';
import Database from 'better-sqlite3';

import { classifyIntegrity, IntegrityClass } from '../src/lib/db-integrity.js';

// REAL engine output, verified by gm against SQLite 3.53 (better-sqlite3) AND
// 3.45 (python): the freelist "never used" note arrives as ONE multi-line row —
// a "*** in database <name> ***" preamble line + "Page N: never used" (COLON).
// This exact string is the case that FAILED the gate; it MUST classify BENIGN.
const REAL_FREELIST_ROW = '*** in database main ***\nPage 9111: never used';

test("'ok' => OK", () => {
  assert.equal(classifyIntegrity(['ok']).cls, IntegrityClass.OK);
});

test('REAL-engine multi-line freelist row (preamble + colon note) => BENIGN (the gate-fail case)', () => {
  // The exact false-green: preamble+note as ONE row must normalize to BENIGN,
  // NOT FATAL (a FATAL here would HALT the API on today's benign incident).
  assert.equal(classifyIntegrity([REAL_FREELIST_ROW]).cls, IntegrityClass.BENIGN);
});

test("legacy 'is' wording also => BENIGN", () => {
  assert.equal(classifyIntegrity(['Page 9111 is never used']).cls, IntegrityClass.BENIGN);
});

test('colon form as a standalone line => BENIGN', () => {
  assert.equal(classifyIntegrity(['Page 42: never used']).cls, IntegrityClass.BENIGN);
});

test('REAL engine round-trip: a healthy DB integrity_check parses as OK', () => {
  // Proves the parse path against the ACTUAL binding output, not a hand string.
  const dir = mkdtempSync(join(tmpdir(), 'dbint-'));
  const d = new Database(join(dir, 't.db'));
  d.exec('CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT); INSERT INTO t (v) VALUES (\'x\');');
  const rows = (d.prepare('PRAGMA integrity_check').all() as Array<{ integrity_check: string }>)
    .map((r) => r.integrity_check);
  d.close();
  assert.equal(classifyIntegrity(rows).cls, IntegrityClass.OK);
});

test('preamble + benign + INDEX line (multi-line row) => INDEX', () => {
  const r = classifyIntegrity(['*** in database main ***\nPage 9111: never used\nrow 5 missing from index ix_v']);
  assert.equal(r.cls, IntegrityClass.INDEX);
});

test('preamble + benign + bare malformed (multi-line row) => FATAL', () => {
  const r = classifyIntegrity(['*** in database main ***\nPage 9111: never used\ndatabase disk image is malformed']);
  assert.equal(r.cls, IntegrityClass.FATAL);
});

test('preamble-only (no findings) => FATAL (cannot affirm ok)', () => {
  assert.equal(classifyIntegrity(['*** in database main ***']).cls, IntegrityClass.FATAL);
});

test('multiple benign notes, all matching => BENIGN', () => {
  const r = classifyIntegrity(['Page 9111 is never used', 'Page 9112 is never used']);
  assert.equal(r.cls, IntegrityClass.BENIGN);
});

test('index-scoped findings => INDEX (REINDEX candidate)', () => {
  assert.equal(classifyIntegrity(['row 5 missing from index idx_msg_conv']).cls, IntegrityClass.INDEX);
  assert.equal(classifyIntegrity(['wrong # of entries in index idx_tasks_status']).cls, IntegrityClass.INDEX);
});

test('bare "database disk image is malformed" => FATAL, never INDEX', () => {
  assert.equal(classifyIntegrity(['database disk image is malformed']).cls, IntegrityClass.FATAL);
});

test('mixed benign + fatal => FATAL (whole-set AND; proves the (1)-cap removal matters)', () => {
  const r = classifyIntegrity(['Page 9111 is never used', 'database disk image is malformed']);
  assert.equal(r.cls, IntegrityClass.FATAL);
});

test('mixed benign + index => INDEX (escalates above benign, below fatal)', () => {
  const r = classifyIntegrity(['Page 9111 is never used', 'row 5 missing from index idx_x']);
  assert.equal(r.cls, IntegrityClass.INDEX);
});

test('unknown/unrecognized finding => FATAL (fail safe, not benign)', () => {
  assert.equal(classifyIntegrity(['something we have never seen before']).cls, IntegrityClass.FATAL);
});

test('empty rows (check produced nothing) => FATAL (cannot confirm ok)', () => {
  assert.equal(classifyIntegrity([]).cls, IntegrityClass.FATAL);
});

test('a thrown check signalled via the sentinel => FATAL', () => {
  assert.equal(classifyIntegrity([classifyIntegrity.THROWN]).cls, IntegrityClass.FATAL);
});

test('page-level "never used" is the ONLY benign phrase (orphan-page note anchored)', () => {
  // A superficially similar but non-allowlisted phrase must NOT be benign.
  assert.notEqual(classifyIntegrity(['freelist count wrong']).cls, IntegrityClass.BENIGN);
});
