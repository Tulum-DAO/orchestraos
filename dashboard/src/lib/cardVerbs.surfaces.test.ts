/**
 * The follow-ups to cardVerbs.test.ts: a card's own verbs on the third surface (the Inbox), a snoozed task, the note
 * a person types, and how an answered task reads.
 *
 * - The Inbox and the Approvals page are rendered as pages (react-dom/server) from a QueryClient that already holds
 *   the list, so nothing is fetched while they draw.
 * - Typing and pressing run the real answer area with no DOM: CardVerbsAnswer and TaskAnswer run as themselves,
 *   their own useState / useRef through React 19's hook dispatcher, as modelPickerPanel.test.ts runs StopSlider.
 *   fetch is stubbed, so a press shows exactly what would be posted.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { register } from 'node:module';
import * as React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { cardVerbs, snoozedLast, snoozedUntil, snoozeLine, taskVerbWord } from './cardVerbs.ts';
import { chosenText, decisionTone, outcomeLine, focusTarget } from './decisions.ts';

register('data:text/javascript,' + encodeURIComponent(
  "export async function load(url, ctx, next) { return url.endsWith('.css') ? { format: 'module', source: '', shortCircuit: true } : next(url, ctx); }"));

/** Every verb button's onClick, by its verb, as last rendered (data-card-verb). */
let verbClicks: Record<string, () => void> = {};
const spy = (type: unknown, props: Record<string, unknown> | null, ...kids: unknown[]) => {
  const v = props?.['data-card-verb'];
  if (typeof v === 'string' && typeof props?.onClick === 'function') verbClicks[v] = props.onClick as () => void;
  return (React.createElement as (...a: unknown[]) => React.ReactElement)(type, props, ...kids);
};
(globalThis as { React?: unknown }).React = { ...React, createElement: spy };

const { default: PendingApprovalCard } = await import('../components/chat/PendingApprovalCard.tsx');
const { default: DecisionBubble } = await import('../components/chat/DecisionBubble.tsx');
const { CardVerbsAnswer } = await import('../components/chat/DecisionCard.tsx');
const { default: Inbox } = await import('../pages/Inbox.tsx');
const { default: Approvals } = await import('../pages/Approvals.tsx');
type Item = Parameters<typeof PendingApprovalCard>[0]['item'];
type Dec = Parameters<typeof DecisionBubble>[0]['decision'];
const h = React.createElement;

const NOW = Date.now();
const LATER = new Date(NOW + 2 * 3600_000).toISOString();
const EARLIER = new Date(NOW - 60_000).toISOString();

interface Row { id: string; kind: string | null; options: string[]; question: string; snoozed_until?: string | null; created_at?: string }
const TASK: Row = { id: 'apr_task1', kind: 'human_task', options: ['done', 'cant', 'snooze'], question: 'Plug in the test phone' };
const SNOOZED: Row = { ...TASK, id: 'apr_task2', question: 'Sign the delivery note', snoozed_until: LATER };
const PLAIN: Row = { id: 'apr_plain1', kind: null, options: ['approve', 'deny'], question: 'Deploy to staging?' };

const asItem = (r: Row): Item => ({ id: r.id, type: 'agent', urgency: 'normal', status: 'pending', title: r.question,
  description: '', risk_level: 'low', evidence_summary: '', qa_verdict: null, sandbox_result: null, onepager_url: null,
  created_at: r.created_at ?? '2026-11-21T10:00:00Z', updated_at: '', source: 'agent_request', kind: r.kind, options: r.options,
  menu: null, agent_id: 'dev', snoozed_until: r.snoozed_until ?? null });
const asDecision = (r: Row, o: Partial<Dec> = {}): Dec => ({ id: r.id, from_agent: 'dev', kind: r.kind, state: 'pending',
  question: r.question, summary: null, options: r.options, menu: null, risk_level: 'low', reversibility: null, answer: null,
  answer_text: null, option_n: null, answered_by: null, answer_surface: null, answer_device: null,
  created_at: '2026-11-21T10:00:00Z', answered_at: null, expires_at: null, snoozed_until: r.snoozed_until ?? null, ...o });

