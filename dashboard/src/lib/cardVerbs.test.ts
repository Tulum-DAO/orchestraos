/**
 * A card that is not a question answers with its OWN verbs, the same on every surface (lib/cardVerbs.ts).
 *
 * The defect: a task for you (kind 'human_task', options done|cant|snooze) was drawn in the agent's chat with
 * Approve / Deny and "Reason, if you deny". Approve posted /approvals/unified/:id/approve, the gateway answered
 * 400 "human_task verbs are done|cant|snooze", and the card said "Could not send: 400".
 *
 * The real cards are rendered (react-dom/server), as in cardPopInCard.test.ts: a loader hook stands an empty module in
 * for every .css, and a verb button's onClick is read from the element as it is created. fetch is stubbed, so a click
 * shows exactly what would be posted.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { register } from 'node:module';
import * as React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { cardVerbs, verbFromPick, verbOptionLines, verbRequest, sendErrorText } from './cardVerbs.ts';

register('data:text/javascript,' + encodeURIComponent(
  "export async function load(url, ctx, next) { return url.endsWith('.css') ? { format: 'module', source: '', shortCircuit: true } : next(url, ctx); }"));

/** Every verb button's onClick, by its verb, as last rendered (data-card-verb on TaskAnswer's and ApprovalAnswer's buttons). */
let verbClicks: Record<string, () => void> = {};
const spy = (type: unknown, props: Record<string, unknown> | null, ...kids: unknown[]) => {
  const v = props?.['data-card-verb'];
  if (typeof v === 'string' && typeof props?.onClick === 'function') verbClicks[v] = props.onClick as () => void;
  return (React.createElement as (...a: unknown[]) => React.ReactElement)(type, props, ...kids);
};
(globalThis as { React?: unknown }).React = { ...React, createElement: spy };

const { default: PendingApprovalCard } = await import('../components/chat/PendingApprovalCard.tsx');
const { default: DecisionBubble } = await import('../components/chat/DecisionBubble.tsx');
type Item = Parameters<typeof PendingApprovalCard>[0]['item'];
type Dec = Parameters<typeof DecisionBubble>[0]['decision'];
const h = React.createElement;

/** One canonical row, as the Approvals page gets it (GET /api/approvals/unified) and as the chat gets it
 *  (GET /api/agents/:id/decisions): both carry the row's kind and its parsed options. */
interface Row { id: string; kind: string | null; options: string[]; question: string; menu?: Dec['menu'] }
const TASK: Row = { id: 'apr_task1', kind: 'human_task', options: ['done', 'cant', 'snooze'], question: 'Plug in the test phone' };
const PLAIN: Row = { id: 'apr_plain1', kind: null, options: ['approve', 'deny'], question: 'Deploy to staging?' };
const MENU: Row = { id: 'apr_menu1', kind: 'menu', options: ['Ship A', 'Ship B'], question: 'Which build?',
  menu: { question: 'Which build?', options: [{ n: '1', label: 'Ship A' }, { n: '2', label: 'Ship B' }] } };

const asItem = (r: Row): Item => ({ id: r.id, type: 'agent', urgency: 'normal', status: 'pending', title: r.question,
  description: '', risk_level: 'low', evidence_summary: '', qa_verdict: null, sandbox_result: null, onepager_url: null,
  created_at: '', updated_at: '', source: 'agent_request', kind: r.kind, options: r.options, menu: r.menu ?? null, agent_id: 'dev' });
const asDecision = (r: Row): Dec => ({ id: r.id, from_agent: 'dev', kind: r.kind, state: 'pending', question: r.question,
  summary: null, options: r.options, menu: r.menu ?? null, risk_level: 'low', reversibility: null, answer: null,
  answer_text: null, option_n: null, answered_by: null, answer_surface: null, answer_device: null,
  created_at: '2026-11-21T10:00:00Z', answered_at: null, expires_at: null });

const chat = (r: Row) => { verbClicks = {}; const html = renderToStaticMarkup(h(DecisionBubble, { decision: asDecision(r), onAnswered() {} })); return { html, clicks: { ...verbClicks } }; };
const approvals = (r: Row) => { verbClicks = {}; const html = renderToStaticMarkup(h(PendingApprovalCard, { item: asItem(r), onChanged() {} })); return { html, clicks: { ...verbClicks } }; };

