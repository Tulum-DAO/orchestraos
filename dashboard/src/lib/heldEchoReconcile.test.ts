// Every case of the iOS HeldEchoReconcile grader, ported one for one, plus the web's pointer cases.
// (a) recency, (b) one-to-one and (c) anchored are the three that stop a bubble clearing early.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { unconfirmed, type Echo, type Row } from './heldEchoReconcile.ts';

const texts = (e: Echo[]) => e.map((x) => x.text);
const msg = 'Oh yeah, text me the command for bringing it to the demo user instead of root';

test('durable row arrives -> bubble stops rendering', () => {
  assert.deepEqual(texts(unconfirmed([{ text: msg }], [{ id: 'r1', text: msg }])), []);
});
test('row not here yet -> bubble KEEPS rendering', () => {
  assert.deepEqual(texts(unconfirmed([{ text: msg }], [{ id: 'r1', text: 'something else entirely' }])), [msg]);
});

test('(a) recency: an old identical row does NOT confirm', () => {
  const old: Row = { id: 'old-yes', text: 'yes' };
  const echo: Echo = { text: 'yes', priorRowIds: new Set(['old-yes']) };
  assert.deepEqual(texts(unconfirmed([echo], [old])), ['yes']);
});
test('(a) recency: its own newer row DOES confirm', () => {
  const echo: Echo = { text: 'yes', priorRowIds: new Set(['old-yes']) };
  assert.deepEqual(texts(unconfirmed([echo], [{ id: 'old-yes', text: 'yes' }, { id: 'new-yes', text: 'yes' }])), []);
});

test('(b) one-to-one: two identical bubbles, one row -> exactly ONE survives', () => {
  assert.equal(unconfirmed([{ text: 'ok' }, { text: 'ok' }], [{ id: 'r1', text: 'ok' }]).length, 1);
});
test('(b) one-to-one: two rows clear both', () => {
  assert.deepEqual(texts(unconfirmed([{ text: 'ok' }, { text: 'ok' }], [{ id: 'r1', text: 'ok' }, { id: 'r2', text: 'ok' }])), []);
});

test('(c) anchored: pending "ok" vs a newer row "look into it" -> STAYS', () => {
  assert.deepEqual(texts(unconfirmed([{ text: 'ok' }], [{ id: 'r9', text: 'look into it' }])), ['ok']);
});
test('(c) anchored: an exact newer row still confirms', () => {
  assert.deepEqual(texts(unconfirmed([{ text: 'ok' }], [{ id: 'r9', text: 'ok' }])), []);
});

test('guard: an empty transcript clears NOTHING', () => {
  assert.deepEqual(texts(unconfirmed([{ text: msg }], [])), [msg]);
});
test('guard: an assistant-looking row does not confirm', () => {
  assert.deepEqual(texts(unconfirmed([{ text: msg }], [{ id: 'r1', text: "I've texted you the message" }])), [msg]);
});
test('guard: a SHORTER chip-dodge row does not confirm (the idle backstop clears it)', () => {
  assert.deepEqual(texts(unconfirmed([{ text: msg }], [{ id: 'r1', text: 'Oh yeah, text me' }])), [msg]);
});

test('newlines folded on both sides', () => {
  const multi = 'line one\n\nline two with rather more text after it to clear sixty characters';
  const folded = 'line one line two with rather more text after it to clear sixty characters';
  assert.deepEqual(texts(unconfirmed([{ text: multi }], [{ id: 'r1', text: folded }])), []);
});
test('the row may carry MORE than was sent (markers appended)', () => {
  assert.deepEqual(texts(unconfirmed([{ text: msg }], [{ id: 'r1', text: `${msg} [attached: /tmp/x.jpg]` }])), []);
});

// ── web addition: the chip_dodge_pointer, checked first ──
const pointer = '/tmp/agent-inject-1791455840-9fc355e7.md';
const banner = `[LONG-MSG chip-dodge] A long message that was... — FULL TEXT (900 chars): read ${pointer}`;
test('pointer: a new row carrying the pointer confirms a long send at once', () => {
  assert.deepEqual(texts(unconfirmed([{ text: 'A long message that was sent as a file because it ran past the limit', pointer }], [{ id: 'r1', text: banner }])), []);
});
test('pointer: an OLD row carrying it does not (recency still applies)', () => {
  const e: Echo = { text: 'A long message', pointer, priorRowIds: new Set(['r1']) };
  assert.equal(unconfirmed([e], [{ id: 'r1', text: banner }]).length, 1);
});
test('pointer: no pointer on the bubble -> the banner does not confirm it (idle backstop)', () => {
  assert.equal(unconfirmed([{ text: 'A long message that was sent as a file because it ran past the limit' }], [{ id: 'r1', text: banner }]).length, 1);
});
test('pointer: the row spent by a pointer match cannot also confirm another bubble', () => {
  const left = unconfirmed([{ text: 'first', pointer }, { text: '[LONG-MSG chip-dodge] A long' }], [{ id: 'r1', text: banner }]);
  assert.deepEqual(texts(left), ['[LONG-MSG chip-dodge] A long']);
});
