/**
 * Moving through a card of several questions without answering each one: the first question has
 * Next, every middle one Back and Next, the last Back and Submit, all enabled whether or not the question is
 * answered. There is no separate Skip: Next on an unanswered question skips it, and the answered card shows it as
 * "Skipped". A card of one question keeps its rules (Send once answered). Per position, the footer comes from
 * lib/multiPart cardFooter (multiPart.test.ts); here the real card is rendered (react-dom/server) at its first
 * question, on a page and in a popover (Dismiss leads the pills), and the answered card is drawn with skips.
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
(globalThis as { React?: unknown }).React = React;

const { QuestionCard, AnsweredQuestionCard } = await import('../components/chat/MultiPartCard.tsx');
const { default: CardPopFrame } = await import('../components/kit/CardPopFrame.tsx');
const { questionnaireAnswers, questionnaireParts } = await import('./multiPart.ts');
type Part = Parameters<typeof QuestionCard>[0]['parts'][number];

const h = React.createElement;
const SRC = fileURLToPath(new URL('..', import.meta.url));
const read = (f: string) => readFileSync(SRC + f, 'utf8');

const THREE: Part[] = [
  { index: 0, question: 'Size?', select: 'single', options: [{ n: '1', label: 'Circle' }, { n: '2', label: 'Square' }] },
  { index: 1, question: 'Colour?', select: 'multi', options: [{ n: '1', label: 'Red' }, { n: '2', label: 'Blue' }] },
  { index: 2, question: 'Finish?', select: 'single', options: [{ n: '1', label: 'Matte' }], has_free_text: true },
];

/** The footer's pill buttons in order (after the parts; pills are rounded-full, option rows rounded-xl): [words, disabled]. */
const buttons = (html: string) => [...html.slice(html.lastIndexOf('data-parts')).matchAll(/<button([^>]*)>(?:<svg[^]*?<\/svg>)?([^<]*)<\/button>/g)]
  .filter((m) => /class="[^"]*rounded-full/.test(m[1]))
  .map((m) => [m[2], /\sdisabled=""/.test(m[1])] as [string, boolean]);

test('the first question of several: Next alone, enabled while nothing is answered; no Skip, no Back', () => {
  const html = renderToStaticMarkup(h(QuestionCard, { title: 'dev', parts: THREE, onSubmit: async () => null }));
  assert.deepEqual(buttons(html), [['Next', false]]);
  assert.ok(!/>Skip</.test(html), 'no Skip button');
});

test('in a popover, Dismiss still leads the footer row, then Next', () => {
  const html = renderToStaticMarkup(h(CardPopFrame, { id: 'c1', label: 'Card', onClose() {}, onGoTo() {} },
    h(QuestionCard, { title: 'dev', parts: THREE, onSubmit: async () => null, onDismiss: async () => null })));
  assert.deepEqual(buttons(html).map(([w]) => w), ['Dismiss', 'Next']);
});

test('a card of one question is unchanged: Send, disabled until answered', () => {
  const one: Part[] = [{ ...THREE[2], index: 0 }];
  const html = renderToStaticMarkup(h(QuestionCard, { title: 'dev', parts: one, onSubmit: async () => null }));
  assert.deepEqual(buttons(html), [['Send', true]]);
});

test('the card draws its footer from cardFooter and sends through the empty-submit gate', () => {
  const src = read('components/chat/MultiPartCard.tsx');
  assert.ok(src.includes('cardFooter(parts, s)'), 'footer per position from lib/multiPart');
  assert.ok(src.includes('emptySubmit('), 'nothing-answered submit goes through the gate');
  assert.ok(!src.includes('skipPart'), 'no separate Skip');
  assert.ok(read('components/QuestionnaireForm.tsx').includes('allowEmpty'), 'a questionnaire may go with nothing answered');
});

test('an answered card shows a skipped question as Skipped, page by page', () => {
  const html = renderToStaticMarkup(h(AnsweredQuestionCard, { title: 'dev', parts: THREE,
    answers: [{ ns: ['2'], text: '', skipped: false }, { ns: [], text: '', skipped: true }, { ns: [], text: '', skipped: true }] }));
  assert.equal((html.match(/data-skipped/g) ?? []).length, 2);
  assert.ok(html.includes('>Square<'));
});

test('a partially answered questionnaire read back from the server shows its skipped questions as Skipped', () => {
  const qs = [
    { n: 1, prompt: 'Which day?', kind: 'menu', required: 1, menu: { options: [{ n: '1', label: 'Monday' }] },
      answer: { option_n: '1', answer_text: null, state: 'final' } },
    { n: 2, prompt: 'Why?', kind: 'free_text', required: 1, answer: { option_n: null, answer_text: null, state: 'skipped' } },
  ];
  const parts = questionnaireParts(qs);
  const html = renderToStaticMarkup(h(AnsweredQuestionCard, { title: 'Q', parts, answers: questionnaireAnswers(parts, qs) }));
  assert.equal((html.match(/data-skipped/g) ?? []).length, 1);
});
