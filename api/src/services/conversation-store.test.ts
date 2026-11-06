/**
 * The API's read side of the conversation store (services/conversation-store.ts): the earlier-conversation strip
 * rule (only for entries marked seen-only), seed rows, the compaction notices and their placement. A fixture store
 * in a temp dir with the writer's tables (scripts/episodic_memory.py).
 */
import { testTmpDir } from '../lib/testTmp.js';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import Database from 'better-sqlite3';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { stripBlock, noticeShown, storeRules, applyStoreRules, applyRulesToLines, compactionNotices, compactionItem, mergeNotices, forgetStoreCache } from './conversation-store.js';

const BLOCK = '<earlier-conversation>\nEarlier: the Sample plan.\n</earlier-conversation>\n';

function fixture() {
  const dir = testTmpDir('cstore-');
  mkdirSync(join(dir, 'state'));
  const p = join(dir, 'state', 'episodic-memory.db');
  const db = new Database(p);
  db.exec(`CREATE TABLE seen_only (provider_session_id TEXT, uuid TEXT, marker TEXT, PRIMARY KEY (provider_session_id, uuid));
    CREATE TABLE seed_boundary (provider_session_id TEXT PRIMARY KEY, through_uuid TEXT NOT NULL);
    CREATE TABLE compaction_notices (id INTEGER PRIMARY KEY, agent TEXT NOT NULL, state TEXT NOT NULL, started_at TEXT NOT NULL,
      finished_at TEXT, before_tokens INTEGER, after_tokens INTEGER, from_session TEXT, to_session TEXT, trigger TEXT NOT NULL DEFAULT 'auto');`);
  return { p, db };
}

test('the block is removed only from an entry marked seen-only in that session', () => {
  forgetStoreCache();
  const { p, db } = fixture();
  db.prepare('INSERT INTO seen_only VALUES (?, ?, ?)').run('sess-1', 'u1', 'earlier-conversation');
  db.close();
  assert.equal(stripBlock(BLOCK + 'What is next?'), 'What is next?');
  assert.equal(stripBlock('a <earlier-conversation>x'), 'a ', 'an unclosed block runs to the end');
  assert.equal(stripBlock('plain'), 'plain');
  const items = [
    { kind: 'text', role: 'user', uuid: 'u1', text: BLOCK + 'What is next?' },
    { kind: 'text', role: 'user', uuid: 'u2', text: BLOCK + 'I pasted this myself' },
    { kind: 'text', role: 'assistant', uuid: 'a1', text: 'reply' },
  ];
  const r = storeRules('sess-1', p);
  const out = applyStoreRules(items, r);
  assert.deepEqual(out.map((i) => i.text), ['What is next?', BLOCK + 'I pasted this myself', 'reply']);
  assert.equal(items[0].text.startsWith('<earlier'), true, 'never edited in place');
  assert.equal(applyStoreRules(items, storeRules('sess-2', p)), items, 'another session: untouched');
  const only = applyStoreRules([{ kind: 'text', role: 'user', uuid: 'u1', text: BLOCK }], r);
  assert.deepEqual(only, [], 'an entry that was only the block is left out');
  assert.equal(storeRules('sess-1', join(p, '..', 'none.db')).seen.size, 0, 'no store: nothing to apply');
});

test('seed rows: everything at or before the boundary is left out; before it is written, all of it', () => {
  forgetStoreCache();
  const { p, db } = fixture();
  db.prepare('INSERT INTO seed_boundary VALUES (?, ?)').run('sess-s', 'seed-2');
  db.close();
  const items = [{ uuid: 'seed-1', text: 'a' }, { uuid: 'seed-2', text: 'b' }, { uuid: 'seed-2', text: 'b2' }, { uuid: 'real-1', text: 'c' }];
  assert.deepEqual(applyStoreRules(items, storeRules('sess-s', p)).map((i) => i.uuid), ['real-1']);
  assert.deepEqual(applyStoreRules([{ uuid: 'seed-1', text: 'a' }], storeRules('sess-s', p)), [], 'boundary not yet written: all seed');
  // the same rules on raw transcript lines (library attribution), with the boundary passed in an earlier chunk
  const line = (uuid: string, text: string) => JSON.stringify({ type: 'user', uuid, message: { role: 'user', content: text } });
  const a = applyRulesToLines([line('seed-1', 'x'), line('seed-2', 'y'), line('real-1', 'z')], storeRules('sess-s', p));
  assert.deepEqual(a.lines.map((l) => JSON.parse(l).uuid), ['real-1']);
  assert.equal(a.seedDone, true);
  assert.deepEqual(applyRulesToLines([line('real-2', 'w')], storeRules('sess-s', p), true).lines.length, 1);
  assert.deepEqual(applyRulesToLines([line('seed-1', 'w')], storeRules('sess-s', p), false).lines, []);
});

