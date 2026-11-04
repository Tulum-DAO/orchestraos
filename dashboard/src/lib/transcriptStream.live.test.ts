/**
 * The live words a headless seat's stream sends (event: live_text) as the chat draws them: after
 * the thread, never a block whose finished line the thread already holds.
 *
 * Run: npx tsx --test src/lib/transcriptStream.live.test.ts   (from dashboard/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { liveTextOf } from './transcriptStream';
import type { ChatItem, LiveTextEvent } from './transcript.gen';

const ev = (blocks: { uuid: string | null; text: string }[], done = false): LiveTextEvent => ({
  agent_id: 'pm-demo', message_id: 'msg_example', text: blocks.map((b) => b.text).join('\n\n'), blocks, done,
});
const line = (uuid: string, text: string) => ({ kind: 'text', role: 'assistant', text, uuid }) as ChatItem;

test('deltas so far are the live text', () => {
  assert.deepEqual(liveTextOf(ev([{ uuid: null, text: 'Looking at it' }]), []), { text: 'Looking at it', blocks: [{ uuid: null, text: 'Looking at it' }] });
  // each block on its own (drawn, revealed and handed off to its row on its own), and whether the turn has ended
  assert.deepEqual(liveTextOf(ev([{ uuid: 'u-1', text: 'One.' }, { uuid: null, text: 'Two' }], true), []),
    { text: 'One.\n\nTwo', blocks: [{ uuid: 'u-1', text: 'One.' }, { uuid: null, text: 'Two' }], done: true });
});

test('a block whose line is already in the thread is not drawn again (the frame before the server clears it)', () => {
  const live = ev([{ uuid: 'u-1', text: 'First paragraph.' }, { uuid: null, text: 'Second' }]);
  assert.deepEqual(liveTextOf(live, [line('u-1', 'First paragraph.')])?.text, 'Second');
  assert.deepEqual(liveTextOf(live, [line('u-1', 'First paragraph.')])?.blocks, [{ uuid: null, text: 'Second' }]);
  assert.equal(liveTextOf(ev([{ uuid: 'u-1', text: 'Only' }], true), [line('u-1', 'Only')]), null, 'all landed: nothing live');
});

test('a cleared frame, or none, draws nothing', () => {
  assert.equal(liveTextOf(ev([]), []), null);
  assert.equal(liveTextOf(null, []), null);
  assert.equal(liveTextOf(ev([{ uuid: null, text: '   ' }]), []), null);
});
