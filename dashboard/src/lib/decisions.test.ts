import { test } from 'node:test';
import assert from 'node:assert/strict';
import { interleave, outcomeLine, chosenText, previewText, menuAnswerLine, waitingByAgent, bellTarget, resolveBellJump, currentGenerationOnly, focusDecision, takeFocusRequest, focusTarget, approvalsFocusPath, decisionTone, deliveryText, type Decision } from './decisions.ts';
import { operatorUserId } from './runtimeConfig.ts';

const d = (over: Partial<Decision>): Decision => ({
  id: 'req-1', from_agent: 'pm-demo', kind: 'menu', state: 'pending', question: 'What now?', summary: null,
  options: [], menu: { options: [{ n: '1', label: 'Give me scope now' }, { n: '2', label: 'Other / Write-in...', input_kind: 'free_text' } as never] },
  risk_level: 'low', reversibility: 'easy', answer: null, answer_text: null, option_n: null, answered_by: null,
  answer_surface: null, answer_device: null, created_at: '2026-10-24T17:11:49Z', answered_at: null, expires_at: null, ...over,
});
const node = (key: string, ts: string) => ({ kind: 'assistant', text: key, ts, key }) as never;

const user = (key: string, ts: string) => ({ kind: 'user', text: key, ts, key }) as never;
const tools = (key: string, ts: string) => ({ kind: 'tool_group', key, children: [{ kind: 'tool', ts, key: key + 't' }] }) as never;
const keys = (out: ReturnType<typeof interleave>) => out.map((e) => (e.kind === 'node' ? (e.node as { key: string }).key : e.decision.id));

test('a card filed mid-turn goes under the reply that names it, not above the work that made it', () => {
  // A card placed by time alone sat above the work ("Worked for 17s") and the reply announcing it
  const nodes = [user('ask', '2026-10-24T20:01:00Z'), tools('work', '2026-10-24T20:01:20Z'),
                 node('Raised one — req-e1c8, at the top of your Approvals page now.', '2026-10-24T20:01:25Z'),
                 user('next', '2026-10-24T20:05:00Z')];
  const out = interleave(nodes, [d({ id: 'req-e1c8', created_at: '2026-10-24T20:01:10Z' })]);
  assert.deepEqual(keys(out).map((k) => k.slice(0, 10)), ['ask', 'work', 'Raised one', 'req-e1c8', 'next']);
});

test('a card no message names goes at the end of the turn it was filed in, or before the next message', () => {
  const nodes = [user('ask', '2026-10-24T20:01:00Z'), tools('work', '2026-10-24T20:01:20Z'), node('reply', '2026-10-24T20:01:25Z'),
                 user('next', '2026-10-24T20:05:00Z'), node('reply2', '2026-10-24T20:05:10Z')];
  assert.deepEqual(keys(interleave(nodes, [d({ id: 'mid', created_at: '2026-10-24T20:01:10Z' })])),
    ['ask', 'work', 'reply', 'mid', 'next', 'reply2']);
  assert.deepEqual(keys(interleave(nodes, [d({ id: 'between', created_at: '2026-10-24T20:03:00Z' })])),
    ['ask', 'work', 'reply', 'between', 'next', 'reply2']);
  assert.deepEqual(keys(interleave(nodes, [d({ id: 'late', created_at: '2026-10-24T21:00:00Z' })])).at(-1), 'late');
});

test('a long summary previews at a line break and never shows a stray ** or backtick', () => {
  const text = 'This is a test.\n\nWhat a real card has:\n- **The question** — one line.\n- **Why it matters** ' + 'x'.repeat(300);
  const p = previewText(text, 120);
  assert.ok(p.endsWith('…') && !p.includes('**Why'));
  assert.equal((p.match(/\*\*/g) ?? []).length % 2, 0);
  assert.equal(previewText('short'), 'short');
  const noBreak = previewText('word '.repeat(100) + '`code', 60);
  assert.equal((noBreak.match(/`/g) ?? []).length % 2, 0);
});

test('a preview cut inside a code block ends before the block, with no stray backticks', () => {
  const block = '```\n' + 'git -C ~/sample log --oneline   →  one commit\n'.repeat(8) + '```\n\nMore after the block.';
  const p = previewText('Intro line.\n\n## The gap\n\n' + block, 280);
  assert.equal(p, 'Intro line.\n\n## The gap…');
  // a block closed before the cut is kept whole
  const closed = previewText('Intro.\n\n```\nshort code\n```\n\n' + 'word '.repeat(80), 120);
  assert.ok(closed.includes('```\nshort code\n```') && closed.endsWith('…'));
  assert.equal((closed.match(/```/g) ?? []).length, 2);
});

