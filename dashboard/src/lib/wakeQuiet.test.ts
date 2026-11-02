/**
 * The agent answering a reaction wake writes NO_REPLY, a "no reply" phrase or one emoji, which the server then hides:
 * while its partial text could still become one of those, nothing shows as typing or as a live bubble.
 *
 * Run: npx tsx --test src/lib/wakeQuiet.test.ts   (from dashboard/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { couldBeQuiet, quietAnswer, answeringWake } from './wakeQuiet.ts';
import { liveTextOf } from './transcriptStream.ts';

const cases = JSON.parse(readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..', 'contract', 'wake-reply-cases.json'), 'utf8'));

test('the shared final cases classify exactly as the API silentReply', () => {
  for (const [text, want] of cases.final as [string, string | null][]) assert.equal(quietAnswer(text), want, JSON.stringify(text));
});

test('the shared partial cases', () => {
  for (const [text, want] of cases.partials as [string, boolean][]) assert.equal(couldBeQuiet(text), want, JSON.stringify(text));
});

test('the named ones: NO_R, NO_REPLY, 👍, (no rep are quiet; Glad is not', () => {
  for (const t of ['NO_R', 'NO_REPLY', '👍', '(no rep']) assert.equal(couldBeQuiet(t), true, t);
  assert.equal(couldBeQuiet('Glad'), false);
});

const wake = { kind: 'text', role: 'user', text: '', reaction_wake: true, uuid: 'w1' };
const msg = { kind: 'text', role: 'user', text: 'Is it done?', uuid: 'u1' };
const reply = { kind: 'text', role: 'assistant', text: 'Deployed.', uuid: 'a0' };
const tool = { kind: 'tool_use', tool: 'Bash', uuid: 't1' };
const result = { kind: 'tool_result', role: 'user', text: 'ok', uuid: 't2' };
const ev = (text: string) => ({ agent_id: 'x', text, blocks: [{ uuid: 'live1', text }] }) as never;

test('answeringWake: the newest user TEXT is a reaction wake (tool results do not count)', () => {
  assert.equal(answeringWake([msg, reply, wake]), true);
  assert.equal(answeringWake([msg, reply, wake, tool, result]), true);
  assert.equal(answeringWake([wake, msg]), false);
  assert.equal(answeringWake([]), false);
});

test('liveTextOf: answering a wake, quiet-so-far words are not live (no Typing, no live bubble); real words are', () => {
  for (const t of ['NO_R', 'NO_REPLY', '👍', '(no rep']) assert.equal(liveTextOf(ev(t), [msg, reply, wake] as never), null, t);
  assert.deepEqual(liveTextOf(ev('Glad it works'), [msg, reply, wake] as never), { text: 'Glad it works' });
});

test('liveTextOf: answering a normal message, a reply starting "No" (or NO_REPLY) still shows', () => {
  assert.deepEqual(liveTextOf(ev('No'), [reply, msg] as never), { text: 'No' });
  assert.deepEqual(liveTextOf(ev('NO_REPLY'), [wake, msg] as never), { text: 'NO_REPLY' });
  assert.deepEqual(liveTextOf(ev('👍'), [msg] as never), { text: '👍' });
});
