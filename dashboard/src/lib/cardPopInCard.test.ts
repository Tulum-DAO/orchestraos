/**
 * A card in its popover (components/kit/CardPopFrame.tsx, CardPopContext) draws the popover's controls itself: at the
 * header's top right, Go to card then Close (where its × sits on a page), and a waiting card's Dismiss in its answer
 * footer, left of Send. The same card on a page (chat, Approvals, Inbox) is drawn as it always was: its × dismisses.
 *
 * The real cards are rendered (react-dom/server). They import stylesheets node cannot load, so a loader hook stands
 * an empty module in for every .css. Buttons are found by their data hooks; a click is the button's own onClick,
 * read from the element as it is created (the cards compile to React.createElement on the global React).
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { register } from 'node:module';
import * as React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

register('data:text/javascript,' + encodeURIComponent(
  "export async function load(url, ctx, next) { return url.endsWith('.css') ? { format: 'module', source: '', shortCircuit: true } : next(url, ctx); }"));

/** Every onClick of a button carrying one of these hooks, as last rendered. */
const HOOKS = ['data-card-close', 'data-card-goto', 'data-card-dismiss'] as const;
let clicks: Partial<Record<(typeof HOOKS)[number], () => void>> = {};
const spy = (type: unknown, props: Record<string, unknown> | null, ...kids: unknown[]) => {
  for (const h of HOOKS) if (props?.[h] && typeof props.onClick === 'function') clicks[h] = props.onClick as () => void;
  return (React.createElement as (...a: unknown[]) => React.ReactElement)(type, props, ...kids);
};
(globalThis as { React?: unknown }).React = { ...React, createElement: spy };

const { default: CardPopFrame } = await import('../components/kit/CardPopFrame.tsx');
const { default: DecisionCard, ApprovalAnswer } = await import('../components/chat/DecisionCard.tsx');
const { QuestionCard, AnsweredQuestionCard, MultiPartAnswer } = await import('../components/chat/MultiPartCard.tsx');
const { default: PendingApprovalCard } = await import('../components/chat/PendingApprovalCard.tsx');
const { default: DecisionBubble } = await import('../components/chat/DecisionBubble.tsx');
const { PopNote } = await import('../components/kit/CardPopControls.tsx');
type Part = Parameters<typeof QuestionCard>[0]['parts'][number];
type Item = Parameters<typeof PendingApprovalCard>[0]['item'];
type Dec = Parameters<typeof DecisionBubble>[0]['decision'];

const h = React.createElement;
const SRC = fileURLToPath(new URL('..', import.meta.url));
const read = (f: string) => readFileSync(SRC + f, 'utf8');

/** The card drawn on a page, and in a popover whose Close / Go to card are counted. */
const page = (el: React.ReactElement) => { clicks = {}; return renderToStaticMarkup(el); };
function inPop(el: React.ReactElement) {
  const calls = { close: 0, goto: 0 };
  clicks = {};
  const html = renderToStaticMarkup(h(CardPopFrame, { id: 'c1', label: 'Card', onClose: () => { calls.close++; }, onGoTo: () => { calls.goto++; } }, el));
  return { html, calls, clicks: { ...clicks } };
}

let dismissed = 0;
const onDismiss = async () => { dismissed++; return null; };
const WORDS: Part[] = [{ index: 0, question: 'Size?', select: 'single', options: [{ n: '1', label: 'Circle' }], has_free_text: true }];
const PICK: Part[] = [{ index: 0, question: 'Size?', select: 'single', options: [{ n: '1', label: 'Circle' }] }];
const TWO: Part[] = [PICK[0], { index: 1, question: 'Colour?', select: 'multi', options: [{ n: '1', label: 'Red' }] }];
const item = (o: Partial<Item>): Item => ({ id: 'apr_1', type: 'agent', urgency: 'normal', status: 'pending', title: 'Deploy?',
  description: '', risk_level: 'low', evidence_summary: '', qa_verdict: null, sandbox_result: null, onepager_url: null,
  created_at: '', updated_at: '', source: 'x', ...o });

