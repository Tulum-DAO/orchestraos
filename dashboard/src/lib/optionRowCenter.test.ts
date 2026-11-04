/**
 * An option row (a letter or number badge beside its label) follows one rule, so the rows of one card line up alike
 * whether a label takes one line or several: the badge sits at the top of the row, centred on the label's FIRST
 * line. A one-line label then reads centred on the badge; a longer one starts level with it and flows below. Every
 * such row uses it: the answer option buttons, the chosen rows of an answered card and the typed-words row.
 *
 * How: the line is items-start and leading-snug; the badge sits in a slot one label line tall (at least 22px) and is
 * centred in it; the label column is pushed down by half of whatever the badge is taller than the line. Both come
 * from 1lh (index.css .opt-badge-slot / .opt-text), so it holds under the text-size setting. The badge carries no
 * mt-px nudge. On a phone index.css gives every button a 44px floor; the option button centres its line in it, so a
 * one-line option keeps equal space above and below. The real cards are rendered (react-dom/server).
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
type Part = Parameters<typeof QuestionCard>[0]['parts'][number];

const h = React.createElement;
const SRC = fileURLToPath(new URL('..', import.meta.url));
const read = (f: string) => readFileSync(SRC + f, 'utf8');

const PARTS: Part[] = [{
  index: 0, question: 'Size?', select: 'single', lean: '2',
  options: [{ n: '1', label: 'Circle' }, { n: '2', label: 'Square', detail: 'Four equal sides' }],
}];
const MULTI: Part[] = [{ ...PARTS[0], select: 'multi' }];
const WORDS: Part[] = [{ ...PARTS[0], has_free_text: true }];

const classes = (tag: string) => (tag.match(/class="([^"]*)"/)?.[1] ?? '').split(/\s+/);
const has = (tag: string, ...want: string[]) => {
  const c = classes(tag);
  for (const w of want) assert.ok(c.includes(w), `expected "${w}" in class="${c.join(' ')}"`);
};
/** The opening tags inside each element carrying a hook, in document order. */
function rows(html: string, hook: string) {
  const out: string[][] = [];
  const re = new RegExp(`<(button|div)[^>]*${hook}[^>]*>`, 'g');
  for (let m; (m = re.exec(html));) {
    const end = html.indexOf(`</${m[1]}>`, m.index);
    out.push(html.slice(m.index, end).match(/<[a-z]+[^>]*>/g) ?? []);
  }
  return out;
}
const find = (tags: string[], cls: string) => tags.findIndex((t) => classes(t).includes(cls));

/** A badge + label line: items-start + leading-snug, the badge (no nudge) first in its slot, then the label column. */
function checkLine(tags: string[], where: string) {
  const line = find(tags, 'gap-2.5');
  assert.ok(line >= 0, `${where}: a badge + label line`);
  has(tags[line], 'flex', 'items-start', 'leading-snug');
  const slot = find(tags, 'opt-badge-slot');
  assert.equal(slot, line + 1, `${where}: the badge slot opens the line`);
  has(tags[slot], 'flex', 'items-center', 'shrink-0');
  const badge = tags[slot + 1];
  has(badge, 'w-[22px]', 'h-[22px]');
  assert.ok(!classes(badge).includes('mt-px'), `${where}: the badge has no mt-px nudge`);
  const col = find(tags, 'opt-text');
  assert.ok(col > slot, `${where}: the label column follows the badge`);
  has(tags[col], 'min-w-0');
  // One rule, no one-line/multi-line switch.
  for (const no of ['justify-center', 'min-h-[22px]']) assert.ok(!classes(tags[col]).includes(no), `${where}: no ${no}`);
}

test('the rule lives in index.css and is driven by the line height (1lh)', () => {
  const css = read('index.css').replace(/\s+/g, ' ');
  assert.match(css, /\.opt-badge-slot \{ height: max\(22px, 1lh\); \}/);
  assert.match(css, /\.opt-text \{ padding-top: max\(0px, calc\(\(22px - 1lh\) \/ 2\)\); \}/);
});

test('answer option buttons: badge on the first line, the button centres its line in its height', () => {
  for (const parts of [PARTS, MULTI]) {
    const html = renderToStaticMarkup(h(QuestionCard, { title: 'dev', parts, onSubmit: async () => null, onDismiss: () => {} }));
    const opts = rows(html, 'data-option=');
    assert.equal(opts.length, 2);
    opts.forEach((tags, k) => {
      has(tags[0], 'flex', 'items-center', 'py-2');
      assert.ok(!classes(tags[0]).includes('items-start'), 'the button itself centres its line (phone 44px floor)');
      checkLine(tags.slice(1), `option ${k + 1}`);
    });
    assert.match(html, /Four equal sides/, 'the detail sub-line still shows');
    assert.match(html, /· agent leans/, 'the lean note still shows');
  }
});

test('answered card: chosen rows and the typed-words row follow the same rule', () => {
  const html = renderToStaticMarkup(h(AnsweredQuestionCard, {
    title: 'dev', parts: WORDS, answers: [{ ns: ['1', '2'], text: 'Bigger please', skipped: false }],
  }));
  const chosen = rows(html, 'data-chosen');
  assert.equal(chosen.length, 3, 'two chosen options and the words row');
  chosen.forEach((tags, k) => { has(tags[0], 'py-2'); checkLine(tags, `chosen row ${k + 1}`); });
  assert.match(html, /Bigger please/);
});

test('MultiPartCard has no mt-px badge nudge left', () => {
  assert.doesNotMatch(read('components/chat/MultiPartCard.tsx'), /\bmt-px\b/);
});
