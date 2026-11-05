// The live stream shows a headless seat's cut turn the way the poll route does: one "[TURN INTERRUPTED]" line after
// the turn its message opened, once, only for a message in view. The supervisor's file changes while the log does
// not, so the tailer watches it too. Temp files; the cut-turn list is injected.
// Run: npx tsx --test src/services/transcript-tail.interrupted.test.ts   (from api/)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, appendFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { TranscriptTailer, type TailEvent } from './transcript-tail.js';
import { INTERRUPTED_PREFIX, type InterruptedTurn } from '../routes/chat-transcript.js';

const ts = '2026-10-30T08:00:00Z';
const user = (uuid: string, text: string) =>
  JSON.stringify({ type: 'user', uuid, timestamp: ts, message: { role: 'user', content: text } }) + '\n';
const said = (uuid: string, id: string, text: string) =>
  JSON.stringify({ type: 'assistant', uuid, timestamp: ts,
    message: { id, role: 'assistant', content: [{ type: 'text', text }] } }) + '\n';

function rig(initial: string) {
  const f = join(mkdtempSync(join(tmpdir(), 'tail-int-')), 'session.jsonl');
  writeFileSync(f, initial);
  const box = { turns: [] as InterruptedTurn[], sig: '' };
  const t = new TranscriptTailer({
    agentId: 'dev-demo', resolve: () => ({ path: f, sid: 's' }),
    interrupted: { read: () => box.turns, sig: () => box.sig },
  });
  const got: TailEvent[] = [];
  t.subscribe((ev) => got.push(ev));
  const cut = (id: string, text: string) => { box.turns = [...box.turns, { id, text, at: ts }]; box.sig = String(box.turns.length); };
  return { f, t, got, cut };
}

const lines = (ev: TailEvent) => ev.items.filter((i: any) => String(i.text || '').startsWith(INTERRUPTED_PREFIX));

test('a turn cut at the end goes out as a delta with the line, with nothing appended to the log', () => {
  const r = rig(user('u1', 'Sample one') + said('a1', 'm1', 'Working on it'));
  assert.equal(lines(r.got[0]).length, 0);
  r.cut('x1', 'Sample one');
  r.t.tick();
  assert.equal(r.got.length, 2);
  const ev = r.got[1];
  assert.equal(ev.type, 'delta');
  assert.equal(ev.items.length, 1);
  assert.equal(ev.items[0].uuid, 'interrupted:x1');
  assert.ok(ev.items[0].text.includes('"Sample one"') && ev.items[0].machine === 'system' && ev.items[0].mid_turn);
  r.t.tick();
  assert.equal(r.got.length, 2, 'once');
});

test('a line that lands before the tail (a later turn already ran) goes out as a snapshot, in place', () => {
  const r = rig(user('u1', 'Sample one') + said('a1', 'm1', 'Working') + user('u2', 'Sample two') + said('a2', 'm2', 'Done'));
  r.cut('x1', 'Sample one');
  r.t.tick();
  const ev = r.got[r.got.length - 1];
  assert.equal(ev.type, 'snapshot');
  assert.equal(ev.reset, undefined, 'not a session reset');
  const uuids = ev.items.map((i: any) => i.uuid);
  assert.deepEqual(uuids.slice(0, 3), ['u1', 'a1', 'interrupted:x1']);
  assert.equal(uuids.indexOf('u2'), 3);
  assert.ok(ev.render_items && ev.render_items.length > 0);
});

test('the line stays in place as the log grows, and later deltas carry only the new items', () => {
  const r = rig(user('u1', 'Sample one') + said('a1', 'm1', 'Working'));
  r.cut('x1', 'Sample one');
  r.t.tick();
  appendFileSync(r.f, user('u2', 'Sample two'));
  r.t.tick();
  const ev = r.got[r.got.length - 1];
  assert.equal(ev.type, 'delta');
  assert.deepEqual(ev.items.map((i: any) => i.uuid), ['u2']);
});

test('a message not in view gets no line, and a new subscriber sees an existing line in its snapshot', () => {
  const r = rig(user('u1', 'Sample one') + said('a1', 'm1', 'Working'));
  r.cut('x2', 'Not in this window');
  r.t.tick();
  assert.equal(r.got.length, 1, 'nothing to send');
  r.cut('x1', 'Sample one');
  r.t.tick();
  const late: TailEvent[] = [];
  r.t.subscribe((ev) => late.push(ev));
  assert.equal(late[0].type, 'snapshot');
  assert.equal(lines(late[0]).length, 1);
});
