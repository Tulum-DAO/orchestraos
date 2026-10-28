import { test } from 'node:test';
import assert from 'node:assert/strict';
import { groupCalls, callEndOf, fmtClock, transcriptText, type CallTurn } from './arturoCallGroups.ts';

const MARK = (id: string) => `Call ended (${id}). [voice-call: ${id} /x/state/voice-calls/${id}.json]`;
const T = (role: 'user' | 'arturo', text: string, extra: Partial<CallTurn> = {}): CallTurn => ({ role, text, ...extra });

test('a call becomes one card; typed turns before and after stay outside it', () => {
  const items = groupCalls([
    T('user', 'typed before'),
    T('user', 'can you hear me', { voice: true, ts: 100 }),
    T('arturo', 'yes', { voice: true, ts: 102 }),
    T('arturo', MARK('vc_client_aaaa'), { voice: true, callEnd: 'vc_client_aaaa', ts: 115 }),
    T('user', 'typed after'),
  ], false);
  assert.deepEqual(items.map((x) => x.kind === 'turn' ? `turn:${x.turn.text}` : `call:${x.callId}:${x.turns.map((t) => t.text).join('/')}`),
    ['turn:typed before', 'call:vc_client_aaaa:can you hear me/yes', 'turn:typed after']);
  const call = items[1];
  assert.ok(call.kind === 'call' && call.startTs === 100 && call.endTs === 115 && !call.live);
});

test('before a reload the call-ended line is recognised by its marker alone', () => {
  assert.equal(callEndOf(T('arturo', MARK('vc_client_bbbb'))), 'vc_client_bbbb');
  assert.equal(callEndOf(T('arturo', `Here is what we said.\n${MARK('vc_client_bbbb')}`)), null);   // the brain's words stay a turn
  assert.equal(callEndOf(T('user', MARK('vc_client_bbbb'))), null);
});

test('during a call the turns so far are the live card, last in the feed', () => {
  const items = groupCalls([T('user', 'typed'), T('user', 'hello', { voice: true }), T('arturo', 'hi', { voice: true })], true);
  const last = items[items.length - 1];
  assert.ok(last.kind === 'call' && last.live && last.turns.length === 2);
  const empty = groupCalls([T('user', 'typed')], true);                 // connecting: an empty live card
  assert.ok(empty[1].kind === 'call' && empty[1].live && empty[1].turns.length === 0);
});

test('tool runs during a call go in its card; an ending with no spoken turns is still a card', () => {
  const items = groupCalls([T('user', 'look it up', { voice: true }), T('arturo', '', { callTools: true }), T('arturo', MARK('vc_client_cccc'))], false);
  assert.equal(items.length, 1);
  assert.ok(items[0].kind === 'call' && items[0].turns.length === 2);
  const bare = groupCalls([T('arturo', MARK('vc_client_dddd'))], false);
  assert.ok(bare[0].kind === 'call' && bare[0].callId === 'vc_client_dddd' && bare[0].turns.length === 0);
});

test('a call length reads like a phone timer', () => {
  assert.equal(fmtClock(15), '00:15');
  assert.equal(fmtClock(724.6), '12:04');
  assert.equal(fmtClock(3723), '1:02:03');
  assert.equal(fmtClock(undefined), '--:--');
});

test('the copied transcript names who said what', () => {
  assert.equal(transcriptText([{ role: 'user', text: 'hi ' }, { role: 'arturo', text: '' }, { role: 'arturo', text: 'hello' }]), 'You: hi\nArturo: hello');
});