/** The header row: from the card's opening tag to the end of its first row of controls. */
const header = (html: string) => html.slice(0, html.search(/data-context|data-parts|class="mt-2|data-testid="decision-outcome"|Reading every part/));
const footer = (html: string) => html.slice(html.lastIndexOf('data-card-footer'));
/** The order of the footer's buttons, by their words. */
const footerButtons = (html: string) => [...footer(html).matchAll(/<button[^>]*>(?:<svg[^]*?<\/svg>)?([^<]*)<\/button>/g)].map((m) => m[1]);

const CARDS: [string, React.ReactElement][] = [
  ['a question with a write-in (Send)', h(QuestionCard, { title: 'dev', parts: WORDS, onSubmit: async () => null, onDismiss })],
  ['an approval (Approve / Deny)', h(DecisionCard, { kind: 'approval', question: 'Deploy?', open: true }, h(ApprovalAnswer, { onApprove() {}, onDeny() {} }))],
  ['an answered question', h(AnsweredQuestionCard, { title: 'dev', parts: PICK, answers: [{ ns: ['1'], text: '', skipped: false }] })],
  ['a dismissed question', h(QuestionCard, { title: 'dev', parts: PICK, onSubmit: async () => null, onDismiss, dismissed: true })],
  ['an answered approval', h(DecisionCard, { kind: 'approval', question: 'Deploy?', open: false, outcome: 'Approved' })],
  ['a multi-part menu still being read', h(MultiPartAnswer, { agentId: 'dev', cardId: 'c', onDismiss })],
  ['a read-only menu', h(PendingApprovalCard, { item: item({ kind: 'menu', menu: { read_only: true } }), onChanged() {} })],
];

test('in a popover every card\'s header ends with Go to card then Close, named, always shown', () => {
  for (const [what, el] of CARDS) {
    const { html } = inPop(el);
    const head = header(html);
    const go = head.indexOf('aria-label="Go to card"');
    const close = head.indexOf('aria-label="Close"');
    assert.ok(go > 0 && close > go, `${what}: Go to card, then Close, in the header`);
    assert.match(head, /<button type="button" aria-label="Go to card" title="Go to card" class="hit-44 w-7 h-7 [^"]*" data-card-goto="true"><svg[^>]*lucide-arrow-up-right/);
    assert.match(head, /<button type="button" aria-label="Close" title="Close" class="hit-44 w-7 h-7 [^"]*" data-card-close="true"><svg[^>]*lucide-x/);
    assert.doesNotMatch(head, /aria-label="Dismiss/, `${what}: the header's × is Close, not Dismiss`);
    assert.doesNotMatch(html, /opacity-0|kit-card-pop-ctl/, `${what}: no hover reveal`);
    assert.equal((html.match(/data-card-close/g) ?? []).length, 1, `${what}: one Close`);
  }
});

test('the header\'s × closes the popover (never dismisses the card); Go to card goes to it', () => {
  dismissed = 0;
  for (const [what, el] of CARDS) {
    const { calls, clicks: c } = inPop(el);
    assert.ok(c['data-card-close'] && c['data-card-goto'], `${what}: both are buttons`);
    c['data-card-close']!();
    assert.deepEqual(calls, { close: 1, goto: 0 }, `${what}: × is Close`);
    c['data-card-goto']!();
    assert.deepEqual(calls, { close: 1, goto: 1 }, `${what}: Go to card`);
  }
  assert.equal(dismissed, 0, 'nothing was dismissed');
});

test('the header controls are the ×\'s size and look, on desktop and phone (hit-44, touch-compact)', () => {
  const { html } = inPop(CARDS[0][1]);
  const onPage = page(CARDS[0][1]);
  const x = onPage.match(/<button type="button" aria-label="Dismiss" title="Dismiss" class="([^"]+)"/)?.[1];
  assert.ok(x, 'the page card has its ×');
  for (const name of ['Go to card', 'Close']) {
    assert.equal(html.match(new RegExp(`aria-label="${name}" title="${name}" class="([^"]+)"`))?.[1], x, `${name} looks like the ×`);
  }
  // On touch 16px apart, so the two 44px tap areas never overlap.
  assert.match(html, /<div class="touch-compact shrink-0 flex items-center gap-2 pointer-coarse:gap-4 -mr-1\.5 -mt-1" data-card-pop-controls="true">/);
});

test('in a popover Dismiss is the first footer pill, directly left of Send, quiet, the pills\' height and radius', () => {
  const { html } = inPop(CARDS[0][1]);
  assert.deepEqual(footerButtons(html), ['Dismiss', 'Send']);
  const pill = footer(html).match(/<button type="button" aria-label="Dismiss" title="Dismiss" data-card-dismiss="true" class="([^"]+)"/)?.[1];
  assert.ok(pill, 'Dismiss is a named button in the footer');
  for (const c of ['hit-44', 'text-xs', 'px-3', 'py-1', 'rounded-full', 'border-border', 'text-muted-foreground']) {
    assert.ok(pill.split(' ').includes(c), `Dismiss has ${c}`);
  }
  const send = footer(html).match(/<button type="button" class="([^"]+)"[^>]*>Send<\/button>/)?.[1] ?? '';
  for (const c of ['hit-44', 'text-xs', 'py-1', 'rounded-full']) assert.ok(send.split(' ').includes(c), `Send has ${c} too`);
  // A multi-part question: Dismiss leads the pills (Skip / Next / Send follow).
  const two = inPop(h(QuestionCard, { title: 'dev', parts: TWO, onSubmit: async () => null, onDismiss })).html;
  assert.equal(footerButtons(two)[0], 'Dismiss');
  // A multi-part menu still being read has no pills yet: Dismiss alone, at the right.
  assert.deepEqual(footerButtons(inPop(CARDS[5][1]).html), ['Dismiss']);
  // A read-only menu (its notice, no buttons): Dismiss under the notice, at the right.
  const ro = inPop(CARDS[6][1]).html;
  assert.ok(ro.includes('<div class="touch-compact flex flex-wrap items-center justify-end gap-2 px-4 pb-3" data-card-footer="true">'), 'its own row, at the right');
  assert.deepEqual(footerButtons(ro), ['Dismiss']);
  assert.ok(ro.indexOf('Multi-part menu') < ro.indexOf('data-card-dismiss'), 'under the notice');
});

