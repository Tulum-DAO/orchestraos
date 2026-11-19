import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  START_FRAME, DISCONNECT_FRAME, NATIVE_FRAME_BYTES, pageForRoute, contextFrame, StreamDownsampler, PcmFramer,
  MicUplink, floatToInt16, int16ToLeBytes, leBytesToFloat, parseNativeFrame, emptyTranscript, reduceTranscript,
  closeAll, journalTurns, newToolCalls, argsSummary, chooseEngine, fetchNativeReady, shouldFallBack, closeMessage,
  type NativeFrame, type TranscriptEffect, type TranscriptState,
} from './nativeVoice.ts';

// Native GPT-Live in the browser (the iPhone app's engine through /api/voice/native). The wire is the native
// server owner's contract; these pin the pure half of the client against it.

test('control frames are the contract\'s exact JSON', () => {
  assert.deepEqual(JSON.parse(START_FRAME), { type: 'start', settle: 'fast' });
  assert.deepEqual(JSON.parse(DISCONNECT_FRAME), { type: 'disconnect' });
});

test('context: only the five pages the server keeps, an agent id only when it is a valid one, never a device', () => {
  assert.deepEqual(pageForRoute('/'), { page: 'Arturo' });
  assert.deepEqual(pageForRoute('/approvals'), { page: 'Approvals' });
  assert.deepEqual(pageForRoute('/agents'), { page: 'Agents' });
  assert.deepEqual(pageForRoute('/agent/dev-3'), { page: 'Agents', agent: 'dev-3' });
  assert.deepEqual(pageForRoute('/agent/..%2Fetc'), { page: 'Agents' });
  assert.deepEqual(pageForRoute('/projects/x/files'), { page: 'Projects' });
  assert.deepEqual(pageForRoute('/pipeline'), { page: 'Pipeline' });
  assert.equal(pageForRoute('/settings'), null);
  assert.equal(contextFrame('/settings'), null);
  assert.deepEqual(JSON.parse(contextFrame('/agent/gm')!), { type: 'context', page: 'Agents', agent: 'gm' });
  assert.ok(!('device' in JSON.parse(contextFrame('/')!)));
});

test('downsampler: 48 kHz -> 16 kHz is exactly a third, with no drift across odd-sized chunks', () => {
  const ds = new StreamDownsampler(48000);
  let out = 0;
  for (let i = 0; i < 375; i++) out += ds.push(new Float32Array(128).fill(0.5)).length;   // 48000 samples = 1 s
  assert.ok(Math.abs(out - 16000) <= 1, `got ${out}`);
  const ds2 = new StreamDownsampler(44100);
  let n = 0;
  for (let i = 0; i < 441; i++) n += ds2.push(new Float32Array(100)).length;             // 44100 samples
  assert.ok(Math.abs(n - 16000) <= 1, `44.1k: got ${n}`);
  assert.deepEqual([...new StreamDownsampler(48000).push(Float32Array.from([0.3, 0.3, 0.3]))].map((x) => +x.toFixed(3)), [0.3]);
  assert.throws(() => new StreamDownsampler(8000));
});

test('framer: exact 3200-byte little-endian frames; the remainder waits', () => {
  const f = new PcmFramer();
  assert.equal(f.push(new Int16Array(1000)).length, 0);
  const frames = f.push(Int16Array.from({ length: 2300 }, (_, i) => (i === 600 ? 0x0102 : 0)));
  assert.equal(frames.length, 2);
  for (const fr of frames) assert.equal(fr.byteLength, NATIVE_FRAME_BYTES);
  // Index 600 of the second push is stream sample 1600: the first sample of frame 1, little-endian.
  assert.ok(new Uint8Array(frames[0]).every((x) => x === 0));
  assert.equal(new Uint8Array(frames[1])[0], 0x02);
  assert.equal(new Uint8Array(frames[1])[1], 0x01);
});

test('mute keeps the stream: the same number of frames, all zero', () => {
  const live = new MicUplink(16000);
  const muted = new MicUplink(16000);
  const loud = new Float32Array(3200).fill(0.9);
  const a = live.push(loud, false);
  const z = muted.push(loud, true);
  assert.equal(a.length, 2);
  assert.equal(z.length, 2);
  assert.ok(new Uint8Array(a[0]).some((x) => x !== 0));
  assert.ok(new Uint8Array(z[0]).every((x) => x === 0));
  assert.equal(z[0].byteLength, NATIVE_FRAME_BYTES);
});

test('PCM round trip: Int16 -> LE bytes -> float', () => {
  const s = floatToInt16(Float32Array.from([0, 0.5, -1, 1]));
  const back = leBytesToFloat(int16ToLeBytes(s));
  assert.deepEqual([...back].map((x) => +x.toFixed(2)), [0, 0.5, -1, 1]);
  assert.equal(leBytesToFloat(new ArrayBuffer(3)).length, 1);
});