/** A page drawn from a QueryClient that already holds the approvals list (and empty mail / questionnaires). */
function pageHtml(page: React.ComponentType, rows: Row[]) {
  const client = new QueryClient({ defaultOptions: { queries: { gcTime: Infinity, staleTime: Infinity, retry: false } } });
  client.setQueryData(['unified-approvals'], { items: rows.map(asItem), total: rows.length });
  client.setQueryData(['unified-approvals-stats'], { by_type: {}, by_urgency: {}, by_status: {}, total: rows.length });
  client.setQueryData(['messages-recent'], { messages: [] });
  client.setQueryData(['questionnaires'], { questionnaires: [], total: 0, pending: 0 });
  verbClicks = {};
  const html = renderToStaticMarkup(h(MemoryRouter, null, h(QueryClientProvider, { client }, h(page))));
  client.clear();
  return { html, clicks: { ...verbClicks } };
}
const chat = (d: Dec) => { verbClicks = {}; const html = renderToStaticMarkup(h(DecisionBubble, { decision: d, onAnswered() {} })); return { html, clicks: { ...verbClicks } }; };
const approvalsCard = (r: Row) => { verbClicks = {}; const html = renderToStaticMarkup(h(PendingApprovalCard, { item: asItem(r), onChanged() {} })); return { html, clicks: { ...verbClicks } }; };

/** A tap with fetch stubbed: what was posted, and where. */
async function capture(tap: () => void, reply: unknown = {}) {
  const sent: { url: string; body: unknown }[] = [];
  const saved = globalThis.fetch;
  globalThis.fetch = (async (url: string, init?: { body?: string }) => {
    sent.push({ url: String(url), body: init?.body ? JSON.parse(init.body) : undefined });
    return { ok: true, status: 200, json: async () => reply } as Response;
  }) as typeof fetch;
  try { tap(); await new Promise((r) => setTimeout(r, 0)); } finally { globalThis.fetch = saved; }
  return sent;
}

// ── a component harness with no DOM ──
type El = { type?: unknown; props: Record<string, unknown> };
function mount(component: unknown, first: Record<string, unknown>) {
  const internals = (React as unknown as Record<string, { H: unknown }>).__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE;
  const slots: unknown[] = [];
  let at = 0;
  let dirty = false;
  const dispatcher = {
    useState(init: unknown) {
      const k = at++;
      if (!(k in slots)) slots[k] = typeof init === 'function' ? (init as () => unknown)() : init;
      const set = (v: unknown) => {
        const next = typeof v === 'function' ? (v as (p: unknown) => unknown)(slots[k]) : v;
        if (!Object.is(next, slots[k])) { slots[k] = next; dirty = true; }
      };
      return [slots[k], set];
    },
    useRef(v: unknown) { const k = at++; if (!(k in slots)) slots[k] = { current: v }; return slots[k]; },
    useContext(ctx: { _currentValue?: unknown }) { return ctx._currentValue; },
    useEffect() { /* no effects without a DOM */ },
  };
  let tree: El = null as unknown as El;
  const render = () => {
    const prev = internals.H;
    internals.H = dispatcher;
    try {
      let n = 0;
      do { dirty = false; at = 0; tree = (component as (p: unknown) => El)(first); } while (dirty && ++n < 25);
    } finally { internals.H = prev; }
    return tree;
  };
  render();
  return { render, tree: () => tree };
}
/** The first element in a tree that matches. */
function find(node: unknown, ok: (el: El) => boolean): El | null {
  if (!node || typeof node !== 'object') return null;
  if (Array.isArray(node)) { for (const c of node) { const f = find(c, ok); if (f) return f; } return null; }
  const el = node as El;
  if (el.props && ok(el)) return el;
  return find(el.props?.children, ok);
}
const verbButton = (tree: El, verb: string) => find(tree, (e) => e.props['data-card-verb'] === verb);
/** CardVerbsAnswer for a card, and the answer area it draws (TaskAnswer / SnoozedTask) running as itself. */
function answerArea(card: Row) {
  const outer = mount(CardVerbsAnswer, { card: asItem(card), onAnswered() {} });
  let inner = mount(outer.tree().type, outer.tree().props);
  return {
    outer,
    inner: () => inner,
    /** Re-render CardVerbsAnswer after a state change, and mount what it draws now. */
    refresh() { const t = outer.render(); inner = mount(t.type, t.props); return t; },
    type(words: string) {
      const input = find(inner.tree(), (e) => e.type === 'input');
      assert.ok(input, 'the note field');
      (input.props.onChange as (e: unknown) => void)({ target: { value: words } });
      inner.render();
    },
  };
}

