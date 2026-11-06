/**
 * The reaction wake never shows in the chat (spec "Hiding it in chat-transcript"): stripped like the page line, kept
 * as an empty flagged turn (reaction_wake: true) so the dashboard can tell the agent's answer to it from an answer
 * to the operator's message, and never copied into an interrupted-turn line.
 *
 * Run: ORCHESTRA_CONFIG=../orchestra.example.toml npx tsx --test src/routes/chat-transcript.reaction.test.ts   (from api/)
 */
import { testTmpDir } from '../lib/testTmp.js';
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { join } from 'node:path';

const dir = testTmpDir('ct-reaction-');
process.env.ORCHESTRA_DIR = dir;
if (!process.env.ORCHESTRA_CONFIG) {
  process.env.ORCHESTRA_CONFIG = join(new URL('../../..', import.meta.url).pathname, 'orchestra.example.toml');
}
after(() => rmSync(dir, { recursive: true, force: true }));

const { stripPageContext, normalizeTranscript, readInterruptedTurns, markWakeReplies } = await import('./chat-transcript.js');
const OLD_WAKE = '<reaction from="user:sam" emoji="👍" on="Deployed the fix."/> You may reply, react (`orchestra react`), or do nothing.';
const { reactionWakeText } = await import('../lib/reaction-wake-text.js');

const WAKE = reactionWakeText({ user: 'sam', emoji: '👍', on: 'Deployed the fix.' });
const userLine = (uuid: string, text: string) => JSON.stringify({ type: 'user', uuid, timestamp: '2026-11-02T10:00:00Z', message: { role: 'user', content: text } });
const agentLine = (uuid: string, text: string) => JSON.stringify({ type: 'assistant', uuid, timestamp: '2026-11-02T10:00:02Z', message: { role: 'assistant', content: [{ type: 'text', text }] } });

test("1. the strip: the wake alone strips to nothing; a message that only contains one is untouched", () => {
  assert.equal(stripPageContext(WAKE), '');
  assert.equal(stripPageContext(`ok ${WAKE}`), `ok ${WAKE}`);
  assert.equal(stripPageContext('a <reaction> mention'), 'a <reaction> mention');
});

test('2. the gate: the wake turn is kept empty and flagged, never drawn; a real reply stays', () => {
  const env = normalizeTranscript([
    userLine('u1', 'Ship it'), agentLine('a1', 'Deployed the fix.'),
    userLine('u2', WAKE), agentLine('a2', 'Glad it works. Shall I tag the release?'),
  ], 'pm-demo', 'sample-session');
  const wake = env.items.find((i: any) => i.uuid === 'u2');
  assert.equal(wake.text, '');
  assert.equal(wake.reaction_wake, true);
  const ship = env.items.find((i: any) => i.uuid === 'u1');
  assert.equal(ship.reaction_wake, undefined);
  assert.deepEqual(env.render_items.filter((n: any) => n.kind === 'user').map((n: any) => n.text), ['Ship it']);
  assert.ok(env.render_items.some((n: any) => n.kind === 'assistant' && n.text === 'Glad it works. Shall I tag the release?'));
  assert.ok(env.items.some((i: any) => i.uuid === 'a2' && i.wake_reply === undefined));
});

test('3. the interrupted copy: a cut wake is not listed (and never matches another message)', () => {
  const seat = join(dir, 'state', 'headless', 'pm-demo');
  mkdirSync(seat, { recursive: true });
  writeFileSync(join(seat, 'interrupted.json'), JSON.stringify({ items: [
    { id: 'i1', text: WAKE }, { id: 'i2', text: 'real message' },
  ] }));
  assert.deepEqual(readInterruptedTurns('pm-demo', dir).map((t: any) => t.id), ['i2']);
});

const toolLine = (uuid: string) => JSON.stringify({ type: 'assistant', uuid, timestamp: '2026-11-02T10:00:03Z', message: { role: 'assistant', content: [{ type: 'tool_use', id: `t-${uuid}`, name: 'Bash', input: { command: 'orchestra react 👍' } }] } });
const shown = (env: any) => env.items.filter((i: any) => i.kind === 'text' && i.role === 'assistant').map((i: any) => i.text);
const drawn = (env: any) => env.render_items.filter((n: any) => n.kind === 'assistant').map((n: any) => n.text);

test('4. the agent says nothing to a wake: NO_REPLY and the "no reply" phrases are hidden (items and render_items)', () => {
  for (const said of ['NO_REPLY', ' NO_REPLY\n', '(no reply needed)', 'No response required.', 'no reply']) {
    const env = normalizeTranscript([
      userLine('u1', 'Ship it'), agentLine('a1', 'Deployed.'), userLine('u2', WAKE), agentLine('a2', said),
    ], 'pm-demo', 'sample-session');
    assert.deepEqual(shown(env), ['Deployed.'], said);
    assert.deepEqual(drawn(env), ['Deployed.'], said);
  }
});

test('5. an emoji written as the answer to a wake is hidden (it becomes a reaction), after tool calls too; old-note wakes too', () => {
  for (const w of [WAKE, OLD_WAKE]) {
    const env = normalizeTranscript([
      userLine('u1', 'Ship it'), agentLine('a1', 'Deployed.'), userLine('u2', w), toolLine('a2'), agentLine('a3', '👍'),
    ], 'pm-demo', 'sample-session');
    assert.deepEqual(shown(env), ['Deployed.']);
    assert.deepEqual(drawn(env), ['Deployed.']);
    assert.ok(env.items.some((i: any) => i.uuid === 'a2'), 'the tool call stays');
  }
});

test('6. only in a wake turn: NO_REPLY or an emoji answering a real message stays; the turn ends at the next real message', () => {
  const env = normalizeTranscript([
    userLine('u1', 'say NO_REPLY'), agentLine('a1', 'NO_REPLY'),
    userLine('u2', WAKE), agentLine('a2', 'NO_REPLY'),
    userLine('u3', 'thanks'), agentLine('a3', '👍'),
  ], 'pm-demo', 'sample-session');
  assert.deepEqual(shown(env), ['NO_REPLY', '👍']);
  assert.deepEqual(drawn(env), ['NO_REPLY', '👍']);
});

test('7. markWakeReplies marks (silent | emoji) without dropping: what chatItemsFor and the wake watcher read', () => {
  const items = [
    { kind: 'text', role: 'user', text: '', reaction_wake: true, uuid: 'w' },
    { kind: 'text', role: 'assistant', text: '🎉', uuid: 'x' },
    { kind: 'text', role: 'assistant', text: 'NO_REPLY', uuid: 'y' },
    { kind: 'text', role: 'assistant', text: 'Real words.', uuid: 'z' },
  ];
  const out = markWakeReplies(items);
  assert.deepEqual(out.map((i: any) => i.wake_reply), [undefined, 'emoji', 'silent', undefined]);
  assert.equal(items[1].hasOwnProperty('wake_reply'), false, 'copies, never the cached originals');
});
