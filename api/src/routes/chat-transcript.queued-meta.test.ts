/**
 * Two transcript defects the operator reported from a phone (2026-10-08, screenshots):
 *
 * 1. "Queued badge never actually disappears after message ingestion."
 *    A message sent mid-turn is logged by Claude Code as an `attachment` of type
 *    `queued_command`. MEASURED on live logs: that line is WRITTEN AT INJECTION -- it
 *    lands after the tool_result it rides with, and the agent's thinking follows at
 *    once. So a queued_command that exists in the log has, by construction, ALREADY
 *    been ingested. It was shipped with queued:true forever, and the client contract
 *    for `queued` is "the server STILL holds this message" -> a chip that never clears.
 *
 * 2. "The message the agent displays after photos are ingested ... looks like messages
 *    that come from the user when it's not."
 *    The harness writes notes as type:'user' with `isMeta: true` -- the image-dimension
 *    note, stop-hook feedback, skill bodies, local-command caveats. Across the 60 most
 *    recent live transcripts NONE of them is human speech, yet every one rendered as a
 *    green bubble in the operator's voice.
 *
 * The guards matter as much as the fixes: a REAL pending message (msg_store held row,
 * not yet delivered) must keep its chip, the operator's real typed turns must still render,
 * and both existing dedupes (which find these items by their queued marker) must still
 * work, since the fix keeps that marker until they have run.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync } from 'fs';
import { join } from 'path';
import { tmpdir } from 'os';
import Database from 'better-sqlite3';

const dir = mkdtempSync(join(tmpdir(), 'qingest-'));
const DBP = join(dir, 'tasks.db');
{
  const db = new Database(DBP);
  db.exec(`CREATE TABLE messages (
    id TEXT PRIMARY KEY, type TEXT NOT NULL, from_agent TEXT NOT NULL, to_agent TEXT NOT NULL,
    body TEXT, status TEXT NOT NULL DEFAULT 'pending', metadata TEXT,
    created_at TEXT NOT NULL, delivered_at TEXT, acknowledged_at TEXT);`);
  // A message the gateway STILL holds (not delivered): its chip is TRUE and must stay.
  db.prepare(`INSERT INTO messages (id,type,from_agent,to_agent,body,status,created_at)
    VALUES ('h1','held_message','operator','agent-x','still waiting from the phone','pending','2026-10-08T06:40:00.000+00:00')`).run();
  // An AGENT's mid-turn message exists twice: this processed batch row, and the
  // queued_command the gateway typed into the pane. The batch must win, and the dedupe
  // that ensures it finds the copy by the queued marker -- which the fix strips, so it
  // must strip it only AFTER that dedupe has run.
  db.prepare(`INSERT INTO messages (id,type,from_agent,to_agent,body,status,metadata,created_at,delivered_at,acknowledged_at)
    VALUES ('b1','task','pm-infra','agent-z',
            'Please rebase the release branch onto main before the archive is cut tonight',
            'acknowledged', '{"batch_id":"BZ","processed_ts":"2026-10-08T06:31:30.000+00:00"}',
            '2026-10-08T06:31:00.000+00:00','2026-10-08T06:31:10.000+00:00','2026-10-08T06:31:30.000+00:00')`).run();
  db.close();
}
process.env.MSG_DB_PATH = DBP;
const { normalizeTranscript } = await import('./chat-transcript.js');

const T = (s: number) => `2026-10-08T06:${String(30 + s).padStart(2, '0')}:00.000+00:00`;
const assistantText = (uuid: string, s: number, text: string) =>
  JSON.stringify({ type: 'assistant', uuid, timestamp: T(s), message: { role: 'assistant', content: [{ type: 'text', text }] } });
const queuedCommand = (uuid: string, s: number, prompt: string) =>
  JSON.stringify({ type: 'attachment', uuid, timestamp: T(s),
    attachment: { type: 'queued_command', prompt, origin: { kind: 'human' }, commandMode: 'prompt' } });
const userTurn = (uuid: string, s: number, text: string, extra: Record<string, unknown> = {}) =>
  JSON.stringify({ type: 'user', uuid, timestamp: T(s), message: { role: 'user', content: text }, ...extra });

const userTexts = (items: any[]) => items.filter((i) => i.kind === 'text' && i.role === 'user');
// Only 'agent-x' has a held msg_store row. Every other test uses 'agent-y', so the
// pending synthetic cannot leak into a test that is not about it (it did, in the
// first run of this file, and made two GUARDS fail for a reason that was not theirs).
const run = (lines: string[], includeQueued = true, agent = 'agent-y') =>
  normalizeTranscript(lines, agent, 'sid', 500, false, includeQueued);

test('BUG 1: a mid-turn message present in the log is INGESTED -- it ships without queued:true', () => {
  const env = run([
    assistantText('a1', 0, 'working on it'),
    queuedCommand('q1', 1, 'And on the second beat, some agents are missing titles'),
    assistantText('a2', 2, 'got it, looking at the titles'),
  ]);
  const msg = userTexts(env.items).find((i) => i.text.startsWith('And on the second beat'));
  assert.ok(msg, 'the mid-turn message must still RENDER -- hiding it is the bug 7cd642ee70 fixed');
  assert.notEqual(msg.queued, true, 'queued:true means "the server still holds it"; this one is already in the agent');
});

test('BUG 1, render_items too: the client may read either envelope', () => {
  const env = run([queuedCommand('q1', 1, 'Problem'), assistantText('a2', 2, 'ok')]);
  const flat = JSON.stringify(env.render_items);
  assert.ok(flat.includes('Problem'), 'the message must still render in render_items');
  assert.ok(!/"queued":true/.test(flat), 'no ingested message may carry queued:true in render_items');
});

test('BUG 1, SSE path too (includeQueued=false is the stream tailer\'s call)', () => {
  const env = run([queuedCommand('q1', 1, 'sent while busy')], false);
  const msg = userTexts(env.items).find((i) => i.text === 'sent while busy');
  assert.ok(msg);
  assert.notEqual(msg.queued, true);
});

test('GUARD: a message the gateway STILL holds keeps its chip (msg_store held, undelivered)', () => {
  const env = run([assistantText('a1', 0, 'busy')], true, 'agent-x');
  const held = userTexts(env.items).find((i) => i.text === 'still waiting from the phone');
  assert.ok(held, 'the held message must render');
  assert.equal(held.queued, true, 'a genuinely pending message MUST keep queued:true -- this is the chip doing its job');
});

test('GUARD (review): a held message keeps its chip even with agent output AFTER it', () => {
  // The held row is 06:40. The agent keeps working its current turn at 06:45 and 06:50, so
  // "output follows it" must NOT be read as "ingested" -- only the queued_command marker is.
  const env = run([assistantText('a1', 0, 'busy'), assistantText('a2', 15, 'still on the same turn'),
                   assistantText('a3', 20, 'and still')], true, 'agent-x');
  const items = env.items;
  const heldIdx = items.findIndex((i) => i.kind === 'text' && i.role === 'user' && i.text === 'still waiting from the phone');
  assert.ok(heldIdx >= 0, 'the held message must render');
  assert.equal(items[heldIdx].queued, true, 'held + agent output after it: the chip must stay');
  assert.ok(items.slice(heldIdx + 1).some((i) => i.role === 'assistant'), 'precondition: agent output follows the held row');
  assert.ok(/"queued":true/.test(JSON.stringify(env.render_items)), 'render_items must carry the chip too');
  assert.ok(!JSON.stringify(env).includes('__fromQueuedCommand'), 'the internal marker never leaves the API');
});

test('BUG 3: an isMeta harness note is not rendered as the operator speaking', () => {
  const env = run([
    assistantText('a1', 0, 'reading the photo'),
    userTurn('m1', 1, '[Image: original 1320x2868, displayed at 921x2000. Multiply coordinates by 1.43 to map to original image.]', { isMeta: true }),
    userTurn('m2', 2, 'Stop hook feedback:\n[QUEUE-DIGEST] 1 pending message(s) for x older than 30s', { isMeta: true }),
    userTurn('m3', 3, 'Base directory for this skill: /home/user/.claude/skills/scrape', { isMeta: true }),
  ]);
  const texts = userTexts(env.items).map((i) => i.text);
  assert.deepEqual(texts, [], `harness notes rendered as operator speech: ${JSON.stringify(texts)}`);
});

test('GUARD: the operator\'s real typed turn (no isMeta) still renders', () => {
  const env = run([userTurn('u1', 0, 'Queued badge never actually disappears')]);
  assert.deepEqual(userTexts(env.items).map((i) => i.text), ['Queued badge never actually disappears']);
});

test('GUARD: isMeta:false is NOT treated as meta (only the explicit true flag hides a turn)', () => {
  const env = run([userTurn('u1', 0, 'a real message', { isMeta: false })]);
  assert.equal(userTexts(env.items).length, 1);
});

test('GUARD: dedupe still works -- a mid-turn message ALSO logged as a real turn shows ONCE', () => {
  const env = run([
    queuedCommand('q1', 1, 'same words twice'),
    userTurn('u1', 2, 'same words twice'),
  ]);
  assert.equal(userTexts(env.items).filter((i) => i.text === 'same words twice').length, 1);
});

test('GUARD: an agent\'s mid-turn message shows ONCE, as the batch -- the strip runs after the batch dedupe', () => {
  const body = 'Please rebase the release branch onto main before the archive is cut tonight';
  const env = run([
    assistantText('a1', 0, 'working'),
    queuedCommand('q9', 1, `[MSG from pm-infra] ${body}`),
    assistantText('a2', 2, 'ok, rebasing'),
  ], true, 'agent-z');
  const asText = userTexts(env.items).filter((i) => String(i.text).includes('rebase the release branch'));
  const batches = env.items.filter((i) => i.kind === 'queued_batch');
  assert.equal(batches.length, 1, 'the batch must render');
  assert.equal(asText.length, 0, `the typed copy must be dropped in favour of the batch, got ${asText.length}`);
});
