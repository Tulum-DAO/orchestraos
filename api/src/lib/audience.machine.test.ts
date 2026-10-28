import { test } from 'node:test';
import assert from 'node:assert/strict';
import { machineKind, tagAudience, tagSynthetics, openerKind } from './audience.js';
import { dropBatchEntriesCoveredByMachineTurns } from '../routes/chat-transcript.js';

const decision = "[DECISION ANSWERED req-1] 'Which plan?' -> Option A. Act on it, then run: python3 approval.py ack req-1 --from pm-demo";

test('every machine grammar the emitters write gets its kind; the operator\'s words get none', () => {
  assert.equal(machineKind(decision), 'decision');
  assert.equal(machineKind("[MULTI-PART DECISION ANSWERED req-2] 'Q' -> [A: x]"), 'questionnaire');
  assert.equal(machineKind('[questionnaire-answers qnr-1] Intake'), 'questionnaire');
  assert.equal(machineKind("[APPROVAL RESOLVED req-3] Your request 'x' was answered: approve (APPROVE)."), 'approval');
  assert.equal(machineKind('[HUMAN-TASK req-4] the operator completed: sign'), 'human_task');
  assert.equal(machineKind('[MSG from dev-demo | high] (sent 2m ago) Build done — Read /tmp/agent-msg-x.md and act on it.'), 'relay');
  assert.equal(machineKind('<task-notification><summary>done</summary></task-notification>'), 'task');
  assert.equal(machineKind('Stop hook feedback: x'), 'system');
  assert.equal(machineKind('please answer [DECISION ANSWERED] later'), null);
  assert.equal(machineKind('ship it'), null);
});

test('a decision answer is a machine turn now (it was read as the operator\'s own message)', () => {
  assert.equal(openerKind({ kind: 'text', role: 'user', text: decision }), 'machine');
});

test('tagAudience and tagSynthetics stamp machine on user lines and batch entries, never on assistant lines', () => {
  const items: any[] = [
    { kind: 'text', role: 'user', text: 'hello', uuid: 'u1' },
    { kind: 'text', role: 'assistant', text: '[DECISION ANSWERED x] quoted by the agent' },
    { kind: 'text', role: 'user', text: decision, uuid: 'u2' },
  ];
  tagAudience(items);
  assert.equal(items[0].machine, undefined);
  assert.equal(items[1].machine, undefined);
  assert.equal(items[2].machine, 'decision');
  const syn: any[] = [{ kind: 'queued_batch', entries: [{ agent: 'approval-loop', sent_ts: 't', body: decision }] }];
  tagSynthetics(syn);
  assert.equal(syn[0].entries[0].machine, 'decision');
});

test('the duplicate: a decision answer typed into the pane AND stored as a batch renders once, as the turn', () => {
  const items: any[] = [
    { kind: 'text', role: 'user', text: decision, machine: 'decision', uuid: 'u2' },
    { kind: 'text', role: 'assistant', text: 'Got it.' },
    { kind: 'queued_batch', count: 1, key: 'msg:1', entries: [{ agent: 'approval-loop', sent_ts: 't', body: decision }] },
  ];
  const out = dropBatchEntriesCoveredByMachineTurns(items);
  assert.equal(out.length, 2);
  assert.equal(out.some((x) => x.kind === 'queued_batch'), false);
});

test('a batch keeps its other entries; the operator\'s own turns never cover a batch', () => {
  const other = 'A different stored message from another agent that is long enough';
  const items: any[] = [
    { kind: 'text', role: 'user', text: decision, machine: 'decision' },
    { kind: 'text', role: 'user', text: other },                       // operator-typed, untagged
    { kind: 'queued_batch', count: 2, key: 'msg:2', entries: [{ agent: 'a', sent_ts: 't', body: decision }, { agent: 'b', sent_ts: 't', body: other }] },
  ];
  const out = dropBatchEntriesCoveredByMachineTurns(items);
  const batch = out.find((x) => x.kind === 'queued_batch');
  assert.equal(batch.count, 1);
  assert.equal(batch.entries[0].body, other);
});

test('a short machine line is not used to match (not distinctive enough)', () => {
  const items: any[] = [
    { kind: 'text', role: 'user', text: '[HUMAN-TASK r] ok', machine: 'human_task' },
    { kind: 'queued_batch', count: 1, key: 'k', entries: [{ agent: 'a', sent_ts: 't', body: '[HUMAN-TASK r] ok' }] },
  ];
  assert.equal(dropBatchEntriesCoveredByMachineTurns(items).length, 2);
});