test('parse: known frames typed, junk and unknown types ignored', () => {
  assert.equal(parseNativeFrame('not json'), null);
  assert.equal(parseNativeFrame('{"type":"later_thing"}'), null);
  assert.deepEqual(parseNativeFrame('{"type":"barge","cut":false}'), { type: 'barge', cut: false });
  assert.deepEqual(parseNativeFrame('{"type":"tick","elapsed_s":3,"cost":{"spent_usd":0.01}}'), { type: 'tick', elapsed_s: 3, spent_usd: 0.01 });
  assert.equal((parseNativeFrame('{"type":"state","conversation_id":"native-0123456789ab"}') as { conversation_id?: string }).conversation_id, 'native-0123456789ab');
  assert.deepEqual(parseNativeFrame('{"type":"refused","reason":"Not now"}'), { type: 'refused', reason: 'Not now' });
  assert.deepEqual(parseNativeFrame('{"type":"notice","text":"I can\'t hear you","code":"no_audio"}'), { type: 'notice', text: "I can't hear you", code: 'no_audio' });
  assert.equal(parseNativeFrame('{"type":"transcript","who":"bot","text":"x","new":true}'), null);
});

function run(frames: NativeFrame[]): { state: TranscriptState; fx: TranscriptEffect[] } {
  let s = emptyTranscript();
  const fx: TranscriptEffect[] = [];
  let t = 1000;
  for (const f of frames) { const r = reduceTranscript(s, f, t += 1000); s = r.state; fx.push(...r.effects); }
  return { state: s, fx };
}
const tr = (who: 'user' | 'arturo', text: string, isNew: boolean, withdrawn = false): NativeFrame =>
  ({ type: 'transcript', who, text, new: isNew, ...(withdrawn ? { withdrawn: true } : {}) });

test('transcript: whole-utterance text replaces the open row; new:true starts a row', () => {
  const { state, fx } = run([tr('user', 'hel', true), tr('user', 'hello there', false)]);
  assert.equal(state.rows.length, 1);
  assert.equal(state.rows[0].text, 'hello there');
  assert.deepEqual(fx, [{ kind: 'partial', who: 'user', text: 'hel' }, { kind: 'partial', who: 'user', text: 'hello there' }]);
});

test('transcript: Arturo starting commits the caller\'s line; audio_end commits Arturo\'s; order is as spoken', () => {
  const { state, fx } = run([tr('user', 'what time is it', true), tr('arturo', 'It is', true), tr('arturo', 'It is noon.', false),
    { type: 'audio_end' }, tr('user', 'thanks', true)]);
  const finals = fx.filter((e) => e.kind === 'final');
  assert.deepEqual(finals, [{ kind: 'final', who: 'user', text: 'what time is it' }, { kind: 'final', who: 'arturo', text: 'It is noon.' }]);
  const end = closeAll(state);
  assert.deepEqual(end.effects, [{ kind: 'final', who: 'user', text: 'thanks' }]);
  assert.deepEqual(journalTurns(end.state).map((t) => [t.role, t.text]),
    [['user', 'what time is it'], ['arturo', 'It is noon.'], ['user', 'thanks']]);
  assert.ok(journalTurns(end.state).every((t) => t.ts > 1 && t.ts < 100));   // epoch SECONDS (ms / 1000)
});

test('transcript: withdrawn removes Arturo\'s open row (caption cleared, never journaled)', () => {
  const { state, fx } = run([tr('user', 'stop', true), tr('arturo', 'Sure, I will', true), tr('arturo', '', false, true)]);
  assert.deepEqual(state.rows.map((r) => r.who), ['user']);
  assert.deepEqual(fx.at(-1), { kind: 'partial', who: 'arturo', text: '' });
  assert.deepEqual(journalTurns(closeAll(state).state).map((t) => t.text), ['stop']);
});

test('transcript: a late update after the row closed corrects the journal without a second UI turn', () => {
  const { state, fx } = run([tr('user', 'book a', true), tr('arturo', 'Booking', true), tr('user', 'book a table', false)]);
  assert.equal(fx.filter((e) => e.kind === 'final' && e.who === 'user').length, 1);
  assert.equal(state.rows[0].text, 'book a table');
  assert.equal(state.open.user, null);
});

