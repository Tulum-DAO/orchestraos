/**
 * Reply-to on the turn (P1, the API half): the store under <data dir>/state/reply-to.db, the body validation, and the
 * stamp onto transcript items. Run: ORCHESTRA_CONFIG=../orchestra.example.toml npx tsx --test src/lib/reply-to-store.test.ts (from api/)
 */
import { testTmpDir } from './testTmp.js';
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import Database from 'better-sqlite3';
import {
  ReplyToStore, replyToStore, replyToDbPath, stampReplyTo, validateReplyTo, EXCERPT_MAX, STAMP_WINDOW_MS, STAMP_SKEW_MS,
  type SentReply,
} from './reply-to-store.js';

const dir = testTmpDir('reply-to-store-');
const path = join(dir, 'state', 'reply-to.db');
const store = new ReplyToStore(path);
after(() => { store.close(); rmSync(dir, { recursive: true, force: true }); });

const T0 = '2026-11-26T02:00:00.000Z';
const PREFIX = '[Replying to gm 21:42 Local: "The folded header could carry the count"]';
const SENT = `${PREFIX} yes, and the time too`;
const send = (over: Partial<SentReply> = {}): SentReply =>
  ({ agent: 'gm', sent_text: SENT, reply_to: { uuid: 'u-orig-1', excerpt: 'The folded header could carry the count' }, message_id: 'msg_1', ts: T0, ...over });

test('the file is made under state/, in WAL mode, with the sent_replies table', () => {
  assert.ok(existsSync(path));
  const db = new Database(path, { readonly: true });
  assert.equal(db.pragma('journal_mode', { simple: true }), 'wal');
  const cols = (db.prepare('PRAGMA table_info(sent_replies)').all() as any[]).map((c) => c.name);
  assert.deepEqual(cols, ['id', 'agent', 'sent_text', 'reply_uuid', 'excerpt', 'message_id', 'ts']);
  db.close();
});

test('record then recent: newest first, the uuid optional, the message id kept', () => {
  store.record(send());
  store.record(send({ sent_text: 'second words', reply_to: { excerpt: 'only an excerpt' }, message_id: undefined, ts: '2026-11-26T02:00:01.000Z' }));
  const rows = store.recent('gm');
  assert.equal(rows.length, 2);
  assert.equal(rows[0].sent_text, 'second words');
  assert.deepEqual(rows[0].reply_to, { excerpt: 'only an excerpt' });
  assert.equal('message_id' in rows[0], false);
  assert.deepEqual(rows[1].reply_to, { uuid: 'u-orig-1', excerpt: 'The folded header could carry the count' });
  assert.equal(rows[1].message_id, 'msg_1');
  assert.deepEqual(store.recent('other-seat'), []);
});

test('rows older than seven days are pruned on write', () => {
  store.record(send({ agent: 'prune', ts: '2026-11-01T00:00:00.000Z', sent_text: 'old' }));
  assert.equal(store.recent('prune').length, 1);
  store.record(send({ agent: 'prune', ts: '2026-11-26T00:00:00.000Z', sent_text: 'new' }));
  assert.deepEqual(store.recent('prune').map((r) => r.sent_text), ['new']);
});

test('validateReplyTo: a bad uuid is dropped and the excerpt kept; no excerpt drops the whole field; the excerpt is one line, capped', () => {
  assert.deepEqual(validateReplyTo({ uuid: 'abc-123:~#.x', excerpt: 'quote' }), { uuid: 'abc-123:~#.x', excerpt: 'quote' });
  assert.deepEqual(validateReplyTo({ uuid: 'has space', excerpt: 'quote' }), { excerpt: 'quote' });
  assert.deepEqual(validateReplyTo({ uuid: 'x'.repeat(201), excerpt: 'quote' }), { excerpt: 'quote' });
  assert.deepEqual(validateReplyTo({ uuid: 7, excerpt: 'quote' }), { excerpt: 'quote' });
  assert.equal(validateReplyTo({ uuid: 'abc' }), undefined);
  assert.equal(validateReplyTo({ uuid: 'abc', excerpt: '   ' }), undefined);
  assert.equal(validateReplyTo({ uuid: 'abc', excerpt: 42 }), undefined);
  assert.equal(validateReplyTo('quote'), undefined);
  assert.equal(validateReplyTo(['quote']), undefined);
  assert.equal(validateReplyTo(null), undefined);
  const long = validateReplyTo({ excerpt: ` a\nb\r\n${'c'.repeat(300)} ` })!;
  assert.equal(long.excerpt.length, EXCERPT_MAX);
  assert.ok(long.excerpt.startsWith('a b c'));
});

