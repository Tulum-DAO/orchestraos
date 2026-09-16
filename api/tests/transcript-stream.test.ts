/**
 * F1 streaming integration test — the RED-first backstop for live transcript
 * streaming (SPEC_transcript-ecosystem §4).
 *
 * Exercises the server tail seam (TranscriptTailer) against a real on-disk
 * fixture JSONL: subscribe -> full snapshot (F0 v2 envelope shapes), append a
 * line -> only the delta arrives, resume via Last-Event-ID offset -> no
 * refetch, stale/foreign ids -> snapshot, session rotation -> reset snapshot.
 *
 * Run: cd api && npm run stream-test
 */
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, writeFileSync, appendFileSync, mkdtempSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';

import { TranscriptTailer, type TailEvent } from '../src/services/transcript-tail.js';
import { normalizeTranscript, GRAMMAR_VERSION } from '../src/routes/chat-transcript.js';

const HERE = dirname(fileURLToPath(import.meta.url));
const FIXTURE = join(HERE, '..', '..', 'contract', 'transcript', 'fixtures', 'claude-basic.input.jsonl');

// A minimal valid Claude transcript line (the shape normalizeClaudeEntry reads).
function claudeTextLine(uuid: string, text: string): string {
  return JSON.stringify({
    type: 'assistant',
    uuid,
    timestamp: '2026-08-27T23:00:00Z',
    message: { role: 'assistant', content: [{ type: 'text', text }] },
  });
}

let dir: string;
let path: string;
let sid: string | null;

function makeTailer(limit = 150): TranscriptTailer {
  return new TranscriptTailer({
    agentId: 'fixture-agent',
    resolve: () => ({ path, sid }),
    limit,
  });
}

function fullItems(): any[] {
  const lines = readFileSync(path, 'utf-8').split('\n');
  return normalizeTranscript(lines, 'fixture-agent', sid, Number.MAX_SAFE_INTEGER).items;
}

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), 'f1-stream-'));
  path = join(dir, 'session-a.jsonl');
  sid = 'session-a';
  writeFileSync(path, readFileSync(FIXTURE, 'utf-8'));
});

test('subscribe with no Last-Event-ID -> one full snapshot in F0 envelope shape', () => {
  const tailer = makeTailer();
  const events: TailEvent[] = [];
  tailer.subscribe((ev) => events.push(ev));

  assert.equal(events.length, 1);
  const snap = events[0];
  assert.equal(snap.type, 'snapshot');
  assert.equal(snap.grammar_version, GRAMMAR_VERSION);
  assert.equal(snap.session_id, 'session-a');
  const expected = fullItems();
  assert.deepStrictEqual(snap.items, expected);
  assert.ok(Array.isArray(snap.render_items), 'snapshot carries server-paired render_items');
  assert.equal(snap.id, `session-a:${expected.length}`);
});

test('append to the JSONL -> tick pushes ONLY the new item as a delta', () => {
  const tailer = makeTailer();
  const events: TailEvent[] = [];
  tailer.subscribe((ev) => events.push(ev));
  const before = fullItems().length;

  appendFileSync(path, '\n' + claudeTextLine('u-tail-1', 'tail arrived'));
  tailer.tick();

  assert.equal(events.length, 2);
  const delta = events[1];
  assert.equal(delta.type, 'delta');
  assert.equal(delta.grammar_version, GRAMMAR_VERSION);
  assert.equal(delta.items.length, 1);
  assert.equal(delta.items[0].kind, 'text');
  assert.equal(delta.items[0].text, 'tail arrived');
  assert.equal(delta.items[0].uuid, 'u-tail-1');
  assert.equal(delta.id, `session-a:${before + 1}`);
});

test('no change -> tick emits nothing', () => {
  const tailer = makeTailer();
  const events: TailEvent[] = [];
  tailer.subscribe((ev) => events.push(ev));
  tailer.tick();
  tailer.tick();
  assert.equal(events.length, 1); // just the snapshot
});

test('resume with valid Last-Event-ID -> delta of missed items only, no snapshot', () => {
  const offsetAtDisconnect = fullItems().length;
  appendFileSync(path, '\n' + claudeTextLine('u-missed-1', 'missed while away'));

  const tailer = makeTailer();
  const events: TailEvent[] = [];
  tailer.subscribe((ev) => events.push(ev), `session-a:${offsetAtDisconnect}`);

  assert.equal(events.length, 1);
  const delta = events[0];
  assert.equal(delta.type, 'delta');
  assert.equal(delta.items.length, 1);
  assert.equal(delta.items[0].uuid, 'u-missed-1');
});

test('resume when fully caught up -> no event until the next append', () => {
  const offset = fullItems().length;
  const tailer = makeTailer();
  const events: TailEvent[] = [];
  tailer.subscribe((ev) => events.push(ev), `session-a:${offset}`);
  assert.equal(events.length, 0);

  appendFileSync(path, '\n' + claudeTextLine('u-tail-2', 'later'));
  tailer.tick();
  assert.equal(events.length, 1);
  assert.equal(events[0].type, 'delta');
});

test('stale/foreign Last-Event-ID (wrong sid or offset beyond file) -> full snapshot', () => {
  const tailer = makeTailer();

  const foreign: TailEvent[] = [];
  tailer.subscribe((ev) => foreign.push(ev), 'some-other-session:5');
  assert.equal(foreign.length, 1);
  assert.equal(foreign[0].type, 'snapshot');

  const beyond: TailEvent[] = [];
  tailer.subscribe((ev) => beyond.push(ev), `session-a:${fullItems().length + 99}`);
  assert.equal(beyond.length, 1);
  assert.equal(beyond[0].type, 'snapshot');
});

test('session rotation (resolver returns a new sid) -> reset snapshot pushed', () => {
  const tailer = makeTailer();
  const events: TailEvent[] = [];
  tailer.subscribe((ev) => events.push(ev));

  const newPath = join(dir, 'session-b.jsonl');
  writeFileSync(newPath, claudeTextLine('u-b-1', 'fresh session'));
  path = newPath;
  sid = 'session-b';
  tailer.tick();

  assert.equal(events.length, 2);
  const snap = events[1];
  assert.equal(snap.type, 'snapshot');
  assert.equal(snap.reset, true);
  assert.equal(snap.session_id, 'session-b');
  assert.equal(snap.items.length, 1);
  assert.equal(snap.items[0].text, 'fresh session');
});

test('snapshot respects the limit window; id still tracks the FULL offset', () => {
  const tailer = makeTailer(2);
  const events: TailEvent[] = [];
  tailer.subscribe((ev) => events.push(ev));
  const full = fullItems();
  const snap = events[0];
  assert.deepStrictEqual(snap.items, full.slice(-2));
  assert.equal(snap.id, `session-a:${full.length}`);
});

test('unsubscribe stops delivery', () => {
  const tailer = makeTailer();
  const events: TailEvent[] = [];
  const off = tailer.subscribe((ev) => events.push(ev));
  off();
  appendFileSync(path, '\n' + claudeTextLine('u-tail-3', 'after unsub'));
  tailer.tick();
  assert.equal(events.length, 1); // snapshot only
  assert.equal(tailer.subscriberCount, 0);
});