// ── 1. the Inbox ──

test('the Inbox draws a task for you with its own verbs, the same as the chat and the Approvals page', async () => {
  const inbox = pageHtml(Inbox, [TASK]);
  assert.match(inbox.html, /Plug in the test phone/, 'the card is drawn');
  assert.deepEqual(Object.keys(inbox.clicks), ['done', 'cant', 'snooze']);
  assert.doesNotMatch(inbox.html, />Approve<|>Deny<|Reason, if you deny/);
  assert.deepEqual(Object.keys(inbox.clicks), Object.keys(chat(asDecision(TASK)).clicks));
  assert.deepEqual(Object.keys(inbox.clicks), Object.keys(approvalsCard(TASK).clicks));
  assert.deepEqual(await capture(inbox.clicks.done), [{ url: '/api/approvals/unified/apr_task1/answer', body: { answer: 'done' } }]);
  // A plain approval in the Inbox keeps Approve / Deny on /approve.
  const plain = pageHtml(Inbox, [PLAIN]);
  assert.deepEqual(Object.keys(plain.clicks), ['approve', 'deny']);
  assert.deepEqual(await capture(plain.clicks.approve), [{ url: '/api/approvals/unified/apr_plain1/approve', body: {} }]);
});

// ── 2. a snoozed task ──

test('a snoozed task says until when, offers Done / Can\'t behind "Answer now", and no second Snooze', () => {
  assert.equal(snoozedUntil(SNOOZED, NOW), Date.parse(LATER));
  assert.equal(snoozedUntil({ ...SNOOZED, snoozed_until: EARLIER }, NOW), null, 'a snooze that is over');
  assert.equal(snoozedUntil({ ...PLAIN, snoozed_until: LATER }, NOW), null, 'only a task snoozes');
  const set = cardVerbs(SNOOZED, NOW);
  assert.ok(set.mode === 'task' && set.snoozed?.until === Date.parse(LATER));
  assert.deepEqual(set.verbs.map((v) => v.verb), ['done', 'cant'], 'no Snooze while snoozed');
  assert.deepEqual(cardVerbs({ ...SNOOZED, snoozed_until: EARLIER }, NOW).verbs.map((v) => v.verb), ['done', 'cant', 'snooze']);
  assert.match(snoozeLine(Date.parse(LATER)), /^Snoozed until \d{1,2}:\d{2}/);
  assert.equal(snoozeLine(null), 'Snoozed');
  const line = snoozeLine(Date.parse(LATER));
  for (const html of [chat(asDecision(SNOOZED)).html, approvalsCard(SNOOZED).html, pageHtml(Inbox, [SNOOZED]).html]) {
    assert.ok(html.includes(line), 'the snoozed line');
    assert.match(html, /data-card-answer-now="true"[^>]*>Answer now<\/button>/);
    assert.doesNotMatch(html, /data-card-verb/, 'the verbs wait behind Answer now');
  }
});

test('Answer now opens Done / Can\'t on a snoozed task', async () => {
  const a = answerArea(SNOOZED);
  const answerNow = find(a.inner().tree(), (e) => e.props['data-card-answer-now'] === true);
  assert.ok(answerNow, 'Answer now');
  (answerNow.props.onClick as () => void)();
  a.refresh();
  assert.ok(verbButton(a.inner().tree(), 'done') && verbButton(a.inner().tree(), 'cant'));
  assert.equal(verbButton(a.inner().tree(), 'snooze'), null);
  const sent = await capture(verbButton(a.inner().tree(), 'done')!.props.onClick as () => void);
  assert.deepEqual(sent, [{ url: '/api/approvals/unified/apr_task2/answer', body: { answer: 'done' } }]);
});

