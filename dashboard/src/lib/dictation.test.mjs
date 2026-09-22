/**
 * Pure-logic tests for the zero-key dictation helper. A fake window.SpeechRecognition
 * stands in for Chrome; no real mic is touched.
 *   node --test dashboard/src/lib/dictation.test.mjs
 */
import assert from 'node:assert';
import { test } from 'node:test';
import { mergeDictation, routeRecognitionEvent, startDictation, dictationSupported, DICTATION_UNAVAILABLE, pickRecordingMime, isTier1DeadError, recordingBlockedReason, transcribeBlob, transcribeReason } from './dictation.ts';

test('mergeDictation keeps typed text, appends finals, swaps the partial', () => {
  assert.strictEqual(mergeDictation('', [], ''), '');
  assert.strictEqual(mergeDictation('', [], 'hel'), 'hel');
  assert.strictEqual(mergeDictation('', [], 'hello wor'), 'hello wor');
  assert.strictEqual(mergeDictation('', ['hello world'], ''), 'hello world');
  assert.strictEqual(mergeDictation('', ['hello world'], 'how are'), 'hello world how are');
  assert.strictEqual(mergeDictation('typed first ', ['hello world'], ' how are you '), 'typed first hello world how are you');
});

test('routeRecognitionEvent sends interim to onPartial and final to onFinal, from resultIndex', () => {
  const seen = [];
  const ev = { resultIndex: 1, results: [
    [{ transcript: 'old' }], // before resultIndex — ignored
    Object.assign([{ transcript: 'hello' }], { isFinal: true }),
    Object.assign([{ transcript: 'wor' }], { isFinal: false }),
    Object.assign([{ transcript: '' }], { isFinal: false }),   // empty — skipped
  ] };
  routeRecognitionEvent(ev, { onPartial: (t) => seen.push(['p', t]), onFinal: (t) => seen.push(['f', t]) });
  assert.deepStrictEqual(seen, [['f', 'hello'], ['p', 'wor']]);
  routeRecognitionEvent({}, { onPartial: () => { throw new Error('must not fire'); } });
});

test('startDictation returns null without a recognizer and a stop handle with one', () => {
  const saved = globalThis.window;
  try {
    globalThis.window = {};
    assert.strictEqual(dictationSupported(), false);
    assert.strictEqual(startDictation({}), null);
    assert.match(DICTATION_UNAVAILABLE, /Chrome or Edge/);

    const log = [];
    class FakeRec extends EventTarget {
      constructor() { super(); FakeRec.last = this; }
      start() { log.push('start'); }
      stop() { log.push('stop'); this.onend?.(); }
      abort() {}
    }
    globalThis.window = { webkitSpeechRecognition: FakeRec };
    assert.strictEqual(dictationSupported(), true);
    const ended = [];
    const h = startDictation({ onPartial: (t) => log.push('p:' + t), onFinal: (t) => log.push('f:' + t), onEnd: () => ended.push(1), onError: (c) => log.push('e:' + c) });
    assert.ok(h);
    const rec = FakeRec.last;
    assert.strictEqual(rec.continuous, true);
    assert.strictEqual(rec.interimResults, true);
    assert.strictEqual(rec.lang, 'en-US');
    rec.onresult({ resultIndex: 0, results: [Object.assign([{ transcript: 'hi' }], { isFinal: false })] });
    rec.onresult({ resultIndex: 0, results: [Object.assign([{ transcript: 'hi there' }], { isFinal: true })] });
    rec.onerror({ error: 'not-allowed' });
    h.stop();
    assert.deepStrictEqual(log, ['start', 'p:hi', 'f:hi there', 'e:not-allowed', 'stop']);
    assert.strictEqual(ended.length, 1);
  } finally {
    globalThis.window = saved;
  }
});

// ---- item C: tier 2 helpers ----------------------------------------------------------------
test('pickRecordingMime prefers webm/opus, falls to mp4 on Safari-shaped support, empty when none', () => {
  assert.strictEqual(pickRecordingMime((t) => t.startsWith('audio/webm')), 'audio/webm;codecs=opus');
  assert.strictEqual(pickRecordingMime((t) => t === 'audio/mp4'), 'audio/mp4');
  assert.strictEqual(pickRecordingMime(() => false), '');
  assert.strictEqual(pickRecordingMime(() => { throw new Error('no isTypeSupported'); }), '');
});

test('isTier1DeadError names the codes that mean "API present, backend dead"', () => {
  assert.ok(isTier1DeadError('network') && isTier1DeadError('service-not-allowed'));
  assert.ok(!isTier1DeadError('not-allowed') && !isTier1DeadError('no-speech'));
});

test('recordingBlockedReason gates on secure context in front of tier 2 only', () => {
  assert.match(recordingBlockedReason({ isSecureContext: false }), /HTTPS/);
  const saved = { MediaRecorder: globalThis.MediaRecorder, navigator: globalThis.navigator };
  try {
    globalThis.MediaRecorder = class {};
    Object.defineProperty(globalThis, 'navigator', { value: { mediaDevices: { getUserMedia: async () => ({}) } }, configurable: true });
    assert.strictEqual(recordingBlockedReason({ isSecureContext: true }), null);
    Object.defineProperty(globalThis, 'navigator', { value: {}, configurable: true });
    assert.match(recordingBlockedReason({ isSecureContext: true }), /cannot record/);
  } finally {
    globalThis.MediaRecorder = saved.MediaRecorder;
    Object.defineProperty(globalThis, 'navigator', { value: saved.navigator, configurable: true });
  }
});

test('transcribeBlob posts multipart to /api/arturo/transcribe and transcribeReason names the fix', async () => {
  let seen = null;
  const fakeFetch = async (url, init) => { seen = { url, init }; return { ok: false, status: 503, json: async () => ({ ok: false, error: 'stt_unavailable', reason: 'not-installed', install: 'orchestra init --stt' }) }; };
  const r = await transcribeBlob(new Blob(['x'], { type: 'audio/webm' }), fakeFetch);
  assert.strictEqual(seen.url, '/api/arturo/transcribe');
  assert.strictEqual(seen.init.method, 'POST');
  assert.ok(seen.init.body instanceof FormData);
  assert.strictEqual(seen.init.body.get('audio').name, 'clip.webm');
  assert.strictEqual(r.ok, false);
  assert.match(transcribeReason(r), /orchestra init --stt/);
  assert.match(transcribeReason({ ok: false, status: 503, error: 'stt_unavailable', reason: 'warming' }), /downloading/);
  assert.match(transcribeReason({ ok: false, status: 422, error: 'no_speech' }), /did not catch/);
  assert.strictEqual(transcribeReason({ ok: true, status: 200, text: 'hi' }), '');
  const ok = await transcribeBlob(new Blob(['x'], { type: 'audio/mp4' }), async (u, i) => { seen = i; return { ok: true, status: 200, json: async () => ({ ok: true, text: 'hello', ms: 300 }) }; });
  assert.deepStrictEqual([ok.ok, ok.text, seen.body.get('audio').name], [true, 'hello', 'clip.mp4']);
});