test('an answered bubble says what was chosen, by whom, where and when', () => {
  const line = outcomeLine(d({ state: 'answered', option_n: '1', answered_by: operatorUserId(), answer_surface: 'web',
                               answered_at: '2026-10-24T17:20:00Z' }));
  assert.match(line, /^Give me scope now · answered by you · the dashboard · /);
  assert.equal(chosenText(d({ state: 'answered', option_n: '2', answer_text: 'park them' })), 'park them');
  assert.equal(outcomeLine(d({ state: 'withdrawn' })), 'Withdrawn by the agent');
  assert.equal(outcomeLine(d({ state: 'dismissed', answered_at: null })), 'Dismissed by you');
  assert.match(outcomeLine(d({ state: 'dismissed', answered_at: '2026-10-25T15:45:00Z' })), /^Dismissed by you · /);
  assert.match(outcomeLine(d({ state: 'expired', expires_at: '2026-10-25T17:11:49Z' })), /^Expired unanswered/);
});

test('menu answers read as a lasting line: dismissed, or the option chosen', () => {
  assert.equal(menuAnswerLine(d({ kind: 'menu_answer', answer: 'dismiss', question: 'Proceed with the deploy?' })), 'You dismissed: Proceed with the deploy?');
  assert.equal(menuAnswerLine(d({ kind: 'menu_answer', answer: 'answer', option_n: '1' })), 'Chose: 1 · Give me scope now');
  assert.equal(menuAnswerLine(d({ kind: 'menu_answer', answer: 'answer', option_n: '2', answer_text: 'do the small one' })), 'Chose: 2 — do the small one');
  assert.equal(menuAnswerLine(d({ kind: 'menu_answer', answer: 'dismiss', answered_by: 'gm', question: 'Q?' })), 'gm dismissed: Q?');
});

test('the bell lists agents with cards waiting, most first, pending only', () => {
  assert.deepEqual(waitingByAgent([
    { agent_id: 'gm', status: 'pending' }, { agent_id: 'pm-demo', status: 'pending' },
    { agent_id: 'pm-demo', status: 'pending' }, { agent_id: 'pm-demo', status: 'answered' }, { agent_id: null },
  ]), [{ agent: 'pm-demo', count: 2 }, { agent: 'gm', count: 1 }]);
});

// ── the bell jump: a card opens where it lives ──
const START = '2026-10-24T20:03:43.000Z';                         // the current session began here
const cur = { id: 'req-cur', agent_id: 'pm-demo', created_at: '2026-10-24T20:12:00Z', status: 'pending' };
const old = { id: 'req-old', agent_id: 'pm-demo', created_at: '2026-10-24T17:11:49Z', status: 'pending' };
const menuRow = { id: 'req-menu', agent_id: 'pm-demo', created_at: '2026-10-24T20:30:00Z', status: 'pending' };