test('a Snooze shows as snoozed at once, so a second tap cannot quietly push it back again', async () => {
  const a = answerArea(TASK);
  const sent = await capture(verbButton(a.inner().tree(), 'snooze')!.props.onClick as () => void, { status: 'snoozed' });
  assert.deepEqual(sent, [{ url: '/api/approvals/unified/apr_task1/answer', body: { answer: 'snooze' } }]);
  const now = a.refresh();
  assert.ok(find(a.inner().tree(), (e) => e.props['data-card-answer-now'] === true), 'the card is collapsed behind Answer now');
  assert.equal(verbButton(a.inner().tree(), 'snooze'), null, 'no Snooze to tap again');
  assert.match(renderToStaticMarkup(now as unknown as React.ReactElement), />Snoozed</);
});

test('a snoozed task goes last on the Approvals page and in the Inbox (and the chat\'s "Needs you")', () => {
  // Newest first everywhere, so the snoozed task (the newest) would lead without the floor.
  const rows = [{ ...SNOOZED, created_at: '2026-11-21T12:00:00Z' }, { ...TASK, created_at: '2026-11-21T10:00:00Z' }];
  assert.deepEqual(snoozedLast(rows, NOW).map((r) => r.id), ['apr_task1', 'apr_task2']);
  for (const page of [Approvals, Inbox]) {
    const { html } = pageHtml(page, rows);
    const a = html.indexOf('Plug in the test phone');
    const b = html.indexOf('Sign the delivery note');
    assert.ok(a > 0 && b > a, `${page.name}: the snoozed task after the other`);
  }
  assert.equal(focusTarget([asDecision(SNOOZED), asDecision(TASK)])?.id, 'apr_task1');
});

// ── 3. an answered task ──

test('an answered task reads Done / Can\'t / Snoozed and its note; Can\'t looks like a deny', () => {
  const at = '2026-11-21T10:05:00Z';
  const cant = asDecision(TASK, { state: 'answered', answer: 'cant', answer_text: 'no cable', answered_at: at, delivery: 'delivered' });
  const done = asDecision(TASK, { state: 'answered', answer: 'done', answer_text: null, answered_at: at, delivery: 'delivered' });
  assert.equal(chosenText(cant), "Can't — no cable");
  assert.equal(chosenText(done), 'Done');
  assert.equal(chosenText({ ...done, answer_text: 'plugged in' }), 'Done — plugged in');
  assert.equal(taskVerbWord('snooze'), 'Snoozed');
  assert.equal(taskVerbWord('approve'), null);
  assert.equal(decisionTone(cant), 'denied');
  assert.equal(decisionTone(asDecision(PLAIN, { state: 'answered', answer: 'deny', answer_text: 'no', delivery: 'delivered' })), 'denied');
  assert.match(outcomeLine(cant), /^Can't — no cable · answered by /);
  const html = chat(cant).html;
  assert.match(html, /data-tone="denied"[^>]*class="[^"]*border-destructive\/50/);
  assert.match(html.replace(/&#x27;/g, "'"), /Can't — no cable/);
});

// ── 4. the note a person types ──

test('a typed note reaches the request body as answer_text (Done and Can\'t), and Snooze sends none', async () => {
  for (const verb of ['cant', 'done']) {
    const a = answerArea(TASK);
    a.type('no cable here');
    const sent = await capture(verbButton(a.inner().tree(), verb)!.props.onClick as () => void);
    assert.deepEqual(sent, [{ url: '/api/approvals/unified/apr_task1/answer', body: { answer: verb, answer_text: 'no cable here' } }]);
  }
  const s = answerArea(TASK);
  s.type('later');
  assert.deepEqual(await capture(verbButton(s.inner().tree(), 'snooze')!.props.onClick as () => void),
    [{ url: '/api/approvals/unified/apr_task1/answer', body: { answer: 'snooze' } }]);
  // A one-word note that looks like a flag is still just a note.
  const f = answerArea(TASK);
  f.type('--help');
  assert.deepEqual(await capture(verbButton(f.inner().tree(), 'cant')!.props.onClick as () => void),
    [{ url: '/api/approvals/unified/apr_task1/answer', body: { answer: 'cant', answer_text: '--help' } }]);
});
