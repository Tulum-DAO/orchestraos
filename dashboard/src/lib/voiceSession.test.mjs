/**
 * Pure-logic tests for B3 voice bridge helpers — PCM downsampler + partial/
 * final event routing. NO real mic/WS/hardware touched (per BUILD constraint).
 *   node --experimental-strip-types dashboard/src/lib/voiceSession.test.mjs
 */
import assert from 'node:assert';
import {
  downsampleBuffer,
  floatTo16BitPCM,
  pcmChunkForUplink,
  routeTranscriptEvent,
  buildVoiceCallMarker,
} from './voiceSession.ts';

// --- downsampleBuffer --------------------------------------------------
{
  const input = new Float32Array(48000); // 1s @ 48kHz
  for (let i = 0; i < input.length; i++) input[i] = Math.sin(i);
  const out = downsampleBuffer(input, 48000, 16000);
  assert.strictEqual(out.length, 16000, 'downsamples 48k->16k to 1/3 the samples');
  console.log('PASS: downsampleBuffer 48k->16k length');
}
{
  const input = new Float32Array(100);
  const out = downsampleBuffer(input, 16000, 16000);
  assert.strictEqual(out, input, 'no-op when rates match (identity, no copy)');
  console.log('PASS: downsampleBuffer identity at matching rates');
}
{
  assert.throws(() => downsampleBuffer(new Float32Array(10), 8000, 16000), /cannot upsample/);
  console.log('PASS: downsampleBuffer refuses to upsample');
}

// --- floatTo16BitPCM / pcmChunkForUplink --------------------------------
{
  const input = new Float32Array([0, 1, -1, 0.5, -0.5, 2, -2]); // incl. out-of-range clamp cases
  const out = floatTo16BitPCM(input);
  assert.strictEqual(out[0], 0);
  assert.strictEqual(out[1], 0x7fff);
  assert.strictEqual(out[2], -0x8000);
  assert.strictEqual(out[5], 0x7fff, 'clamps > 1 to max');
  assert.strictEqual(out[6], -0x8000, 'clamps < -1 to min');
  console.log('PASS: floatTo16BitPCM clamps and scales correctly');
}
{
  const input = new Float32Array(48000).fill(0.25);
  const chunk = pcmChunkForUplink(input, 48000);
  assert.strictEqual(chunk.length, 16000, 'end-to-end uplink chunk is 16kHz PCM16');
  assert.ok(chunk instanceof Int16Array);
  console.log('PASS: pcmChunkForUplink produces 16kHz Int16 frames');
}

// --- routeTranscriptEvent: sub-second, no-batching partial/final routing ---
{
  const partials = [];
  const finals = [];
  const cb = { onPartial: (t, r) => partials.push([t, r]), onFinal: (t, r) => finals.push([t, r]) };

  routeTranscriptEvent({ event: 'transcript', text: 'Hel', role: 'arturo', partial: true }, cb);
  routeTranscriptEvent({ event: 'transcript', text: 'Hello', role: 'arturo', partial: true }, cb);
  routeTranscriptEvent({ event: 'transcript', text: 'Hello there.', role: 'arturo' }, cb);

  assert.deepStrictEqual(partials, [['Hel', 'arturo'], ['Hello', 'arturo']], 'each partial forwarded immediately, unbatched');
  assert.deepStrictEqual(finals, [['Hello there.', 'arturo']]);
  console.log('PASS: routeTranscriptEvent forwards every partial + the final, unbatched');
}
{
  const partials = []; const finals = [];
  const cb = { onPartial: (t, r) => partials.push([t, r]), onFinal: (t, r) => finals.push([t, r]) };
  routeTranscriptEvent({ event: 'user_turn_log', text: 'so I was thinking' }, cb);
  routeTranscriptEvent({ event: 'user_turn', text: 'so I was thinking about the deploy' }, cb);
  assert.deepStrictEqual(partials, [['so I was thinking', 'user']]);
  assert.deepStrictEqual(finals, [['so I was thinking about the deploy', 'user']]);
  console.log('PASS: routeTranscriptEvent routes on-device user_turn(_log) as user partial/final');
}
{
  const cb = { onPartial: () => assert.fail('should not fire'), onFinal: () => assert.fail('should not fire') };
  routeTranscriptEvent({ event: 'turn_complete' }, cb);
  routeTranscriptEvent({ event: 'interrupted' }, cb);
  routeTranscriptEvent({ event: 'connected', session_id: 'vc_live_abc123' }, cb);
  console.log('PASS: routeTranscriptEvent ignores non-transcript control events');
}

// --- buildVoiceCallMarker: grammar exactly matches voiceCall.ts's MARKER_RE ---
{
  const marker = buildVoiceCallMarker('vc_live_abc123def456');
  assert.match(marker, /^\[voice-call:\s+vc_[A-Za-z0-9_-]{4,64}\s+\S+\]$/);
  console.log('PASS: buildVoiceCallMarker matches the shared [voice-call:] grammar');
}

console.log('\nAll voiceSession pure-logic tests passed.');
