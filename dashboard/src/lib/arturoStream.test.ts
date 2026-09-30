/**
 * RED-first for the browser side of /text/stream: parse the SSE frames into callbacks.
 *
 * Written against frames the server actually emits (services/arturo/text_stream.py), and the
 * cases that make a naive parser wrong: a frame split across network chunks, several frames in
 * one chunk, and a stream that dies mid-turn.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseSseChunks, readStream, type ArturoStreamEvent } from './arturoStream.js';

function collect(chunks: string[]): ArturoStreamEvent[] {
  const out: ArturoStreamEvent[] = [];
  const feed = parseSseChunks((e) => out.push(e));
  for (const c of chunks) feed(c);
  return out;
}

const START = 'event: turn.start\ndata: {"turn_id":"t1","conversation_id":"c1"}\n\n';
const D1 = 'event: text.delta\ndata: {"turn_id":"t1","text":"On it, "}\n\n';
const D2 = 'event: text.delta\ndata: {"turn_id":"t1","text":"I\'ll text Shaw."}\n\n';
const END = 'event: turn.end\ndata: {"turn_id":"t1","reply_text":"On it, I\'ll text Shaw.","tools_called":[]}\n\n';

test('whole frames parse in order', () => {
  const evs = collect([START, D1, D2, END]);
  assert.deepEqual(evs.map((e) => e.event), ['turn.start', 'text.delta', 'text.delta', 'turn.end']);
});

test('a frame split across chunks is not lost or duplicated', () => {
  const joined = START + D1 + END;
  for (let i = 1; i < joined.length; i++) {
    const evs = collect([joined.slice(0, i), joined.slice(i)]);
    assert.deepEqual(evs.map((e) => e.event), ['turn.start', 'text.delta', 'turn.end'], `split at ${i}`);
  }
});

test('several frames arriving in ONE chunk all fire', () => {
  assert.equal(collect([START + D1 + D2 + END]).length, 4);
});

test('the deltas reassemble into the reply the server reports', () => {
  const evs = collect([START, D1, D2, END]);
  const streamed = evs.filter((e) => e.event === 'text.delta').map((e) => e.data.text).join('');
  const end = evs.find((e) => e.event === 'turn.end');
  assert.equal(streamed, end?.data.reply_text);
});

test('a comment keep-alive is ignored, not treated as an event', () => {
  assert.equal(collect([': ping\n\n', START]).length, 1);
});

test('a malformed data line is skipped rather than killing the turn', () => {
  const evs = collect([START, 'event: text.delta\ndata: {not json\n\n', D1, END]);
  assert.deepEqual(evs.map((e) => e.event), ['turn.start', 'text.delta', 'turn.end']);
});

test('an error frame is delivered like any other event', () => {
  const evs = collect(['event: error\ndata: {"code":"brain_failed","message":"CLI exited 1"}\n\n']);
  assert.equal(evs[0].event, 'error');
  assert.equal(evs[0].data.code, 'brain_failed');
});

test('a trailing partial frame is NOT emitted — half a turn is not a turn', () => {
  assert.equal(collect([START, 'event: text.delta\ndata: {"text":"half']).length, 1);
});

// --- the stream must never hang forever ------------------------------------------------
// Found live: a turn whose connection died mid-flight left the bubble spinning for TEN
// MINUTES while the answer sat finished on the server. The server sends a keep-alive every
// 10s, so silence past that is a dead stream, not a slow one.

test('a stream that goes silent past the idle limit gives up instead of spinning', async () => {
  const stalled = new ReadableStream<Uint8Array>({ start() { /* never enqueues, never closes */ } });
  const t0 = Date.now();
  const res = await readStream(stalled, () => {}, 120);
  assert.equal(res, 'stalled');
  assert.ok(Date.now() - t0 < 2000, 'it must give up promptly, not wait out the request');
});

test('a stream that keeps sending is never cut off by the idle limit', async () => {
  let n = 0;
  const chunks = new ReadableStream<Uint8Array>({
    async pull(c) {
      if (n >= 4) { c.close(); return; }
      await new Promise((r) => setTimeout(r, 60));   // steady traffic, under the limit
      c.enqueue(new TextEncoder().encode(`event: text.delta\ndata: {"text":"${n++}"}\n\n`));
    },
  });
  const seen: string[] = [];
  const res = await readStream(chunks, (s) => seen.push(s), 200);
  assert.equal(res, 'done');
  assert.equal(seen.join('').match(/text.delta/g)?.length, 4);
});

test('a keep-alive comment counts as traffic — it is the server saying it is alive', async () => {
  let sent = 0;
  const pinged = new ReadableStream<Uint8Array>({
    async pull(c) {
      if (sent >= 3) { c.close(); return; }
      await new Promise((r) => setTimeout(r, 60));
      sent += 1;
      c.enqueue(new TextEncoder().encode(': ping\n\n'));
    },
  });
  assert.equal(await readStream(pinged, () => {}, 200), 'done');
});
