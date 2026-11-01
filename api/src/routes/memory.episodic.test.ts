/**
 * GET /api/memory/search's `episodic` source over a fixture conversation index (the schema of
 * scripts/episodic_memory.py, built here in a temp data dir; no real transcript is read).
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import express from 'express';
import Database from 'better-sqlite3';
import type { AddressInfo } from 'node:net';
import { mkdtempSync, mkdirSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { searchEpisodic, ftsQuery } from '../services/episodic-memory.js';

// The writer's schema (scripts/episodic_memory.py SCHEMA), the parts the reader uses.
const DDL = `
CREATE TABLE entries (id INTEGER PRIMARY KEY, agent TEXT NOT NULL, session_id TEXT NOT NULL, generation INTEGER, uuid TEXT NOT NULL,
  part INTEGER NOT NULL DEFAULT 0, tool_id TEXT, role TEXT, kind TEXT NOT NULL, ts TEXT, text TEXT NOT NULL, UNIQUE (session_id, uuid, part));
CREATE VIRTUAL TABLE entries_fts USING fts5(text, content='entries', content_rowid='id', tokenize='unicode61');
CREATE TRIGGER entries_ai AFTER INSERT ON entries BEGIN INSERT INTO entries_fts(rowid, text) VALUES (new.id, new.text); END;
CREATE TABLE embeddings (entry_id INTEGER PRIMARY KEY, model TEXT NOT NULL, dim INTEGER NOT NULL, vector BLOB NOT NULL, created TEXT);`;

function box() {
  const data = mkdtempSync(join(tmpdir(), 'episodic-'));
  mkdirSync(join(data, 'state'), { recursive: true });
  const path = join(data, 'state', 'episodic-memory.db');
  const db = new Database(path);
  db.exec(DDL);
  const add = db.prepare('INSERT INTO entries(agent, session_id, generation, uuid, part, tool_id, role, kind, ts, text) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)');
  add.run('qa-demo', 'sess-a', 3, 'r1', 0, 'toolu_1', 'user', 'tool_result', '2026-10-31T02:00:00Z', `the Sample report says ZEBRA-7731 ${'and a long tail '.repeat(200)}`);
  add.run('qa-demo', 'sess-a', 3, 'a2', 1, null, 'assistant', 'message', '2026-10-31T02:00:05Z', 'The report names ZEBRA-7731.');
  add.run('dev-demo', 'sess-b', 1, 'x1', 0, null, 'user', 'message', '2026-10-31T01:00:00Z', 'ZEBRA-7731 is in the Example plan too');
  db.close();
  // the registry gives dev-demo a client tag, so a client-scoped caller sees only it
  writeFileSync(join(data, 'registry.json'), JSON.stringify({ agents: { 'qa-demo': {}, 'dev-demo': { tags: ['client:sample'] } } }));
  return { data, path };
}

test('episodic search: tags and ids, a snippet (never the whole text), filters, only agents the caller may see', () => {
  const { path } = box();
  const all = searchEpisodic('ZEBRA-7731', {}, () => true, path);
  assert.deepEqual(new Set(all.map((h) => h.agent)), new Set(['qa-demo', 'dev-demo']));
  const res = all.find((h) => h.kind === 'tool_result')!;
  assert.deepEqual([res.source, res.session_id, res.generation, res.uuid, res.part, res.tool_id, res.role], ['episodic', 'sess-a', 3, 'r1', 0, 'toolu_1', 'user']);
  assert.ok(res.snippet.includes('[ZEBRA]') || res.snippet.includes('[ZEBRA'), 'the match is marked');
  assert.ok(res.snippet.length < 400 && !('text' in res), 'a snippet, not the stored text');
  assert.ok(all.every((h) => h.score >= 1 && h.score <= 95));
  assert.deepEqual(searchEpisodic('ZEBRA-7731', { agent: 'dev-demo' }, () => true, path).map((h) => h.uuid), ['x1']);
  assert.deepEqual(searchEpisodic('ZEBRA-7731', { session: 'sess-a', generation: 3 }, () => true, path).length, 2);
  assert.deepEqual(searchEpisodic('ZEBRA-7731', { generation: 9 }, () => true, path), []);
  assert.deepEqual(searchEpisodic('ZEBRA-7731', {}, (a) => a === 'dev-demo', path).map((h) => h.agent), ['dev-demo'], 'unseen agents left out');
  assert.deepEqual(searchEpisodic('ZEBRA-7731', { agent: 'qa-demo' }, (a) => a === 'dev-demo', path), [], 'naming an unseen agent finds nothing');
  assert.deepEqual(searchEpisodic('ZEBRA-7731', {}, () => true, join(path, '..', 'missing.db')), [], 'no index yet: nothing');
  assert.deepEqual(searchEpisodic('-dash: OR (paren', {}, () => true, path), [], 'operators are words: no syntax error');
  assert.equal(ftsQuery(' a  "b" '), '"a" """b"""');
});

async function withApp(data: string, fn: (base: string) => Promise<void>) {
  const prev = process.env.ORCHESTRA_DIR;
  process.env.ORCHESTRA_DIR = data;
  // imported only now: the state reader (agent tags for the scope rule) fixes its data dir when first imported
  const { default: memoryRouter } = await import('./memory.js');
  const app = express();
  app.use('/api/memory', memoryRouter);
  const server = app.listen(0);
  const { port } = server.address() as AddressInfo;
  try { await fn(`http://127.0.0.1:${port}`); } finally {
    server.close();
    if (prev === undefined) delete process.env.ORCHESTRA_DIR; else process.env.ORCHESTRA_DIR = prev;
  }
}

test('GET /api/memory/search: episodic is asked for by name, and carries the transcript routes\' scope', async () => {
  const { data } = box();
  await withApp(data, async (base) => {
    const plain = await (await fetch(`${base}/api/memory/search?q=ZEBRA-7731`)).json();
    assert.ok(!plain.results.some((r: { source: string }) => r.source === 'episodic'), 'not unless asked');
    const asked = await (await fetch(`${base}/api/memory/search?q=ZEBRA-7731&sources=episodic&agent=qa-demo&generation=3`)).json();
    assert.equal(asked.total, 2);
    assert.ok(asked.results.every((r: { source: string; agent: string }) => r.source === 'episodic' && r.agent === 'qa-demo'));
    // a trusted proxy: a client-scoped caller sees only its client's agents; no identity sees nothing
    const prevTrust = process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS;
    process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS = '1';
    try {
      const scoped = await (await fetch(`${base}/api/memory/search?q=ZEBRA-7731&sources=episodic`, {
        headers: { 'x-orchestra-user': 'sample-user', 'x-orchestra-role': 'viewer', 'x-orchestra-client': 'sample' } })).json();
      assert.deepEqual(scoped.results.map((r: { agent: string }) => r.agent), ['dev-demo']);
      const anon = await (await fetch(`${base}/api/memory/search?q=ZEBRA-7731&sources=episodic`)).json();
      assert.equal(anon.total, 0);
    } finally {
      if (prevTrust === undefined) delete process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS; else process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS = prevTrust;
    }
  });
});