const items = (over: any[] = []) => [
  { kind: 'text', role: 'user', text: SENT, ts: '2026-11-26T02:00:03.000Z', uuid: 'line-1' },
  { kind: 'text', role: 'assistant', text: SENT, ts: '2026-11-26T02:00:04.000Z', uuid: 'line-2' },
  { kind: 'text', role: 'user', text: 'unrelated words', ts: '2026-11-26T02:00:05.000Z', uuid: 'line-3' },
  { kind: 'text', role: 'user', text: SENT, ts: '2026-11-26T02:00:06.000Z', uuid: 'line-4', machine: 'relay' },
  { kind: 'tool_result', role: 'user', text: SENT, uuid: 'line-5' },
  ...over,
];

test('stampReplyTo: the user text item whose words are the send gets reply_to; assistant, machine, tool and other items do not', () => {
  const out = stampReplyTo(items(), 'gm', [send()]);
  assert.deepEqual(out[0].reply_to, { uuid: 'u-orig-1', excerpt: 'The folded header could carry the count' });
  assert.equal(out[0].uuid, 'line-1');
  for (const i of [1, 2, 3, 4]) assert.equal('reply_to' in out[i], false, `item ${i}`);
  // the input items are not mutated
  assert.equal('reply_to' in items()[0], false);
});

test('stampReplyTo: equal, or the words followed only by a server tag the reader did not strip; same agent only; within the window', () => {
  const at = (ms: number) => new Date(Date.parse(T0) + ms).toISOString();
  const withTag = [{ kind: 'text', role: 'user', text: `${SENT} <page-context>The person is looking at this page in the dashboard as they send this: /agents/gm. Use it only if they ask about the page; do not mention it otherwise.</page-context>`, ts: at(3000), uuid: 'a' }];
  assert.equal(stampReplyTo(withTag, 'gm', [send()])[0].reply_to.uuid, 'u-orig-1', 'the page tag after the words');
  const withVoice = [{ kind: 'text', role: 'user', text: `${SENT} <voice-call-live>The operator is on a live voice call with you right now.</voice-call-live>`, ts: at(3000), uuid: 'a2' }];
  assert.equal(stampReplyTo(withVoice, 'gm', [send()])[0].reply_to.uuid, 'u-orig-1', 'the voice tag after the words');
  const withBoth = [{ kind: 'text', role: 'user', text: `${SENT} <page-context>x</page-context> <voice-call-live>y</voice-call-live>`, ts: at(3000), uuid: 'a3' }];
  assert.equal(stampReplyTo(withBoth, 'gm', [send()])[0].reply_to.uuid, 'u-orig-1', 'both tags after the words');
  assert.equal('reply_to' in stampReplyTo(withTag, 'other', [send()])[0], false, 'another agent');
  // more words after the send are a DIFFERENT message, not this send: "ok" never stamps "okay" or "ok, and also..."
  const okSend = send({ sent_text: 'ok' });
  for (const text of ['okay then', 'ok, and the other thing', 'ok [page: /agents/gm]', 'ok\nmore']) {
    const other = [{ kind: 'text', role: 'user', text, ts: at(3000), uuid: 'f' }];
    assert.equal('reply_to' in stampReplyTo(other, 'gm', [okSend])[0], false, `longer words: ${JSON.stringify(text)}`);
  }
  assert.equal(stampReplyTo([{ kind: 'text', role: 'user', text: 'ok', ts: at(3000), uuid: 'g' }], 'gm', [okSend])[0].reply_to.uuid, 'u-orig-1', 'the exact words');
  const late = [{ kind: 'text', role: 'user', text: SENT, ts: at(STAMP_WINDOW_MS + 1000), uuid: 'b' }];
  assert.equal('reply_to' in stampReplyTo(late, 'gm', [send()])[0], false, 'outside the window');
  // the item is written when the words reach the agent, so it is never meaningfully BEFORE the send: a line from
  // before the send is an earlier message that happens to read the same (only a bounded clock skew is allowed)
  const earlier = [{ kind: 'text', role: 'user', text: SENT, ts: at(-10 * 60 * 1000), uuid: 'c' }];
  assert.equal('reply_to' in stampReplyTo(earlier, 'gm', [send()])[0], false, 'ten minutes before the send');
  const skew = [{ kind: 'text', role: 'user', text: SENT, ts: at(-STAMP_SKEW_MS + 1000), uuid: 'c2' }];
  assert.equal(stampReplyTo(skew, 'gm', [send()])[0].reply_to.uuid, 'u-orig-1', 'within the clock skew allowance');
  const pastSkew = [{ kind: 'text', role: 'user', text: SENT, ts: at(-STAMP_SKEW_MS - 1000), uuid: 'c3' }];
  assert.equal('reply_to' in stampReplyTo(pastSkew, 'gm', [send()])[0], false, 'just past the clock skew allowance');
  const noTs = [{ kind: 'text', role: 'user', text: SENT, uuid: 'd' }];
  assert.equal(stampReplyTo(noTs, 'gm', [send()])[0].reply_to.uuid, 'u-orig-1', 'an item with no ts matches on the words alone');
  const shorter = [{ kind: 'text', role: 'user', text: PREFIX, ts: at(3000), uuid: 'e' }];
  assert.equal('reply_to' in stampReplyTo(shorter, 'gm', [send()])[0], false, 'a shorter text is not the send');
});

