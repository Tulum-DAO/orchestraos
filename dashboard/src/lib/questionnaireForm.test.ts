/**
 * Native rendering of DB ("live") questionnaires on the web (P1, 2026-10-14). The web showed them
 * in an iframe of /api/questionnaires/:id/html, which only knows legacy file questionnaires, so a
 * live one rendered as a title and nothing to answer. These pin the form's logic: which ones are
 * live, which options show, how answers are built (skips included), drafts preloaded.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { isLiveQuestionnaire, questionnaireSummary, SUMMARY_MAX } from './questionnaireForm';
import {
  canSkip, partRows, questionnaireAnswers, questionnaireBody, questionnaireParts, skip as skipped, takesWords,
  type QuestionnaireQuestion,
} from './multiPart';

const Q: QuestionnaireQuestion[] = [
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

test('chat options are not offered (the gateway refuses them in questionnaires); a write-in row is the words field', () => {
  const parts = questionnaireParts(Q);
  assert.deepEqual(partRows(parts[0]).map((o) => o.label), ['Monday']);
  assert.equal(takesWords(parts[0]), true);
  assert.deepEqual(partRows(parts[1]), []);                     // a free-text question is words only
  assert.deepEqual(parts.map((p) => p.required), [true, false, true]);
});

test('answers carry what was given (a pick, words, or both); an optional question left unanswered goes as a skip', () => {
  const parts = questionnaireParts(Q);
  const body = questionnaireBody(parts, [
    { ns: [], text: '  Thursday ', skipped: false }, { ns: [], text: '', skipped: true }, { ns: ['1'], text: '', skipped: false },
  ]);
  assert.deepEqual(body, { '1': { answer_text: 'Thursday' }, '2': { skipped: true }, '3': { option_n: '1' } });
});

test('a required question is never sent as a skip, and the card offers no Skip on it', () => {
  const parts = questionnaireParts(Q);
  assert.deepEqual(questionnaireBody(parts, [skipped(), skipped(), skipped()]), { '2': { skipped: true } });
  assert.equal(canSkip(parts, { answers: [], at: 0 }), false);
  assert.equal(canSkip(parts, { answers: [], at: 1 }), true);
});

test('a saved draft answer is preloaded; a stored skip shows as skipped', () => {
  const parts = questionnaireParts(Q);
  assert.deepEqual(questionnaireAnswers(parts, Q).map((a) => [a.ns, a.text, a.skipped]),
    [[[], '', false], [[], '', false], [['1'], '', false]]);
  const qs = Q.map((q) => (q.n === 2 ? { ...q, answer: { option_n: null, answer_text: null, state: 'skipped' } } : q));
  assert.equal(questionnaireAnswers(parts, qs)[1].skipped, true);
});

test('a saved draft holding a hidden chat option or an unknown option is not carried into the form', () => {
  const qs: QuestionnaireQuestion[] = [
    { n: 1, prompt: 'p', kind: 'menu', required: 1, answer: { option_n: '3', answer_text: null, state: 'draft' },
      menu: { options: [{ n: '1', label: 'A', input_kind: 'direct' }, { n: '3', label: 'Chat', input_kind: 'chat' }] } },
    { n: 2, prompt: 'p', kind: 'menu', required: 1, answer: { option_n: '9', answer_text: null, state: 'draft' },
      menu: { options: [{ n: '1', label: 'A', input_kind: 'direct' }] } },
    { n: 3, prompt: 'p', kind: 'menu', required: 1, answer: { option_n: '1', answer_text: null, state: 'draft' },
      menu: { options: [{ n: '1', label: 'A', input_kind: 'direct' }] } },
  ];
  const parts = questionnaireParts(qs);
  const answers = questionnaireAnswers(parts, qs);
  assert.deepEqual(questionnaireBody(parts, answers), { '3': { option_n: '1' } });   // 1 and 2 shown as unanswered
});

test('the context above the questions is the summary (notes are appended to it); empty or title-only shows nothing', () => {
  const summary = '**Decide by: Fri 18:00 ET.**\n\nWhere things stand: the list is ready.\n\n— NOTE 2026-10-14T21:00:00Z by pm-demo: tight list.';
  assert.equal(questionnaireSummary({ title: 'Audience refresh', summary }), summary);
  assert.equal(questionnaireSummary({ title: 'Audience refresh', summary: '  Audience refresh ' }), '');
  assert.equal(questionnaireSummary({ title: 'Audience refresh', summary: null }), '');
  assert.equal(questionnaireSummary({ title: 'Audience refresh' }), '');
});

test('a huge summary is cut at SUMMARY_MAX with a visible note, so it cannot freeze the page or bury the questions', () => {
  const out = questionnaireSummary({ title: 't', summary: 'x\n'.repeat(200_000) });
  assert.ok(out.length <= SUMMARY_MAX + 120, String(out.length));
  assert.match(out, /\n\n\*\(summary truncated[^*]*\)\*$/);   // *...*: the italics Markdown.tsx renders
  assert.ok(!/truncated/.test(questionnaireSummary({ title: 't', summary: 'short' })));
});

test('a cut inside a code block closes the block, so the truncation note stays outside it', () => {
  const out = questionnaireSummary({ title: 't', summary: 'intro\n```\n' + 'line\n'.repeat(5000) + '```\nafter' });
  assert.equal((out.match(/^```/gm) || []).length % 2, 0);
  assert.match(out, /```\n\n\*\(summary truncated[^*]*\)\*$/);
});

test('an indented ``` is not a fence (the renderer reads column 0 only), so no stray fence is added', () => {
  const out = questionnaireSummary({ title: 't', summary: 'intro\n  ```\n' + 'line\n'.repeat(5000) });
  assert.equal((out.match(/^```/gm) || []).length, 0);
  assert.match(out, /[^`]\n\n\*\(summary truncated[^*]*\)\*$/);
});