test('raw lines: the block is stripped from a seen-only line, in a string or a text block', () => {
  forgetStoreCache();
  const { p, db } = fixture();
  db.prepare('INSERT INTO seen_only VALUES (?, ?, ?)').run('sess-1', 'u1', 'earlier-conversation');
  db.close();
  const l1 = JSON.stringify({ type: 'user', uuid: 'u1', message: { role: 'user', content: [{ type: 'text', text: BLOCK + 'carry on with /tmp/sample.md' }] } });
  const l2 = JSON.stringify({ type: 'user', uuid: 'u2', message: { role: 'user', content: BLOCK + 'mine' } });
  const out = applyRulesToLines([l1, l2], storeRules('sess-1', p)).lines;
  assert.equal(JSON.parse(out[0]).message.content[0].text, 'carry on with /tmp/sample.md');
  assert.equal(out[1], l2);
});

test('notices: oldest first per seat, as items with a stable id, placed by time within a range', () => {
  forgetStoreCache();
  const { p, db } = fixture();
  const add = db.prepare('INSERT INTO compaction_notices(agent, state, started_at, finished_at, before_tokens, after_tokens) VALUES (?, ?, ?, ?, ?, ?)');
  add.run('qa-demo', 'compacted', '2026-10-31T07:00:05Z', '2026-10-31T07:00:30Z', 150000, 40000);
  add.run('qa-demo', 'compacting', '2026-10-31T08:00:00Z', null, 160000, null);
  add.run('dev-demo', 'failed', '2026-10-31T07:30:00Z', '2026-10-31T07:31:00Z', 1, null);
  db.close();
  const n = compactionNotices('qa-demo', p);
  assert.deepEqual(n.map((x) => x.state), ['compacted', 'compacting']);
  assert.deepEqual(compactionItem(n[0]), {
    kind: 'compaction', id: 'cmp-1', uuid: 'cmp-1', state: 'compacted', trigger: 'auto', ts: '2026-10-31T07:00:05Z',
    finished_at: '2026-10-31T07:00:30Z', before_tokens: 150000, after_tokens: 40000, audience: 'user',
  });
  const items = [
    { uuid: 'a', ts: '2026-10-31T07:00:00.000Z' }, { uuid: 'b', ts: '2026-10-31T07:00:10.000Z' },
    { uuid: 'nots' }, { uuid: 'c', ts: '2026-10-31T09:00:00.000Z' },
  ];
  assert.deepEqual(mergeNotices(items, n, { from: null, to: null }).map((i: any) => i.uuid), ['a', 'cmp-1', 'b', 'nots', 'cmp-2', 'c']);
  assert.deepEqual(mergeNotices(items, n, { from: '2026-10-31T07:30:00Z', to: null }).map((i: any) => i.uuid), ['a', 'b', 'nots', 'cmp-2', 'c']);
  assert.deepEqual(mergeNotices(items, n, { from: null, to: '2026-10-31T07:30:00.000Z' }).map((i: any) => i.uuid), ['a', 'cmp-1', 'b', 'nots', 'c']);
  assert.deepEqual(mergeNotices([], n, { from: null, to: null }).map((i: any) => i.uuid), ['cmp-1', 'cmp-2']);
  assert.deepEqual(compactionNotices('pm-demo', p), []);
});

test('the compact note follows the same rule as the earlier conversation', () => {
  forgetStoreCache();
  const { p, db } = fixture();
  db.prepare('INSERT INTO seen_only VALUES (?, ?, ?)').run('sess-1', 'u1', 'compact-note');
  db.close();
  const note = "<compact-note>\nYour earlier request to compact this chat didn't go through.\n</compact-note>\n";
  assert.equal(stripBlock(note + 'Carry on.'), 'Carry on.');
  assert.equal(stripBlock('<earlier-conversation>a</earlier-conversation>\n' + note + 'real'), 'real');
  assert.equal(stripBlock('x <compact-note>unclosed'), 'x ');
  const out = applyStoreRules([{ uuid: 'u1', text: note + 'Carry on.' }, { uuid: 'u2', text: note + 'typed' }], storeRules('sess-1', p));
  assert.deepEqual(out.map((i) => i.text), ['Carry on.', note + 'typed']);
  const raw = JSON.stringify({ type: 'user', uuid: 'u1', message: { role: 'user', content: note + 'Carry on.' } });
  assert.equal(JSON.parse(applyRulesToLines([raw], storeRules('sess-1', p)).lines[0]).message.content, 'Carry on.');
});

test('a failed notice is shown only when a person\'s /compact started it', () => {
  const n = (state: any, trigger: any) => ({ id: 1, agent: 'qa-demo', state, trigger, started_at: '2026-10-31T07:00:00Z', finished_at: null,
                                            before_tokens: null, after_tokens: null, from_session: null, to_session: null });
  assert.equal(noticeShown(n('failed', 'manual')), true);
  assert.equal(noticeShown(n('failed', 'auto')), false);
  assert.equal(noticeShown(n('failed', null)), false, 'an older row with no trigger reads as automatic');
  assert.equal(noticeShown(n('compacting', 'auto')), true);
  assert.equal(noticeShown(n('compacted', 'manual')), true);
  assert.deepEqual(mergeNotices([], [n('failed', 'auto'), { ...n('failed', 'manual'), id: 2 }], { from: null, to: null }).map((i: any) => [i.id, i.trigger]),
                   [['cmp-2', 'manual']]);
});
