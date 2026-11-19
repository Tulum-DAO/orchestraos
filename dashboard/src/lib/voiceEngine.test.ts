import { test } from 'node:test';
import assert from 'node:assert/strict';
import { EngineSwitch, type NativeCallbacks } from './voiceEngine.ts';
import { HandsFreeCall } from './handsFree.ts';
import type { VoiceSessionCallbacks, VoiceSessionStartOptions, VoiceSessionState } from './voiceSession.ts';

// The hands-free call picks its engine per call: native GPT-Live when /api/voice/native/ready says ok, else the
// relay. The UI drives one interface (HandsFreeCall) and must not be able to tell which engine ran.

function rig(ready: { ok: boolean; reason?: string } | null | 'hang') {
  const log: string[] = [];
  const states: VoiceSessionState[] = [];
  let nativeCb: NativeCallbacks = {};
  let release: () => void = () => {};
  const session = (name: string, getCb: () => VoiceSessionCallbacks) => ({
    async start(o: VoiceSessionStartOptions) { log.push(`${name} start ${o.route}`); getCb().onStateChange?.('connecting'); },
    stop() { log.push(`${name} stop`); getCb().onStateChange?.('idle'); },
    startDictation() { log.push(`${name} captions on`); },
    stopDictation() { log.push(`${name} captions off`); },
    sendContext(o: VoiceSessionStartOptions) { log.push(`${name} context ${o.route}`); },
    setMuted(m: boolean) { log.push(`${name} muted ${m}`); },
  });
  let relayCb: VoiceSessionCallbacks = {};
  const make = (cb: VoiceSessionCallbacks) => new EngineSwitch(cb, {
    makeNative: (c) => { nativeCb = c; return session('native', () => nativeCb); },
    makeRelay: (c) => { relayCb = c; return session('relay', () => relayCb); },
    ready: () => ready === 'hang' ? new Promise((r) => { release = () => r({ ok: true }); }) : Promise.resolve(ready),
    log: (m) => log.push(m),
  });
  const call = new HandsFreeCall(make, { onState: (s) => states.push(s) });
  return { log, states, call, native: () => nativeCb, release: () => release() };
}

test('ready ok: the call runs native, and mute/context reach the native session', async () => {
  const r = rig({ ok: true });
  await r.call.toggle({ route: '/' });
  assert.ok(r.log.includes('[voice] engine: native'));
  assert.ok(r.log.includes('native start /'));
  assert.ok(!r.log.some((l) => l.startsWith('relay')));
  r.call.setMuted(true);
  r.call.setContext({ route: '/approvals' });
  assert.ok(r.log.includes('native muted true'));
  assert.ok(r.log.includes('native context /approvals'));
  await r.call.toggle({ route: '/' });   // hang up
  assert.ok(r.log.includes('native stop'));
  assert.equal(r.call.state, 'idle');
});

test('ready not ok (or no answer): the relay runs, and the log says why', async () => {
  const r = rig({ ok: false, reason: 'Native voice is switched off' });
  await r.call.toggle({ route: '/' });
  assert.ok(r.log.includes('[voice] engine: relay (native: Native voice is switched off)'));
  assert.ok(r.log.includes('relay start /'));
  assert.ok(r.log.includes('relay captions on'));
  const n = rig(null);
  await n.call.toggle({ route: '/' });
  assert.ok(n.log.includes('relay start /'));
});

test('an agent\'s call never asks native and always runs on the relay', async () => {
  const r = rig({ ok: true });
  await r.call.toggle({ route: '/agent/x', agentId: 'dev3' });
  assert.ok(r.log.includes('relay start /agent/x'));
  assert.ok(!r.log.some((l) => l.startsWith('native')));
});

test('native refused before going live (down / off): the same call restarts on the relay, once', async () => {
  const r = rig({ ok: true });
  await r.call.toggle({ route: '/projects' });
  r.native().onFallback?.('unavailable: native voice is switched off (HTTP 503)');
  r.native().onFallback?.('again');
  await new Promise((res) => setTimeout(res, 0));
  assert.equal(r.log.filter((l) => l === 'relay start /projects').length, 1);
  assert.ok(r.log.includes('relay captions on'));   // the relay gets the on-device captions the call asked for
  assert.equal(r.call.state, 'connecting');
});

test('hanging up while the ready check is out: no engine starts, and the call is idle', async () => {
  const r = rig('hang');
  const p = r.call.toggle({ route: '/' });
  assert.equal(r.call.state, 'connecting');          // a second tap now ends it, it does not start another
  r.call.end();
  r.release();
  await p;
  assert.equal(r.call.state, 'idle');
  assert.ok(!r.log.some((l) => l.includes(' start ')));
});

test('a muted caller stays muted when the call is handed to the relay', async () => {
  const r = rig({ ok: true });
  await r.call.toggle({ route: '/' });
  r.call.setMuted(true);
  r.native().onFallback?.('unavailable: native voice is switched off (HTTP 503)');
  await new Promise((res) => setTimeout(res, 0));
  assert.ok(r.log.includes('relay start /'));
  assert.ok(r.log.includes('relay muted true'));
  assert.equal(r.call.muted, true);
});
