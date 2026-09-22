/**
 * Pure-logic tests for the zero-key dictation helper. A fake window.SpeechRecognition
 * stands in for Chrome; no real mic is touched.
 *   node --test dashboard/src/lib/dictation.test.mjs
 */
import assert from 'node:assert';
import { test } from 'node:test';
import { mergeDictation, routeRecognitionEvent, startDictation, dictationSupported, DICTATION_UNAVAILABLE } from './dictation.ts';

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
