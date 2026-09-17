/**
 * Facts store — RED-first for POST /api/facts (the gate's "write a fact").
 *
 * Before this change the facts API could only edit or refresh rows that already
 * existed, and nothing on a clean install ever created one — so Arturo's FACTS
 * recall was inert. This pins the pure create path the route calls: validation,
 * id allocation, the row shape facts_recall.py reads (`fact` + `timestamp` +
 * `verified_at` + `source`), and that the store file survives a round trip.
 *
 * Run: npx tsx --test src/lib/facts-store.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, existsSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';
import { createFactRow, loadFactsDb, writeFactsDb, factsDbPath, type FactsDb } from './facts-store.js';

test('createFactRow rejects empty / non-string text', () => {
  assert.throws(() => createFactRow({ facts: [] }, ''), /text/);
  assert.throws(() => createFactRow({ facts: [] }, '   '), /text/);
  assert.throws(() => createFactRow({ facts: [] }, 42 as unknown as string), /text/);
});

test('createFactRow allocates max(id)+1 and stamps the recall-readable fields', () => {
  const db = { facts: [{ id: 7, fact: 'older' }, { id: 3, fact: 'oldest' }] };
  const row = createFactRow(db, 'Acme Dental signed the retainer', { category: 'clients' });
  assert.equal(row.id, 8);
  assert.equal(row.fact, 'Acme Dental signed the retainer');
  assert.equal(row.text, 'Acme Dental signed the retainer');
  assert.equal(row.source, 'dashboard');
  assert.equal(row.category, 'clients');
  assert.match(row.timestamp as string, /^\d{4}-\d{2}-\d{2}T/);
  assert.equal(row.verified_at, row.timestamp);
});

test('createFactRow starts ids at 1 on an empty store and trims text', () => {
  const row = createFactRow({ facts: [] }, '  first fact  ');
  assert.equal(row.id, 1);
  assert.equal(row.fact, 'first fact');
});

test('round trip: write then load from an isolated ORCHESTRA_DIR', () => {
  const dir = mkdtempSync(join(tmpdir(), 'facts-store-'));
  const path = factsDbPath(dir);
  assert.equal(existsSync(path), false);
  const db: FactsDb = loadFactsDb(path);
  assert.equal(db.facts.length, 0);
  db.facts.push(createFactRow(db, 'pixel live on staging'));
  writeFactsDb(path, db);
  const again = loadFactsDb(path);
  assert.equal(again.facts.length, 1);
  assert.equal(again.facts[0].fact, 'pixel live on staging');
  assert.ok(again.last_updated, 'last_updated stamped');
  // the file is what facts_recall.py reads: plain JSON with a facts[] array
  const raw = JSON.parse(readFileSync(path, 'utf-8'));
  assert.ok(Array.isArray(raw.facts));
});
