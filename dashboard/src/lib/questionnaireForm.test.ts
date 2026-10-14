/**
 * Native rendering of DB ("live") questionnaires on the web (P1, 2026-10-14). The web showed them
 * in an iframe of /api/questionnaires/:id/html, which only knows legacy file questionnaires, so a
 * live one rendered as a title and nothing to answer. These pin the form's logic: which ones are
 * live, which options show, how answers are built, what counts as missing, drafts preloaded.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  isLiveQuestionnaire, answerableOptions, buildAnswers, missingRequired, initialAnswers,
  type LiveQuestion,
} from './questionnaireForm';

const Q: LiveQuestion[] = [
  { n: 1, prompt: 'Which day?', kind: 'menu', required: 1, answer: null,
    menu: { options: [
      { n: '1', label: 'Monday', input_kind: 'direct' },
      { n: '2', label: 'Other', input_kind: 'free_text' },
      { n: '3', label: 'Chat about this', input_kind: 'chat' },
    ] } },
  { n: 2, prompt: 'Anything else?', kind: 'free_text', required: 0, answer: null },
  { n: 3, prompt: 'Ship it?', kind: 'menu', required: 1, answer: { option_n: '1', answer_text: null, state: 'draft' },
    menu: { options: [{ n: '1', label: 'Yes' }, { n: '2', label: 'No' }] } },
];

test('a questionnaire is live when the API marks it, or by its qnr_ id; legacy ones are not', () => {
  assert.equal(isLiveQuestionnaire({ id: 'qnr_b0c2f42b_11457858' }), true);
  assert.equal(isLiveQuestionnaire({ id: 'x', source: 'live' }), true);
  assert.equal(isLiveQuestionnaire({ id: 'onboarding-2026-09' }), false);
});

test('chat options are not offered (the gateway refuses them in questionnaires)', () => {
  assert.deepEqual(answerableOptions(Q[0]).map((o) => o.label), ['Monday', 'Other']);
  assert.deepEqual(answerableOptions(Q[1]), []);
});

test('answers carry only what was given: an option, text, or both; blanks are dropped', () => {
  const a = buildAnswers({ 1: { option_n: '2', answer_text: '  Thursday ' }, 2: { answer_text: '   ' }, 3: { option_n: '1' } });
  assert.deepEqual(a, { '1': { option_n: '2', answer_text: 'Thursday' }, '3': { option_n: '1' } });
  assert.deepEqual(buildAnswers({ 2: { answer_text: 'just text' } }), { '2': { answer_text: 'just text' } });
});

test('a required question is answered by an option or by text alone; a free_text option needs its text', () => {
  assert.deepEqual(missingRequired(Q, {}), [1, 3]);
  assert.deepEqual(missingRequired(Q, { 1: { answer_text: 'whenever' }, 3: { option_n: '2' } }), []);
  assert.deepEqual(missingRequired(Q, { 1: { option_n: '2' }, 3: { option_n: '1' } }), [1]);   // "Other" with no text
  assert.deepEqual(missingRequired(Q, { 1: { option_n: '1' }, 3: { option_n: '1' } }), []);
});

test('a saved draft answer is preloaded', () => {
  assert.deepEqual(initialAnswers(Q), { 3: { option_n: '1' } });
});

test('a saved draft holding a hidden chat option or an unknown option is not carried into the form', () => {
  const qs: LiveQuestion[] = [
    { n: 1, prompt: 'p', kind: 'menu', required: 1, answer: { option_n: '3', answer_text: null, state: 'draft' },
      menu: { options: [{ n: '1', label: 'A', input_kind: 'direct' }, { n: '3', label: 'Chat', input_kind: 'chat' }] } },
    { n: 2, prompt: 'p', kind: 'menu', required: 1, answer: { option_n: '9', answer_text: null, state: 'draft' },
      menu: { options: [{ n: '1', label: 'A', input_kind: 'direct' }] } },
    { n: 3, prompt: 'p', kind: 'menu', required: 1, answer: { option_n: '1', answer_text: null, state: 'draft' },
      menu: { options: [{ n: '1', label: 'A', input_kind: 'direct' }] } },
  ] as any;
  const d = initialAnswers(qs);
  assert.deepEqual(buildAnswers(d), { '3': { option_n: '1' } });
  assert.deepEqual(missingRequired(qs, d), [1, 2]);       // shown as unanswered, so Send is blocked
});
