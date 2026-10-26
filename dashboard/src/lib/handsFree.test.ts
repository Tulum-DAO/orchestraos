import { test } from 'node:test';
import assert from 'node:assert/strict';
import { HandsFreeCall, isInCall } from './handsFree.ts';
import type { VoiceSessionCallbacks, VoiceSessionStartOptions } from './voiceSession.ts';
import { muteTail, MUTE_TAIL_SECONDS } from './voiceSession.ts';

// pm-tulumdao (2026-10-08): the home composer's live-call button ("Live voice mode") had no call behind it.
// A label that does nothing is the same dishonesty the relabel removes. Both surfaces now drive this.

function fake(outcome: 'opens' | 'refused') {
  const log: string[] = [];
  let cb: VoiceSessionCallbacks = {};
  const make = (c: VoiceSessionCallbacks) => {
    cb = c;
    return {
      async start(o: VoiceSessionStartOptions) {
        log.push(`start ${o.route} ${o.focusedEntity ?? '-'}`);
        if (outcome === 'opens') cb.onStateChange?.('connecting');
        else { cb.onUnavailable?.("voice isn't configured yet: GEMINI_API_KEY missing on server"); cb.onStateChange?.('error'); }
      },
      stop() { log.push('stop'); cb.onStateChange?.('idle'); },
      startDictation() { log.push('captions on'); },
      stopDictation() { log.push('captions off'); },
    };
  };
  return { log, make, cb: () => cb };
}

test('a tap starts the call, with the page context, and turns the captions on', async () => {
  const f = fake('opens');
  const states: string[] = [];
  const call = new HandsFreeCall(f.make, { onState: (s) => states.push(s) });
  await call.toggle({ route: '/', focusedEntity: null });
  assert.deepEqual(f.log, ['start / -', 'captions on']);
  assert.equal(call.inCall, true);
  assert.deepEqual(states, ['connecting']);
});

test('a second tap ends it, and the captions end with it', async () => {
  const f = fake('opens');
  const call = new HandsFreeCall(f.make);
  await call.toggle({ route: '/' });
  await call.toggle({ route: '/' });
  assert.deepEqual(f.log, ['start / -', 'captions on', 'stop', 'captions off', 'captions off']);
  assert.equal(call.inCall, false);
});

test('a refused call says why and never turns the captions on', async () => {
  const f = fake('refused');
  const notes: string[] = [];
  const call = new HandsFreeCall(f.make, { onUnavailable: (r) => notes.push(r) });
  await call.toggle({ route: '/approvals', focusedEntity: 'approval:a1' });
  assert.ok(!f.log.includes('captions on'));
  assert.match(notes[0], /GEMINI_API_KEY/);
  assert.equal(call.inCall, false);
});

test('captions and the end line reach the page', async () => {
  const f = fake('opens');
  const got: string[] = [];
  const call = new HandsFreeCall(f.make, {
    onPartial: (t, r) => got.push(`~${r}:${t}`), onFinal: (t, r) => got.push(`${r}:${t}`), onEnded: (t) => got.push(t),
  });
  await call.toggle({ route: '/' });
  f.cb().onPartial?.('hel', 'user');
  f.cb().onFinal?.('hello', 'user');
  f.cb().onFinal?.('hi Shaw', 'arturo');
  f.cb().onCallEnded?.('[voice-call: vc_1]', 'vc_1');
  assert.deepEqual(got, ['~user:hel', 'user:hello', 'arturo:hi Shaw', 'Call ended (vc_1). [voice-call: vc_1]']);
});

test('in a call means connecting or live', () => {
  assert.equal(isInCall('connecting'), true);
  assert.equal(isInCall('live'), true);
  assert.equal(isInCall('idle'), false);
  assert.equal(isInCall('error'), false);
});

// ── mute: only the caller's microphone stops; nothing else does ──────────────

test('mute reaches the session only during a call, and a call ends unmuted', async () => {
  const calls: boolean[] = [];
  const seen: boolean[] = [];
  let cb: any = null;
  const make = (c: any) => {
    cb = c;
    return {
      start: async () => { c.onStateChange?.('live'); }, stop: () => { c.onStateChange?.('idle'); },
      startDictation: () => {}, stopDictation: () => {}, setMuted: (m: boolean) => { calls.push(m); },
    };
  };
  const call = new HandsFreeCall(make, { onMuted: (m) => seen.push(m) });
  call.setMuted(true);                                  // no call: nothing to mute
  assert.deepEqual(calls, []);
  await call.toggle({ route: '/' });
  call.toggleMute();
  assert.deepEqual(calls, [true]);
  assert.equal(call.muted, true);
  cb.onStateChange('idle');                             // the call ends while muted
  assert.equal(call.muted, false);
  assert.deepEqual(seen, [true, false]);
});

test('the one frame sent on mute is generated silence, never microphone audio', () => {
  const tail = muteTail();
  assert.equal(tail.length, Math.round(16000 * MUTE_TAIL_SECONDS));
  assert.equal(tail.every((x) => x === 0), true);
});