test('tools: the cumulative snapshot yields each call once, in order', () => {
  const seen = new Set<string>();
  const snap = (ids: string[]) => ({ type: 'tools' as const, calls: ids.map((id) => ({ call_id: id, name: 'n', ok: true })), refused: [], claims_blocked: [] });
  assert.deepEqual(newToolCalls(seen, snap(['a'])).map((c) => c.call_id), ['a']);
  assert.deepEqual(newToolCalls(seen, snap(['a', 'b', 'c'])).map((c) => c.call_id), ['b', 'c']);
  assert.deepEqual(newToolCalls(seen, snap(['a', 'b', 'c'])), []);
  assert.equal(argsSummary({ q: 'x'.repeat(200) })!.length, 80);
  assert.equal(argsSummary({}), undefined);
});

test('engine: native only when ready says ok, and never for an agent\'s call', () => {
  assert.equal(chooseEngine({ ok: true }), 'native');
  assert.equal(chooseEngine({ ok: false }), 'relay');
  assert.equal(chooseEngine(null), 'relay');
  assert.equal(chooseEngine({ ok: true }, 'dev3'), 'relay');
});

test('fetchNativeReady: ok, not ok with reason, and no usable answer', async () => {
  const f = (status: number, body: unknown) => (async () => new Response(JSON.stringify(body), { status })) as unknown as typeof fetch;
  assert.deepEqual(await fetchNativeReady(f(200, { ok: true })), { ok: true });
  assert.deepEqual(await fetchNativeReady(f(200, { ok: false, reason: 'off' })), { ok: false, reason: 'off' });
  assert.equal(await fetchNativeReady(f(404, {})), null);
  assert.equal(await fetchNativeReady(f(200, { nope: 1 })), null);
  assert.equal(await fetchNativeReady((async () => { throw new Error('down'); }) as unknown as typeof fetch), null);
});

test('fallback only for native down / off before the call went live', () => {
  assert.equal(shouldFallBack(4502, false), true);
  assert.equal(shouldFallBack(4503, false), true);
  assert.equal(shouldFallBack(4503, true), false);
  assert.equal(shouldFallBack(4409, false), false);
  assert.equal(shouldFallBack(1011, false), false);
});

test('close wording: ordinary ends say nothing; the rest say what happened', () => {
  assert.equal(closeMessage(1000, 'stopped', false), null);
  assert.equal(closeMessage(1011, '', true), null);
  assert.match(closeMessage(1000, 'idle', false)!, /two minutes/);
  assert.match(closeMessage(1001, 'gateway restart', false)!, /restarted/);
  assert.match(closeMessage(1011, 'x', false)!, /dropped/);
  assert.equal(closeMessage(4409, 'busy: a call is already open', false), 'busy: a call is already open');
  assert.match(closeMessage(4403, 'refused: ...', false)!, /refused this server/);
  assert.match(closeMessage(4418, '', false)!, /HTTP 418/);
});

test('e2e 2026-11-19: Arturo\'s "." after the caller cut in never overwrites "Checking" (exact frame sequence)', () => {
  // From e2e/console.log, in order: the native server resets Arturo's running text when the caller speaks, so after
  // audio_end closed "Checking" his next chunk came new:false holding only ".".
  const { state, fx } = run([
    tr('arturo', 'Hey,', true),
    tr('user', 'Hey', true), tr('user', 'Hey Arturo', false), { type: 'barge', cut: false },
    tr('user', 'Hey Arturo, what', false), tr('user', 'Hey Arturo, what time', false), tr('user', 'Hey Arturo, what time is', false),
    tr('arturo', 'Checking', true),
    { type: 'audio_end' },
    tr('user', 'it', true),
    tr('arturo', '.', false),
    tr('user', 'Please', true), { type: 'barge', cut: false }, tr('user', 'Please answer', false),
    tr('user', 'Please answer in', false), { type: 'barge', cut: false }, tr('user', 'Please answer in one', false),
  ]);
  const end = closeAll(state);
  assert.deepEqual(journalTurns(end.state).map((t) => [t.role, t.text]), [
    ['arturo', 'Hey,'], ['user', 'Hey Arturo, what time is'], ['arturo', 'Checking'], ['user', 'it'], ['user', 'Please answer in one'],
  ]);
  assert.ok(!fx.some((e) => e.text === '.'));
});

test('a new:false that does not extend the closed row is new speech: it opens a row of its own', () => {
  const { state, fx } = run([tr('arturo', 'Checking', true), { type: 'audio_end' }, tr('arturo', 'It is noon', false)]);
  assert.deepEqual(state.rows.map((r) => [r.text, r.closed]), [['Checking', true], ['It is noon', false]]);
  assert.deepEqual(fx.at(-1), { kind: 'partial', who: 'arturo', text: 'It is noon' });
  // ... and one that DOES extend it still only corrects the journal.
  const ext = run([tr('arturo', 'Checking', true), { type: 'audio_end' }, tr('arturo', 'Checking now.', false)]);
  assert.deepEqual(ext.state.rows.map((r) => r.text), ['Checking now.']);
});
