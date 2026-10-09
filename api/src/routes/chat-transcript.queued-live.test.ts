/**
 * The operator, 2026-10-09 (phone + iPad screenshots): an agent message such as
 * "[DECISION ANSWERED apr_...]" rendered as a raw green bubble while the agent was working, and as
 * the amber "N queued messages processed" card only "after the fact, when we load an agent convo
 * again". It must be a card IMMEDIATELY.
 *
 * Measured on a live seat (apr_df7b8c6a, 10:30Z): the sender types the body into the pane; the log
 * writes a queued_command whose prompt IS the msg_store body; the row's batch_id -- which the card
 * (B2) needs -- is stamped only by the Stop-hook drain, ~70 s later at the end of the turn. A row the
 * agent acks before its drain runs never gets one, so it never became a card at all.
 *
 * B3 renders the card from the row as soon as the log proves the injection, and the existing
 * covered-by-batch drop removes the bubble on the same poll.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync } from 'fs';
import { join } from 'path';
import { tmpdir } from 'os';
import Database from 'better-sqlite3';

const DECISION = "[DECISION ANSWERED apr_df7b8c6a_41375323] 'Which blue replaces the IntentMagic purple?' -> None - show me more";
const ACKED = "[DECISION ANSWERED apr_00f04365_37087287] 'Sidebar logo morph: approve the staging result to go live?' -> Ship it";
const LATER = 'Please rebase the release branch onto main before the archive is cut tonight, thanks';

const dir = mkdtempSync(join(tmpdir(), 'qlive-'));
const DBP = join(dir, 'tasks.db');
const db = new Database(DBP);
db.exec(`CREATE TABLE messages (
  id TEXT PRIMARY KEY, type TEXT NOT NULL, from_agent TEXT NOT NULL, to_agent TEXT NOT NULL,
  body TEXT, status TEXT NOT NULL DEFAULT 'pending', metadata TEXT,
  created_at TEXT NOT NULL, delivered_at TEXT, acknowledged_at TEXT);`);
const ins = db.prepare(`INSERT INTO messages (id,type,from_agent,to_agent,body,status,metadata,created_at,acknowledged_at)
  VALUES (@id,@type,@from,@to,@body,@status,@md,@ts,@ack)`);
// live: injected, not yet drained (pending, no batch_id)
ins.run({ id: 'm1', type: 'approval_resolved', from: 'approval-loop', to: 'agent-a', body: DECISION, status: 'pending',
  md: '{"approval_id":"apr_df7b8c6a_41375323","writer_pane":"agent-a"}', ts: '2026-10-09T10:30:10.279013+00:00', ack: null });
// acked by the agent before any drain ran: no batch_id, ever
ins.run({ id: 'm2', type: 'approval_resolved', from: 'approval-loop', to: 'agent-b', body: ACKED, status: 'acknowledged',
  md: '{"approval_id":"apr_00f04365_37087287"}', ts: '2026-10-09T10:30:10.000000+00:00', ack: '2026-10-09T10:30:40.000000+00:00' });
// sent but NOT yet in the agent's log: must not be shown early
ins.run({ id: 'm3', type: 'task', from: 'pm-infra', to: 'agent-a', body: LATER, status: 'pending', md: null,
  ts: '2026-10-09T10:30:20.000000+00:00', ack: null });
// short body: too short to tie to a queued item
ins.run({ id: 'm4', type: 'task', from: 'pm-infra', to: 'agent-c', body: 'ok go', status: 'pending', md: null,
  ts: '2026-10-09T10:30:10.000000+00:00', ack: null });
// a held phone message (B1's lane), even if its text were in the log
ins.run({ id: 'm5', type: 'held_message', from: 'operator', to: 'agent-d', body: DECISION, status: 'pending', md: null,
  ts: '2026-10-09T10:30:10.000000+00:00', ack: null });
// AMBIGUITY guards (ios-watch-dev, from the menu-card binding scar): binding is by EQUALITY and
// only when unique on both sides.
const PREFIX = 'On it: building the orange variants for the sidebar logo now';
ins.run({ id: 'm6', type: 'reply', from: 'pm-x', to: 'agent-e', body: PREFIX, status: 'pending', md: null,
  ts: '2026-10-09T10:30:10.000000+00:00', ack: null });
const TWICE = '[DECISION ANSWERED apr_11111111_11111111] retry of the same card -> Ship it';
for (const id of ['m7', 'm8'])
  ins.run({ id, type: 'approval_resolved', from: 'approval-loop', to: 'agent-f', body: TWICE, status: 'pending', md: null,
    ts: `2026-10-09T10:30:1${id === 'm7' ? 0 : 1}.000000+00:00`, ack: null });
ins.run({ id: 'm9', type: 'approval_resolved', from: 'approval-loop', to: 'agent-g', body: DECISION, status: 'pending', md: null,
  ts: '2026-10-09T10:30:10.000000+00:00', ack: null });
// #327 review: (P4) the operator's OWN message that merely opens like an agent's must stay;
// (P3) a row the drain has stamped but not yet acked is in B2's gap and must still be a card.
const ALARM = 'pulse Level A: identity-invariant: gm tmux pane mismatch at 10:29Z (seat gm, pane %41)';
ins.run({ id: 'm10', type: 'status', from: 'identity-reconciler', to: 'agent-h', body: ALARM, status: 'pending', md: null,
  ts: '2026-10-09T10:30:09.000000+00:00', ack: null });
const STAMPED = '[APPROVAL RESOLVED apr_22222222_22222222] Deploy the staging build -> approved';
ins.run({ id: 'm11', type: 'approval_resolved', from: 'approval-loop', to: 'agent-i', body: STAMPED, status: 'pending',
  md: '{"batch_id":"BSTAMP","processed_ts":"2026-10-09T10:31:00+00:00"}', ts: '2026-10-09T10:30:10.000000+00:00', ack: null });
db.close();
process.env.MSG_DB_PATH = DBP;
const { normalizeTranscript } = await import('./chat-transcript.js');

const T = (s: number) => `2026-10-09T10:${String(30 + Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}.000Z`;
const assistantText = (uuid: string, s: number, text: string) =>
  JSON.stringify({ type: 'assistant', uuid, timestamp: T(s), message: { role: 'assistant', content: [{ type: 'text', text }] } });
const queuedCommand = (uuid: string, s: number, prompt: string) =>
  JSON.stringify({ type: 'attachment', uuid, timestamp: T(s),
    attachment: { type: 'queued_command', prompt, origin: { kind: 'human' }, commandMode: 'prompt' } });
const run = (agent: string, lines: string[]) => normalizeTranscript(lines, agent, 'sid', 500, false, true);
const cards = (items: any[]) => items.filter((i) => i.kind === 'queued_batch');
const bubbles = (items: any[], body: string) =>
  items.filter((i) => i.kind === 'text' && i.role === 'user' && String(i.text).includes(body.slice(0, 40)));
const midTurn = (body: string) => [
  assistantText('a1', 0, 'checking the blues'),
  queuedCommand('q1', 11, body),
  assistantText('a2', 12, 'got the answer, adjusting'),
];

test('a message injected mid-turn is a card on the FIRST poll, with its sender, and no raw bubble', () => {
  const env = run('agent-a', midTurn(DECISION));
  const c = cards(env.items);
  assert.equal(c.length, 1, 'exactly one card');
  assert.equal(c[0].key, 'msg:m1');
  assert.equal(c[0].count, 1);
  assert.deepEqual(c[0].entries[0], { agent: 'approval-loop', sent_ts: '2026-10-09T10:30:10.279013+00:00', body: DECISION });
  assert.equal(bubbles(env.items, DECISION).length, 0, 'the raw bubble is gone on the same poll');
  const at = env.items.findIndex((i) => i.kind === 'queued_batch');
  assert.ok(at > env.items.findIndex((i) => i.text === 'checking the blues') &&
            at < env.items.findIndex((i) => i.text === 'got the answer, adjusting'), 'the card sits where the message landed');
  assert.ok(env.render_items.some((r: any) => r.kind === 'queued_batch'), 'and it reaches render_items');
});

test('a message not yet in the log is never shown early', () => {
  const env = run('agent-a', [assistantText('a1', 0, 'checking the blues')]);
  assert.equal(cards(env.items).length, 0);
});

test('once the drain stamps the batch, B2 takes the row over: still ONE card, never a bubble', () => {
  const d = new Database(DBP);
  d.prepare(`UPDATE messages SET status='acknowledged', acknowledged_at='2026-10-09T10:31:24.650464+00:00',
    metadata='{"batch_id":"B1X","processed_ts":"2026-10-09T10:31:22+00:00"}' WHERE id='m1'`).run();
  d.close();
  try {
    const env = run('agent-a', midTurn(DECISION));
    const c = cards(env.items);
    assert.equal(c.length, 1, 'the live card is replaced by the batch, not doubled');
    assert.equal(c[0].key, 'batch:B1X');
    assert.equal(bubbles(env.items, DECISION).length, 0);
  } finally {
    const r = new Database(DBP);
    r.prepare(`UPDATE messages SET status='pending', acknowledged_at=NULL,
      metadata='{"approval_id":"apr_df7b8c6a_41375323","writer_pane":"agent-a"}' WHERE id='m1'`).run();
    r.close();
  }
});

test('a row the agent acked before its drain ran (no batch_id, ever) is still a card', () => {
  const env = run('agent-b', midTurn(ACKED));
  assert.deepEqual(cards(env.items).map((c) => c.key), ['msg:m2']);
  assert.equal(bubbles(env.items, ACKED).length, 0);
});

test('guards: a short body and a held phone message never become live cards', () => {
  const short = run('agent-c', [assistantText('a1', 0, 'x'), queuedCommand('q1', 11, 'ok go'), assistantText('a2', 12, 'y')]);
  assert.equal(cards(short.items).length, 0);
  assert.equal(short.items.filter((i) => i.kind === 'text' && i.text === 'ok go').length, 1, 'the bubble stays');
  const held = run('agent-d', midTurn(DECISION));
  assert.equal(cards(held.items).length, 0, 'held_message is B1, never B3');
});

test('ambiguity 1: a row whose body is only a PREFIX of the queued text never binds (equality, not contains)', () => {
  const env = run('agent-e', midTurn(PREFIX + ', and then the greens after lunch'));
  assert.equal(cards(env.items).length, 0, 'no card naming a sender who did not write this text');
});

test('ambiguity 2: two rows with ONE body (a re-send) bind to neither: no live card, the drain shows it later', () => {
  const env = run('agent-f', midTurn(TWICE));
  assert.equal(cards(env.items).length, 0);
  assert.equal(bubbles(env.items, TWICE).length, 1, 'the bubble stays until the drain batches it');
});

test('ambiguity 3: one body typed into the log TWICE binds to neither', () => {
  const env = run('agent-g', [assistantText('a1', 0, 'x'), queuedCommand('q1', 11, DECISION),
    queuedCommand('q2', 13, DECISION), assistantText('a2', 14, 'y')]);
  assert.equal(cards(env.items).length, 0);
});

test('the operator\'s own message that only OPENS like a live card\'s body is never hidden', () => {
  const mine = 'pulse Level A: identity-invariant: gm tmux pane mismatch -- is this the same one as yesterday? ignore it';
  const env = run('agent-h', [assistantText('a1', 0, 'x'), queuedCommand('q1', 11, ALARM),
    queuedCommand('q2', 13, mine), assistantText('a2', 14, 'y')]);
  assert.deepEqual(cards(env.items).map((c) => c.key), ['msg:m10']);
  assert.equal(env.items.filter((i) => i.kind === 'text' && i.text === mine).length, 1, 'the operator\'s bubble stays');
  assert.equal(env.items.filter((i) => i.kind === 'text' && i.text === ALARM).length, 0, 'the agent\'s copy is the card');
});

test('a row the drain stamped but has not acked (B2 does not render it yet) is still a live card', () => {
  const env = run('agent-i', midTurn(STAMPED));
  assert.deepEqual(cards(env.items).map((c) => c.key), ['msg:m11']);
  assert.equal(bubbles(env.items, STAMPED).length, 0);
});

test('a window with no real turn skips the live lane (no unbounded scan)', () => {
  const env = run('agent-a', [queuedCommand('q1', 11, DECISION)]);
  assert.equal(cards(env.items).length, 0);
});