test('Dismiss in a footer keeps the two-tap safety, spinner and error of the header ×: one DismissButton', () => {
  const src = read('components/chat/DecisionCard.tsx');
  const fn = src.slice(src.indexOf('export function DismissButton'), src.indexOf('export function FooterDismiss'));
  assert.equal((fn.match(/setTimeout\(\(\) => setArmed\(false\), 5000\)/g) ?? []).length, 1, 'one arm / disarm');
  assert.match(fn, /place === 'footer'/);
  assert.match(fn, /armed \? 'Dismiss ✓' : 'Dismiss'/);
  assert.match(fn, /Loader2/);
  assert.match(fn, /\{error && <div className="basis-full order-last/);
  assert.equal((src.match(/setArmed\(true\)/g) ?? []).length, 1, 'the logic is not duplicated');
  assert.doesNotMatch(read('components/chat/MultiPartCard.tsx'), /setArmed/);
  // Armed and unarmed are separate class sets: never two text-* colours on one button.
  assert.match(fn, /armed \? `\$\{PILL\} border-border bg-muted text-foreground hover:bg-accent` : PILL_QUIET/);
  assert.doesNotMatch(src, /tall/, 'no dead Dismiss-before-Approve path');
});

test('no Dismiss where the card cannot be dismissed (approvals, answered, dismissed)', () => {
  for (const [what, el] of CARDS.filter(([w]) => /approval|answered|dismissed/.test(w))) {
    assert.doesNotMatch(inPop(el).html, /data-card-dismiss|aria-label="Dismiss/, what);
  }
  // An approval keeps its row as on a page: Approve and Deny.
  assert.match(inPop(CARDS[1][1]).html, /<div class="grid grid-cols-2 gap-2"><button[^>]*>Approve<\/button><button[^>]*>Deny<\/button><\/div>/);
});

test('on a page the same cards are drawn as before: the header × dismisses, no Close or Go to card, no footer Dismiss', () => {
  for (const [what, el] of [CARDS[0], CARDS[5], CARDS[6]] as const) {
    const html = page(el);
    assert.match(header(html), /aria-label="Dismiss" title="Dismiss"/, `${what}: the × is Dismiss`);
    assert.doesNotMatch(html, /Go to card|aria-label="Close"|data-card-dismiss|data-card-footer/, what);
  }
  for (const [what, el] of CARDS) assert.doesNotMatch(page(el), /data-card-pop-controls|data-card-goto|data-card-close/, what);
  // On a page the footer is unchanged: Send alone, the × above.
  assert.match(page(CARDS[0][1]), /<div class="touch-compact flex items-center gap-2 px-3\.5 pb-3 pt-1"><span class="flex-1"><\/span><button type="button" class="[^"]+" disabled="">Send<\/button><\/div>/);
});

test('every body a popover can draw goes through the popover controls', () => {
  // Words in place of a card (loading, couldn't load) and the answered in-agent menu's one-line pill.
  const note = inPop(h(PopNote, { className: 'kit-card-pop-note' }, 'Loading the card…')).html;
  assert.match(note, /data-card-pop-note[^]*Loading the card…[^]*data-card-goto[^]*data-card-close/);
  assert.equal(page(h(PopNote, { className: 'kit-card-pop-note' }, 'Loading the card…')), '<p class="kit-card-pop-note">Loading the card…</p>');
  const menuAnswer = { id: 'd1', kind: 'menu_answer', answer: 'Circle', question: 'Size?', state: 'answered' } as unknown as Dec;
  const pill = inPop(h(DecisionBubble, { decision: menuAnswer, onAnswered() {} })).html;
  assert.match(pill, /data-menu-answer[^]*data-card-goto[^]*data-card-close/);
  // Tall enough for the 44px tap areas inside the clipping popover (28px buttons + 2 x 8px), and no px-3 / py-1 left.
  assert.match(pill, /class="[^"]*rounded-full border bg-card border-info\/50 flex items-center gap-2 pl-3 pr-2 py-2"/);
  assert.doesNotMatch(page(h(DecisionBubble, { decision: menuAnswer, onAnswered() {} })), /data-card-goto/);
  // CardChip's bodies: every one is a card above or a PopNote; none is a bare line.
  const chip = read('components/kit/CardChip.tsx');
  const bodies = [...chip.matchAll(/body = (<[A-Za-z]+)/g)].map((m) => m[1]);
  assert.deepEqual([...new Set(bodies)].sort(), ['<AnsweredQuestionCard', '<DecisionBubble', '<PendingApprovalCard', '<PopNote', '<QuestionnaireForm'].sort());
  assert.doesNotMatch(chip, /body = <p\b/);
  assert.doesNotMatch(read('components/kit/kit.css'), /\.kit-card-pop-card > p|\.kit-card-pop-note \{[^}]*opacity|\.kit-card-pop-card \.kit-card-pop-note/, 'no dead note rules');
  assert.match(read('components/QuestionnaireForm.tsx'), /return <PopNote [^]*return <PopNote /);
  assert.doesNotMatch(read('components/QuestionnaireForm.tsx'), /return <p\b/);
  // The one mechanism: both card frames read CardPopContext, the frame provides it.
  assert.match(read('components/kit/CardPopFrame.tsx'), /<CardPopContext\.Provider value=\{\{ onClose, onGoTo \}\}>\{children\}<\/CardPopContext\.Provider>/);
  for (const f of ['components/chat/DecisionCard.tsx', 'components/chat/MultiPartCard.tsx']) {
    assert.match(read(f), /useCardPop\(\)/, f);
    assert.match(read(f), /<PopHeaderControls /, f);
  }
});
