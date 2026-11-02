/**
 * The reaction wake never shows in the chat (spec "Hiding it in chat-transcript"): stripped like the page line, kept
 * as an empty flagged turn (reaction_wake: true) so the dashboard can tell the agent's answer to it from an answer
 * to the operator's message, and never copied into an interrupted-turn line.
 *
 * Run: ORCHESTRA_CONFIG=../orchestra.example.toml npx tsx --test src/routes/chat-transcript.reaction.test.ts   (from api/)
 */
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const dir = mkdtempSync(join(tmpdir(), 'ct-reaction-'));
process.env.ORCHESTRA_DIR = dir;
if (!process.env.ORCHESTRA_CONFIG) {
  process.env.ORCHESTRA_CONFIG = join(new URL('../../..', import.meta.url).pathname, 'orchestra.example.toml');
}
after(() => rmSync(dir, { recursive: true, force: true }));

const { stripPageContext, normalizeTranscript, readInterruptedTurns } = await import('./chat-transcript.js');
const { reactionWakeText } = await import('../lib/reaction-wake-text.js');

const WAKE = reactionWakeText({ user: 'sam', emoji: '👍', on: 'Deployed the fix.' });
const userLine = (uuid: string, text: string) => JSON.stringify({ type: 'user', uuid, timestamp: '2026-11-02T10:00:00Z', message: { role: 'user', content: text } });
const agentLine = (uuid: string, text: string) => JSON.stringify({ type: 'assistant', uuid, timestamp: '2026-11-02T10:00:02Z', message: { role: 'assistant', content: [{ type: 'text', text }] } });

test("1. the strip: the wake alone strips to nothing; a message that only contains one is untouched", () => {
  assert.equal(stripPageContext(WAKE), '');
  assert.equal(stripPageContext(`ok ${WAKE}`), `ok ${WAKE}`);
  assert.equal(stripPageContext('a <reaction> mention'), 'a <reaction> mention');
});

test('2. the gate: the wake turn is kept empty and flagged, never drawn; the reply stays', () => {
  const env = normalizeTranscript([
    userLine('u1', 'Ship it'), agentLine('a1', 'Deployed the fix.'),
    userLine('u2', WAKE), agentLine('a2', '🙏'),
  ], 'pm-demo', 'sample-session');
  const wake = env.items.find((i: any) => i.uuid === 'u2');
  assert.equal(wake.text, '');
  assert.equal(wake.reaction_wake, true);
  const ship = env.items.find((i: any) => i.uuid === 'u1');
  assert.equal(ship.reaction_wake, undefined);
  assert.deepEqual(env.render_items.filter((n: any) => n.kind === 'user').map((n: any) => n.text), ['Ship it']);
  assert.ok(env.render_items.some((n: any) => n.kind === 'assistant' && n.text === '🙏'));
});

test('3. the interrupted copy: a cut wake is not listed (and never matches another message)', () => {
  const seat = join(dir, 'state', 'headless', 'pm-demo');
  mkdirSync(seat, { recursive: true });
  writeFileSync(join(seat, 'interrupted.json'), JSON.stringify({ items: [
    { id: 'i1', text: WAKE }, { id: 'i2', text: 'real message' },
  ] }));
  assert.deepEqual(readInterruptedTurns('pm-demo', dir).map((t: any) => t.id), ['i2']);
});
