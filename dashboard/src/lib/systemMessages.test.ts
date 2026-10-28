// Same grammars and trimming rules as the iOS SystemMessages grader, so the web and the phone draw
// the same card for the same line.
import { serviceNoticeText, test } from 'node:test';
import assert from 'node:assert/strict';
import { parseSystemMessage, stripAck, machineKindOf, foldCardAnswers, serviceNoticeText } from './systemMessages.ts';

const ACK = '. Act on it, then run: python3 /x/approval.py ack req-1 --from pm-demo';

test('decision: question, answer, note; the ack instruction trimmed; the card id kept', () => {
  const m = parseSystemMessage(`[DECISION ANSWERED req-1] 'Which plan?' -> Option A — note: start small${ACK}`, 'decision')!;
  assert.equal(m.headline, 'Decision received');
  assert.equal(m.subject, 'Which plan?');
  assert.equal(m.detail, 'Option A');
  assert.equal(m.note, 'start small');
  assert.equal(m.cardId, 'req-1');
});

test('decision without a note keeps the answer clean of the trailing period', () => {
  const m = parseSystemMessage(`[DECISION ANSWERED req-1] 'Ship it?' -> Yes${ACK}`)!;
  assert.equal(m.detail, 'Yes');
});

test('the ack trim stops at the end of its own line: prose after it is never lost', () => {
  assert.equal(stripAck(`answer${ACK}\n\nAlso: please rebuild the site.`), 'answer\n\nAlso: please rebuild the site.');
});

test('multi-part decision reads as a questionnaire', () => {
  const m = parseSystemMessage(`[MULTI-PART DECISION ANSWERED req-2] 'Setup' -> [Surfaces: iOS, Web] [Priority: P0]${ACK}`)!;
  assert.equal(m.kind, 'questionnaire');
  assert.equal(m.headline, 'Questionnaire received');
  assert.equal(m.detail, '[Surfaces: iOS, Web] [Priority: P0]');
});

test('approval resolved: approved and denied carry their outcome for the colour', () => {
  const a = parseSystemMessage(`[APPROVAL RESOLVED req-3] Your request 'Deploy now' was answered: approve (APPROVE)${ACK}`)!;
  assert.equal(a.outcome, 'approve');
  assert.equal(a.subject, 'Deploy now');
  const d = parseSystemMessage(`[APPROVAL RESOLVED req-4] Your request 'Drop table' was answered: deny (DENY) — note: not today${ACK}`)!;
  assert.equal(d.outcome, 'deny');
  assert.equal(d.note, 'not today');
});

test('human task: done, or could not', () => {
  assert.equal(parseSystemMessage("[HUMAN-TASK req-5] the operator completed: Sign the form. Act, then run: x")!.outcome, 'done');
  const c = parseSystemMessage("[HUMAN-TASK req-6] the operator couldn't: Call the bank. Act, then run: x")!;
  assert.equal(c.outcome, 'cant');
  assert.equal(c.subject, 'Call the bank');
});

test('questionnaire answers name the questionnaire', () => {
  const m = parseSystemMessage('[questionnaire-answers qnr-1] Client intake\n\n1. Name: Acme')!;
  assert.equal(m.kind, 'questionnaire');
  assert.equal(m.subject, 'Client intake');
  assert.equal(m.cardId, 'qnr-1');
});

test('relay: sender, subject, age; the agent\'s plumbing cut at the LAST " — Read "', () => {
  const m = parseSystemMessage('[MSG from dev-demo | medium] (sent 2m ago) BG FIRE — builder -> DRAINED — Read /tmp/agent-msg-a1.md and act on it. To reply: x')!;
  assert.equal(m.headline, 'Message from dev-demo');
  assert.equal(m.subject, 'BG FIRE — builder -> DRAINED');
  assert.equal(m.note, 'sent 2m ago');
  assert.equal(m.urgent, false);  assert.equal(m.sender, 'dev-demo');
  assert.equal(m.messageId, 'a1');
});

