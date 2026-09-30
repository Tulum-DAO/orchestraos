/**
 * RED-first for the browser side of /text/stream: parse the SSE frames into callbacks.
 *
 * Written against frames the server actually emits (services/arturo/text_stream.py), and the
 * cases that make a naive parser wrong: a frame split across network chunks, several frames in
 * one chunk, and a stream that dies mid-turn.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseSseChunks, type ArturoStreamEvent } from './arturoStream.js';

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
