import { test } from 'node:test';
import assert from 'node:assert/strict';
import { queuedCommandItems, dedupeQueuedCommands } from './chat-transcript.js';

/**
 * Mid-turn messages. Claude Code records a message sent while the agent is busy as
 * {type:'attachment', attachment:{type:'queued_command', ...}} rather than as a user turn,
 * so a renderer that reads only user turns never shows it.
 *
 * Measured on two real transcripts: quest-orchestra had 12 human prompts and NOT ONE ever
 * became a user turn (Shaw: "I don't see the most recent message that I sent you"). The same
 * file carried 1098 attachment rows in total — so what is admitted matters as much as what
 * is recovered.
 */

const att = (attachment: unknown, extra: Record<string, unknown> = {}) =>
  ({ type: 'attachment', attachment, timestamp: '2026-10-06T20:00:00Z', uuid: 'u1', ...extra });

test('a human mid-turn prompt becomes a user turn, marked queued', () => {
  const [it] = queuedCommandItems(att({ type: 'queued_command', prompt: 'hello there', origin: { kind: 'human' }, commandMode: 'prompt' }));
  assert.equal(it.kind, 'text');
  assert.equal(it.role, 'user');
  assert.equal(it.text, 'hello there');
  assert.equal(it.queued, true, 'the client needs to know it was sent while the agent was working');
  assert.equal(it.ts, '2026-10-06T20:00:00Z', 'it keeps its own timestamp, so it sorts in position');
});

test('a task-notification is NOT operator speech', () => {
  // The same shape carries the harness's own envelopes. Rendering them would put machine
  // chatter in the conversation as if Shaw typed it — the same lie as hiding his message,
  // pointed the other way.
  assert.deepEqual(queuedCommandItems(att({
    type: 'queued_command', prompt: '<task-notification>\n<task-id>abc</task-id>\n</task-notification>',
    origin: { kind: 'task-notification' }, commandMode: 'task-notification',
  })), []);
});

test('the empty queue-operation rows are rejected', () => {
  // 1086 of quest-orchestra's 1098 attachment rows were these: no origin, no prompt.
  assert.deepEqual(queuedCommandItems(att({ type: 'queued_command', prompt: '' })), []);
  assert.deepEqual(queuedCommandItems(att({ type: 'queued_command' })), []);

  // AND an empty prompt that IS human-origin, which is the only fixture that actually
  // exercises the empty guard. The bare rows above are already rejected for having no
  // origin, so a mutation deleting the guard left them passing: this assertion exists
  // because that mutation went green and the test was vacuous without it.
  assert.deepEqual(queuedCommandItems(att({
    type: 'queued_command', prompt: '   ', origin: { kind: 'human' }, commandMode: 'prompt',
  })), [], 'a human row with no text must not render an empty bubble');
  assert.deepEqual(queuedCommandItems(att({
    type: 'queued_command', origin: { kind: 'human' }, commandMode: 'prompt',
  })), []);
});

test('an UNKNOWN origin kind is rejected — this is an allowlist', () => {
  // A denylist would admit whatever origin.kind is added next, which is how machine text
  // ends up in the conversation months later with nobody having decided it should.
  for (const kind of ['hook', 'system', 'automation', 'future-thing']) {
    assert.deepEqual(queuedCommandItems(att({
      type: 'queued_command', prompt: 'x', origin: { kind }, commandMode: 'prompt',
    })), [], kind);
  }
});

test('a build with no `origin` falls back to commandMode', () => {
  const [it] = queuedCommandItems(att({ type: 'queued_command', prompt: 'legacy', commandMode: 'prompt' }));
  assert.equal(it?.text, 'legacy');
  // ...but only for `prompt`. An unlabelled notification stays out.
  assert.deepEqual(queuedCommandItems(att({ type: 'queued_command', prompt: 'x', commandMode: 'task-notification' })), []);
});

test('a non-queued_command attachment is ignored', () => {
  assert.deepEqual(queuedCommandItems(att({ type: 'image', prompt: 'x', origin: { kind: 'human' } })), []);
  assert.deepEqual(queuedCommandItems({ type: 'attachment' }), []);
  assert.deepEqual(queuedCommandItems(null), []);
});

test('dedupe drops a queued message the log ALSO recorded as a real user turn', () => {
  const items = [
    { kind: 'text', role: 'user', text: 'do the thing', queued: true },
    { kind: 'text', role: 'user', text: 'do the thing' },
  ];
  const out = dedupeQueuedCommands(items);
  assert.equal(out.length, 1);
  assert.equal(out[0].queued, undefined, 'the REAL turn is the one that survives');
});

test('dedupe keeps a queued message that has no real counterpart', () => {
  // The control, and on real data this is the ONLY case that occurs: across both
  // transcripts, zero human prompts were also recorded as user turns. A dedupe that
  // dropped them would delete exactly the messages this change exists to recover.
  const items = [
    { kind: 'text', role: 'user', text: 'only queued', queued: true },
    { kind: 'text', role: 'user', text: 'something else' },
  ];
  assert.equal(dedupeQueuedCommands(items).length, 2);
});

test('dedupe does not confuse an ASSISTANT echo for a real user turn', () => {
  const items = [
    { kind: 'text', role: 'user', text: 'ship it', queued: true },
    { kind: 'text', role: 'assistant', text: 'ship it' },
  ];
  assert.equal(dedupeQueuedCommands(items).length, 2, 'an assistant repeating the words is not the operator saying them');
});