test('bell: a current-generation card drawn in the chat opens the chat at that card', () => {
  assert.deepEqual(bellTarget([cur], 'pm-demo', new Set(['req-cur'])), { where: 'chat', id: 'req-cur' });
});
test('bell: a card from before the rotation goes to the Approvals page, and the chat does not draw it', () => {
  assert.deepEqual(bellTarget([old], 'pm-demo', new Set()), { where: 'approvals', id: 'req-old' });
  const drawn = currentGenerationOnly([d({ id: 'req-old', created_at: old.created_at }), d({ id: 'req-cur', created_at: cur.created_at })], START);
  assert.deepEqual(drawn.map((x) => x.id), ['req-cur']);
  // and so it is never pinned at the top of the successor's chat
  const out = interleave([node('hello', '2026-10-24T20:05:00Z')], drawn);
  assert.equal(out.some((e) => e.kind === 'decision' && e.decision.id === 'req-old'), false);
});
test('bell: an on-screen menu row (the chat never draws it as a card) goes to the Approvals page', () => {
  assert.deepEqual(bellTarget([menuRow], 'pm-demo', new Set(['req-cur'])), { where: 'approvals', id: 'req-menu' });
});
test('bell: several cards across both cases -> the OLDEST pending one, by the same rule', () => {
  assert.deepEqual(bellTarget([menuRow, cur, old], 'pm-demo', new Set(['req-cur'])), { where: 'approvals', id: 'req-old' });
  assert.deepEqual(bellTarget([menuRow, cur], 'pm-demo', new Set(['req-cur'])), { where: 'chat', id: 'req-cur' });
});
test('bell: nothing of the agent\'s still waiting -> null (the panel shows a notice)', async () => {
  assert.equal(bellTarget([{ ...cur, status: 'answered' }, { ...old, agent_id: 'gm' }], 'pm-demo', new Set()), null);
  assert.equal(await resolveBellJump('pm-demo', async () => [], async () => ['req-cur']), null);
});
test('bell: a failed read of the chat\'s cards sends the click to Approvals, where every card is drawn', async () => {
  assert.deepEqual(await resolveBellJump('pm-demo', async () => [cur], async () => { throw new Error('down'); }), { where: 'approvals', id: 'req-cur' });
});
test('bell: the Approvals jump names the card', () => {
  assert.equal(approvalsFocusPath('req-old'), '/approvals?focus=req-old');
});
test('a named focus request is taken once and shows that card; a missing one finds nothing', () => {
  focusDecision('pm-demo', 'req-cur');
  assert.deepEqual(takeFocusRequest('pm-demo'), { agentId: 'pm-demo', decisionId: 'req-cur' });
  assert.equal(takeFocusRequest('pm-demo'), null);
  const ds = [d({ id: 'req-a', state: 'answered' }), d({ id: 'req-cur' })];
  assert.equal(focusTarget(ds, 'req-cur')?.id, 'req-cur');
  assert.equal(focusTarget(ds, 'req-gone'), undefined);            // the view then shows its notice
  assert.equal(focusTarget(ds)?.id, 'req-cur');                    // "Needs you": first open one
});
test('no generation start known: every card is drawn, as before', () => {
  assert.equal(currentGenerationOnly([d({ id: 'x', created_at: '2020-01-01T00:00:00Z' })], null).length, 1);
});

// ── answered cards: outline by outcome, one compact row ──
test('answered cards: blue answered, green approved, red denied, dim blue held, grey only expired', () => {
  const at = '2026-10-27T04:00:00Z';
  assert.equal(decisionTone(d({ state: 'pending' })), 'pending');
  assert.equal(decisionTone(d({ state: 'answered', answer: 'option', option_n: '1', delivery: 'delivered', answered_at: at })), 'answered');
  assert.equal(decisionTone(d({ state: 'answered', answer: 'approve', delivery: 'delivered' })), 'approved');
  assert.equal(decisionTone(d({ state: 'answered', answer: 'deny', delivery: 'delivered' })), 'denied');
  assert.equal(decisionTone(d({ state: 'answered', answer: 'approve', delivery: 'held' })), 'held');
  assert.equal(decisionTone(d({ state: 'expired' })), 'expired');
  assert.equal(decisionTone(d({ state: 'withdrawn' })), 'closed');
  assert.equal(decisionTone(d({ state: 'dismissed' })), 'closed');
});
test('the outcome row: choice · who · where · when · delivery speed', () => {
  const x = d({ state: 'answered', answer: 'option', option_n: '1', answer_surface: 'web',
    answered_at: '2026-10-27T04:00:00.000Z', resumed_at: '2026-10-27T04:00:02.000Z', delivery: 'delivered' });
  const line = outcomeLine(x);
  assert.match(line, /^Give me scope now · answered by you · the dashboard · .+ · delivered in 2s$/);
  assert.equal(deliveryText({ ...x, resumed_at: '2026-10-27T04:00:00.000Z' }), 'delivered in <1s');
  assert.equal(deliveryText({ ...x, delivery: 'held' }), 'delivery held');
  assert.equal(deliveryText({ ...x, delivery: null }), '');
});
