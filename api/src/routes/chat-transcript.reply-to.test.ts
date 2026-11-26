/**
 * reply_to stamped onto the operator's transcript item (reply threads P1, the API half): a send recorded in
 * state/reply-to.db under the data dir is found again on the user text item whose words are that send, through the
 * poll route's window finisher (normalizeTranscript -> finishWindow). The SSE tailer runs the same stampReplyTo after
 * its own normalize; the stamp function itself is tested in lib/reply-to-store.test.ts.
 *
 * Run: ORCHESTRA_CONFIG=../orchestra.example.toml npx tsx --test src/routes/chat-transcript.reply-to.test.ts (from api/)
 */
import { testTmpDir } from '../lib/testTmp.js';
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { rmSync } from 'node:fs';

const dir = testTmpDir('reply-to-route-');
process.env.ORCHESTRA_DIR = dir;
const { ReplyToStore, replyToDbPath } = await import('../lib/reply-to-store.js');
const { normalizeTranscript } = await import('./chat-transcript.js');
after(() => rmSync(dir, { recursive: true, force: true }));

const T = (s: number) => `2026-11-26T02:${String(10 + s).padStart(2, '0')}:00.000Z`;
const userLine = (uuid: string, s: number, text: string) =>
  JSON.stringify({ type: 'user', uuid, timestamp: T(s), message: { role: 'user', content: text } });
const assistantLine = (uuid: string, s: number, text: string) =>
  JSON.stringify({ type: 'assistant', uuid, timestamp: T(s), message: { role: 'assistant', content: [{ type: 'text', text }] } });

const SENT = '[Replying to gm 21:42 Local: "the count"] yes, and the time too';
const store = new ReplyToStore(replyToDbPath(dir));
store.record({ agent: 'seat-a', sent_text: SENT, reply_to: { uuid: 'orig-uuid-1', excerpt: 'the count' }, message_id: 'msg_1', ts: T(0) });
store.record({ agent: 'seat-a', sent_text: 'another send', reply_to: { excerpt: 'no uuid here' }, ts: T(0) });
store.close();

const lines = [
  userLine('u1', 1, 'hello'),
  assistantLine('a1', 2, 'hi'),
  userLine('u2', 3, SENT),
  assistantLine('a2', 4, SENT),                     // the agent quoting the same words is not the operator's send
  userLine('u3', 5, 'another send [page: /agents/seat-a]'),
];

test('the poll window stamps reply_to on the operator item whose words were sent with one, by uuid and excerpt', () => {
  const env = normalizeTranscript(lines, 'seat-a', 'sid-1', 50, false, false, null, true, false) as any;
  const by = (uuid: string) => env.items.find((it: any) => it.uuid === uuid);
  assert.deepEqual(by('u2').reply_to, { uuid: 'orig-uuid-1', excerpt: 'the count' });
  assert.equal('reply_to' in by('u1'), false, 'a message that answered nothing');
  assert.equal('reply_to' in by('a2'), false, 'the agent, not the operator');
  assert.deepEqual(by('u3').reply_to, { excerpt: 'no uuid here' }, 'a send with no uuid still carries its excerpt; the page tag after the words does not break the match');
});

test('another agent\'s window is not stamped with this agent\'s sends', () => {
  const env = normalizeTranscript(lines, 'seat-b', 'sid-2', 50, false, false, null, true, false) as any;
  assert.ok(env.items.every((it: any) => !('reply_to' in it)));
});