test('relay: high or urgent priority marks it urgent; stacked relay banners stripped', () => {
  const m = parseSystemMessage('[MSG from pm-demo | high] [SENT 5m AGO — world may have moved since; verify] [LOOP-SUSPECT depth=3] Status? — Read /tmp/agent-msg-b.md and act on it.')!;
  assert.equal(m.urgent, true);
  assert.equal(m.subject, 'Status?');
  assert.equal(m.note, 'sent 5m ago · possible reply loop');
});

test('relay: a subject that itself starts with a bracket is kept', () => {
  const m = parseSystemMessage('[MSG from ops-demo | medium] [disk-guard] root 90% used — Read /tmp/agent-msg-c.md and act on it.')!;
  assert.equal(m.subject, '[disk-guard] root 90% used');
});

test('task notification: summary and status; no summary -> null (drawn raw)', () => {
  const m = parseSystemMessage('<task-notification><status>failed</status><summary>Build broke</summary></task-notification>')!;
  assert.equal(m.headline, 'Background task failed');
  assert.equal(m.outcome, 'failed');
  assert.equal(parseSystemMessage('<task-notification><status>completed</status></task-notification>'), null);
});

test('the operator\'s own words are not a machine line', () => {
  assert.equal(machineKindOf('ship it'), null);
  assert.equal(parseSystemMessage('ship it'), null);
  assert.equal(parseSystemMessage('please look at [MSG from x] later'), null);
});

test('a card answer folds into its card when the chat draws that card; relays and other cards stay', () => {
  const nodes = [
    { kind: 'user', text: `[DECISION ANSWERED req-1] 'Q' -> A${ACK}`, machine: 'decision' },
    { kind: 'user', text: `[DECISION ANSWERED req-9] 'Old' -> B${ACK}`, machine: 'decision' },
    { kind: 'user', text: '[MSG from dev-demo | medium] mentions req-1 — Read /tmp/agent-msg-x.md and act on it.', machine: 'relay' },
    { kind: 'assistant', text: 'ok' },
  ];
  const out = foldCardAnswers(nodes, new Set(['req-1']));
  assert.deepEqual(out.map((n) => n.text.slice(0, 22)), ['[DECISION ANSWERED req', '[MSG from dev-demo | m', 'ok']);
  assert.ok(out[0].text.includes('req-9'));
});

test("the approvals service's copy of an answer folds into the card it names; other relays stay", () => {
  const nodes = [
    { kind: 'user', text: '[MSG from approval-loop | high] Approval apr_demo1_1 resolved — Read /tmp/agent-msg-m1.md and act on it.', machine: 'relay' },
    { kind: 'user', text: '[MSG from approval-loop | normal] Approval apr_demo2_2 discarded — Read /tmp/agent-msg-m2.md and act on it.', machine: 'relay' },
    { kind: 'user', text: '[MSG from dev-demo | high] Approval apr_demo1_1 looks wrong to me — Read /tmp/agent-msg-m3.md and act on it.', machine: 'relay' },
  ];
  const out = foldCardAnswers(nodes, new Set(['apr_demo1_1']));
  assert.deepEqual(out.map((n) => n.text.slice(10, 32)), ['approval-loop | normal', 'dev-demo | high] Appro']);
});

test("a service's line: what happened and to what, read from its body when it can be", () => {
  const relay = parseSystemMessage('[MSG from approval-loop | normal] Approval apr_demo2_2 discarded — Read /tmp/agent-msg-m2.md and act on it.')!;
  assert.equal(serviceNoticeText(relay, "[APPROVAL DISCARDED apr_demo2_2] Your request 'Sample question?' was DISMISSED without an answer."),
    'Request dismissed · Sample question?');
  assert.equal(serviceNoticeText(relay, "[APPROVAL RESOLVED apr_demo2_2] Your request 'Sample question?' was answered: approve (APPROVE). Act on it, then run: x"),
    'Request approved · Sample question?');
  assert.equal(serviceNoticeText(relay, null), 'approval-loop · Approval apr_demo2_2 discarded');
  const brief = parseSystemMessage('[MSG from orchestra | normal] [briefing] What your predecessor did — Read /tmp/agent-msg-m4.md and act on it.')!;
  assert.equal(serviceNoticeText(brief, 'Example briefing body.'), 'orchestra · [briefing] What your predecessor did');
});