test('stampReplyTo: the newest send wins when two match; an exact match beats a newer tagged one; an item already stamped is left alone', () => {
  const sends = [send({ reply_to: { uuid: 'newer', excerpt: 'n' }, ts: '2026-11-26T02:00:02.000Z' }), send()];
  assert.equal(stampReplyTo(items(), 'gm', sends)[0].reply_to.uuid, 'newer');
  // "ok" sent newest, and earlier "ok <page-context>..</page-context>" typed verbatim: the item reading exactly the
  // latter is the latter's send, not the newer "ok" plus a tag
  const taggedWords = 'ok <page-context>typed by hand</page-context>';
  const two = [send({ sent_text: 'ok', reply_to: { uuid: 'short', excerpt: 's' }, ts: '2026-11-26T02:00:02.000Z' }),
    send({ sent_text: taggedWords, reply_to: { uuid: 'exact', excerpt: 'e' } })];
  const item = [{ kind: 'text', role: 'user', text: taggedWords, ts: '2026-11-26T02:00:03.000Z', uuid: 'h' }];
  assert.equal(stampReplyTo(item, 'gm', two)[0].reply_to.uuid, 'exact');
  const pre = [{ kind: 'text', role: 'user', text: SENT, uuid: 'x', reply_to: { excerpt: 'mine' } }];
  assert.deepEqual(stampReplyTo(pre, 'gm', [send()])[0].reply_to, { excerpt: 'mine' });
});

test('stampReplyTo: no sends, or no stampable item, returns the same array; a store that cannot be read is fail-open', () => {
  const it = items();
  assert.equal(stampReplyTo(it, 'gm', []), it);
  const only = [{ kind: 'thinking', role: 'assistant', text: 'x' }];
  assert.equal(stampReplyTo(only, 'gm', [send()]), only);
  // the default store path points at a directory that is a FILE: opening fails, the items come back as they were
  const prev = process.env.ORCHESTRA_DIR;
  process.env.ORCHESTRA_DIR = join(dir, 'not-a-dir');
  try {
    const out = stampReplyTo(it, 'gm');
    assert.ok(Array.isArray(out) && out.length === it.length);
  } finally {
    if (prev === undefined) delete process.env.ORCHESTRA_DIR; else process.env.ORCHESTRA_DIR = prev;
  }
});

test('the process-wide store opens once per path, under state/ of the data dir', () => {
  const p = join(dir, 'other', 'state', 'reply-to.db');
  const a = replyToStore(p);
  assert.equal(replyToStore(p), a);
  assert.ok(existsSync(p));
  assert.equal(replyToDbPath('/x/data'), '/x/data/state/reply-to.db');
  a.close();
});
