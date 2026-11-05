// A call never writes into the composer's draft (not your spoken turns, not its end); at hang-up it is handed to the
// agent through the hand-off route. While muted, your layer of the waveform is flat.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { handOffCall } from './agentCalls.ts';

const src = (p: string) => readFileSync(new URL(`../${p}`, import.meta.url), 'utf8');

test('hang-up hands the call to the agent: POST /calls/handoff; 404 is "absent"; 502 retried once after 2 s', async () => {
  const calls: { url: string; body: unknown }[] = [];
  const answer = (...statuses: number[]) => (async (url: string, init?: RequestInit) => { calls.push({ url, body: JSON.parse(String(init!.body)) }); return new Response('{}', { status: statuses.shift() ?? 200 }); }) as typeof fetch;
  const waits: number[] = [];
  const wait = async (ms: number) => { waits.push(ms); };
  assert.equal(await handOffCall('dev-demo', 'vc_client_0123456789ab', answer(200), wait), 'ok');
  assert.deepEqual(calls[0], { url: '/api/agents/dev-demo/calls/handoff', body: { client_call_id: 'vc_client_0123456789ab' } });
  // an id that is not the browser's vc_client_<12 hex> is left out: the server matches the latest call
  calls.length = 0;
  await handOffCall('dev-demo', 'vc_sample', answer(200), wait);
  assert.deepEqual(calls[0].body, {});
  assert.equal(await handOffCall('dev-demo', 'vc_client_0123456789ab', answer(404), wait), 'absent');
  assert.equal(await handOffCall('dev-demo', 'vc_client_0123456789ab', answer(400), wait), 'failed');
  calls.length = 0;
  assert.equal(await handOffCall('dev-demo', 'vc_client_0123456789ab', answer(502, 200), wait), 'ok');
  assert.equal(calls.length, 2); assert.deepEqual(waits, [2000]);
  assert.equal(await handOffCall('dev-demo', 'vc_client_0123456789ab', answer(502, 502), wait), 'failed', 'then given up quietly');
  assert.equal(await handOffCall('dev-demo', 'vc_client_0123456789ab', (async () => { throw new Error('offline'); }) as typeof fetch, wait), 'failed');
});

test('nothing about a call goes into the draft; the dictation mic still does', () => {
  const vc = src('components/agent/VoiceControls.tsx');
  const listeners = vc.slice(vc.indexOf('const callListeners = {'), vc.indexOf('};', vc.indexOf('const callListeners = {')));
  assert.doesNotMatch(listeners, /onPartial|onFinal/);
  assert.match(listeners, /onCallEnded: \(_marker: string, callId: string\) => onCallEnded\?\.\(callId\)/);
  assert.match(vc, /onPartial: \(text, role\) => \{ if \(role === 'user'\) onPartial\(text\); \}/, 'dictation keeps writing into the box');
  const composer = src('components/agent/Composer.tsx');
  assert.match(composer, /onCallEnded=\{\(callId\) => \{ void handOffCall\(agentId, callId\)\.then\(\(r\) => \{ if \(r === 'ok'\) refreshTranscript\(agentId\); \}\); \}\}/);
  // no optimistic card: the thread is read again and the server's hand-off line is the card
  assert.match(src('components/chat/TranscriptChatView.tsx'), /\}, \[agentId, fixtureItems, refreshKey\]\);/);
  assert.doesNotMatch(composer, /\+ marker\)/, 'the old marker-into-draft is gone');
});

test('muted: your layer of the waveform reads no microphone (flat); the agent layer keeps its playback', () => {
  const wave = src('components/call/CallWaveform.tsx');
  assert.match(wave, /if \(micOffRef\.current\) mic = 0;\s*else \[mic, micBuf\] = read\(levels\.mic\(\), micBuf\);/);
  assert.match(wave, /\[play, playBuf\] = read\(levels\.playback\(\), playBuf\);/);
  const bar = src('components/call/CallBar.tsx');
  assert.equal((bar.match(/micOff=\{call\.muted\}/g) ?? []).length, 1);   // one bar; the phone's full-screen view is gone
});

test('the call bar is a chip at a phone\'s width everywhere; its transcript drops down, never full screen', () => {
  const bar = src('components/call/CallBar.tsx');
  assert.match(bar, /max-w-\[420px\]/);
  assert.doesNotMatch(bar, /data-call-full|fixed inset-0/);
  assert.match(bar, /\{open && <CallTranscript /);
});