/** The words on the card's buttons, in order, without its header ✕ or "more". */
const buttons = (html: string) => [...html.matchAll(/<button[^>]*>(?:<svg[^]*?<\/svg>)?([^<]*)<\/button>/g)]
  .map((m) => m[1].replace(/&#x27;/g, "'")).filter((w) => w && !/^(more|less)$/.test(w));

/** A click, with fetch stubbed: what was posted, and where. */
async function clickAndCapture(click: () => void, status = 200) {
  const sent: { url: string; body: unknown }[] = [];
  const saved = globalThis.fetch;
  globalThis.fetch = (async (url: string, init?: { body?: string }) => {
    sent.push({ url: String(url), body: init?.body ? JSON.parse(init.body) : undefined });
    return { ok: status < 400, status, json: async () => ({}) } as Response;
  }) as typeof fetch;
  try { click(); await new Promise((r) => setTimeout(r, 0)); } finally { globalThis.fetch = saved; }
  return sent;
}

test('a task for you takes Done / Can\'t / Snooze, never Approve / Deny', () => {
  const set = cardVerbs(TASK);
  assert.equal(set.mode, 'task');
  assert.deepEqual(set.verbs.map((v) => v.label), ['Done', "Can't", 'Snooze']);
  assert.deepEqual(set.verbs.map((v) => v.verb), ['done', 'cant', 'snooze']);
  // Its own options decide which (only the three the gateway takes, in the gateway's order); none means all three.
  assert.deepEqual(cardVerbs({ kind: 'human_task', options: ['snooze', 'DONE', 'approve'] }).verbs.map((v) => v.verb), ['snooze', 'done']);
  assert.deepEqual(cardVerbs({ kind: 'human_task', options: null }).verbs.map((v) => v.verb), ['done', 'cant', 'snooze']);
  assert.deepEqual(verbOptionLines(set), ['A. Done', "B. Can't", 'C. Snooze']);
});

test('a task\'s verbs post { answer } to the card\'s answer route; its words go as the note', () => {
  assert.deepEqual(verbRequest('apr_task1', 'done'), { path: '/approvals/unified/apr_task1/answer', body: { answer: 'done' } });
  assert.deepEqual(verbRequest('apr_task1', 'cant', '  no cable here '), { path: '/approvals/unified/apr_task1/answer', body: { answer: 'cant', answer_text: 'no cable here' } });
  assert.deepEqual(verbRequest('apr_task1', 'snooze', 'later'), { path: '/approvals/unified/apr_task1/answer', body: { answer: 'snooze' } });
  assert.deepEqual(verbRequest('apr/x', 'done'), { path: '/approvals/unified/apr%2Fx/answer', body: { answer: 'done' } });
  assert.ok('error' in verbRequest('apr_task1', 'hold'));
});

test('a plain approval keeps Approve / Deny, on their own routes; Deny needs a reason', () => {
  for (const kind of [null, 'approval', 'decision', undefined]) {
    const set = cardVerbs({ kind, options: ['approve', 'deny'] });
    assert.equal(set.mode, 'approval');
    assert.deepEqual(set.verbs.map((v) => v.label), ['Approve', 'Deny']);
  }
  assert.deepEqual(verbOptionLines(cardVerbs(PLAIN)), ['A. Approve', 'B. Deny (needs a reason)']);
  assert.deepEqual(verbRequest('apr_plain1', 'approve'), { path: '/approvals/unified/apr_plain1/approve', body: {} });
  assert.deepEqual(verbRequest('apr_plain1', 'deny', 'not today'), { path: '/approvals/unified/apr_plain1/deny', body: { reason: 'not today' } });
  assert.deepEqual(verbRequest('apr_plain1', 'deny', '  '), { error: 'Add a reason to deny.' });
});

test('an answer the assistant hands back names a verb by letter, verb or label', () => {
  const set = cardVerbs(TASK);
  for (const [said, verb] of [['b', 'cant'], ['B)', 'cant'], ["Can't", 'cant'], ['cant', 'cant'], ['A.', 'done'], ['snooze', 'snooze']]) {
    assert.equal(verbFromPick(set, said)?.verb, verb, said);
  }
  for (const said of ['approve', 'd', '', 'yes']) assert.equal(verbFromPick(set, said), null, said);
  assert.equal(sendErrorText(new Error('API /approvals/unified/apr_1/answer: 400')), 'Could not send: 400');
});

test('the agent\'s chat draws a task for you with Done / Can\'t / Snooze, and no deny reason field', () => {
  const { html } = chat(TASK);
  assert.deepEqual(buttons(html), ['Done', "Can't", 'Snooze']);
  assert.doesNotMatch(html, />Approve<|>Deny<|Reason, if you deny/);
});

test('Done in the chat posts { answer: "done" } to /answer, never /approve', async () => {
  const { clicks } = chat(TASK);
  assert.deepEqual(Object.keys(clicks), ['done', 'cant', 'snooze']);
  assert.deepEqual(await clickAndCapture(clicks.done), [{ url: '/api/approvals/unified/apr_task1/answer', body: { answer: 'done' } }]);
  assert.deepEqual(await clickAndCapture(clicks.snooze), [{ url: '/api/approvals/unified/apr_task1/answer', body: { answer: 'snooze' } }]);
  assert.deepEqual(await clickAndCapture(clicks.cant), [{ url: '/api/approvals/unified/apr_task1/answer', body: { answer: 'cant' } }]);
});

test('the inline chat card and the Approvals page draw the same verbs, and post the same, for the same card', async () => {
  for (const row of [TASK, PLAIN, { ...TASK, options: ['done', 'snooze'] }]) {
    const c = chat(row);
    const a = approvals(row);
    assert.deepEqual(buttons(c.html), buttons(a.html), `${row.id}: same buttons`);
    assert.deepEqual(buttons(c.html), cardVerbs(row).verbs.map((v) => v.label), `${row.id}: the card's own verbs`);
    assert.deepEqual(Object.keys(c.clicks), Object.keys(a.clicks), `${row.id}: same verb buttons`);
    for (const verb of Object.keys(c.clicks)) {
      assert.deepEqual(await clickAndCapture(c.clicks[verb]), await clickAndCapture(a.clicks[verb]), `${row.id} ${verb}: same post`);
    }
  }
  assert.deepEqual(buttons(chat(PLAIN).html), ['Approve', 'Deny']);
  // Approve on a plain approval still goes to /approve, from both surfaces.
  assert.deepEqual(await clickAndCapture(chat(PLAIN).clicks.approve), [{ url: '/api/approvals/unified/apr_plain1/approve', body: {} }]);
  assert.match(chat(PLAIN).html, /Reason, if you deny/);
});

test('a card with options (a question) shows its options on both surfaces, not Approve / Deny', () => {
  for (const html of [chat(MENU).html, approvals(MENU).html]) {
    assert.match(html, /Ship A[^]*Ship B/);
    assert.doesNotMatch(html, />Approve<|>Deny<|>Done<|Reason, if you deny/);
  }
});
