import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  splitRefs, hasRefs, isCardId, scrubIds, firstWords, chipLabel, chipState, describeCard, placeholderLabel,
  summaryFromCard, summaryFromUnified, summaryFromQuestionnaire, refState, type CardSummary,
} from './cardRefs.ts';
import type { Decision } from './decisions.ts';

const APR = 'apr_5a3b1c2d_12345678';
const QNR = 'qnr_0a1b2c3d_1234';

test('card ids in text come out as references, in order, with the words around them kept', () => {
  assert.deepEqual(splitRefs(`Filed ${APR}, then ${QNR}.`), [
    { t: 'text', text: 'Filed ' },
    { t: 'ref', ref: { type: 'card', id: APR } },
    { t: 'text', text: ', then ' },
    { t: 'ref', ref: { type: 'card', id: QNR } },
    { t: 'text', text: '.' },
  ]);
  assert.deepEqual(splitRefs(APR), [{ t: 'ref', ref: { type: 'card', id: APR } }]);
  assert.deepEqual(splitRefs(`and \`${APR}\`.`), [{ t: 'text', text: 'and ' }, { t: 'ref', ref: { type: 'card', id: APR } }, { t: 'text', text: '.' }]);
  assert.deepEqual(splitRefs(`\`${APR} x`).map((p) => p.t), ['text', 'ref', 'text']);
  assert.deepEqual(splitRefs('no card here'), [{ t: 'text', text: 'no card here' }]);
  assert.deepEqual(splitRefs(''), [{ t: 'text', text: '' }]);
});

test('only the ids\' own shape is a reference: not inside a word, not a near miss', () => {
  for (const s of ['xapr_5a3b1c2d_12345678', 'apr_5a3b1c2d', 'apr_B0FAAEAD_1', 'apr_b0faaea_1', 'apr_5a3b1c2d_x', 'prop_5a3b1c2d_1', 'apr_5a3b1c2d_12345678_extra']) {
    assert.equal(hasRefs(s), false, s);
  }
  assert.equal(hasRefs(`(${APR})`), true);
  assert.equal(hasRefs(`[DECISION ANSWERED ${APR}]`), true);
  assert.equal(isCardId(` ${QNR} `), true);
  assert.equal(isCardId(`${QNR}.`), false);
});

test('ids left in words for a person or a voice are said as "a card"', () => {
  assert.equal(scrubIds(`Answer ${APR} first`), 'Answer a card first');
});

test('the chip: the question\'s first words and the live state, never the id', () => {
  const c: CardSummary = { id: APR, kind: 'decision', from: 'pm-demo', state: 'answered', question: 'Which size?', answer: 'Circle', options: [], more: 0 };
  assert.equal(chipLabel(c), 'Which size? · Answered: Circle');
  assert.equal(chipLabel({ ...c, kind: 'approval', state: 'waiting', question: 'Deploy the drawer?', answer: null }), 'Deploy the drawer? · Waiting');
  assert.equal(chipState({ state: 'dismissed', answer: null }), 'Dismissed');
  assert.equal(chipState({ state: 'answered', answer: null }), 'Answered');
  const long = chipLabel({ ...c, question: 'Should the sample report go out to every example reader this afternoon?', answer: 'Yes, but only after the checks run' });
  assert.equal(long, 'Should the sample report go out to every… · Answered: Yes, but only after the…');
  assert.ok(!chipLabel({ ...c, question: `Close ${APR}?` }).includes('apr_'));
  assert.equal(placeholderLabel(QNR), 'Questionnaire');
  assert.equal(placeholderLabel(APR), 'Card');
});

test('first words cut at a word and drop Markdown marks', () => {
  assert.equal(firstWords('**Which** `size`?'), 'Which size?');
  assert.equal(firstWords('one two three four', 9), 'one two…');
});

const row = (over: Partial<Decision> & Record<string, unknown>) => ({
  id: APR, from_agent: 'pm-demo', kind: 'menu', state: 'pending', question: 'Which size?', summary: null, options: [],
  menu: { options: [{ n: '1', label: 'Circle' }, { n: '2', label: 'Square' }, { n: '3', label: 'Type something.' }] },
  risk_level: null, reversibility: null, answer: null, answer_text: null, option_n: null, answered_by: null,
  answer_surface: null, answer_device: null, created_at: '2026-10-30T10:00:00Z', answered_at: null, expires_at: null, ...over,
} as Decision & { batch?: never });

test('a card looked up by id: a decision, an approval, a recorded multi-part answer', () => {
  const waiting = summaryFromCard(row({}));
  assert.deepEqual(waiting, { id: APR, kind: 'decision', from: 'pm-demo', state: 'waiting', question: 'Which size?', answer: null,
    options: ['Circle', 'Square'], more: 0 });
  const picked = summaryFromCard(row({ state: 'answered', option_n: '1', answer_text: 'the big one' }));
  assert.equal(picked.answer, 'Circle — the big one');
  const approval = summaryFromCard(row({ kind: 'approval', question: 'Deploy the drawer?', menu: null, state: 'answered', answer: 'deny', answer_text: 'not today' }));
  assert.equal(approval.kind, 'approval');
  assert.equal(approval.answer, 'Denied — not today');
  const batch = {
    id: APR, answered_at: '2026-10-30T10:01:00Z',
    parts: [{ index: 0, question: 'Which size?', select: 'single' as const, options: [{ n: '1', label: 'Circle' }] },
            { index: 1, question: 'Which colour?', select: 'single' as const, options: [{ n: '1', label: 'Blue' }] }],
    answers: [{ part: 0, ns: ['1'] }, { part: 1, skipped: true }],
  };
  const multi = summaryFromCard({ ...row({ state: 'answered', answer: 'batch' }), batch } as never);
  assert.equal(multi.kind, 'multipart');
  assert.equal(multi.more, 1);
  assert.equal(multi.answer, 'Circle; Skipped');
  assert.equal(chipLabel(multi), 'Which size? · Answered: Circle; Skipped');
  assert.equal(describeCard(multi), 'pm-demo asked a question: Which size? (and 1 more question) Answered: Which size? Circle; Which colour? Skipped.');
});

test('a waiting approvals row and a questionnaire', () => {
  assert.deepEqual(summaryFromUnified({ id: APR, status: 'pending', title: 'Deploy the drawer?', kind: null, agent_id: 'dev-demo' }),
    { id: APR, kind: 'approval', from: 'dev-demo', state: 'waiting', question: 'Deploy the drawer?', answer: null, options: [], more: 0 });
  assert.equal(summaryFromUnified({ id: APR, status: 'pending', title: 'x', kind: 'menu', menu: { multipart: true } }).kind, 'multipart');
  const q = summaryFromQuestionnaire({ id: QNR, title: 'Sample survey', status: 'submitted', from_agent: 'qa-demo', questions: [
    { prompt: 'Which size?', menu: { options: [{ n: '1', label: 'Circle' }, { n: '2', label: 'Square' }] }, answer: { option_n: '2', answer_text: null } },
    { prompt: 'Anything else?', answer: { option_n: null, answer_text: null, state: 'skipped' } },
  ] });
  assert.equal(q.state, 'answered');
  assert.equal(q.answer, 'Square');
  assert.equal(q.more, 1);
  assert.equal(refState('superseded'), 'replaced');
  assert.equal(refState('discarded'), 'dismissed');
});

test('the card in words for a call or a notification: question, state, answer or what is asked; never an id', () => {
  const base: CardSummary = { id: APR, kind: 'decision', from: 'pm-demo', state: 'waiting', question: 'Which size?', answer: null, options: ['Circle', 'Square', 'Triangle'], more: 0 };
  assert.equal(describeCard(base), 'pm-demo asked a question: Which size? It is waiting for your answer. The options are Circle, Square or Triangle.');
  assert.equal(describeCard({ ...base, state: 'answered', answer: 'Circle' }), 'pm-demo asked a question: Which size? Answered: Circle.');
  assert.equal(describeCard({ ...base, kind: 'approval', from: 'dev-demo', question: 'Deploy the drawer', options: [] }),
    'dev-demo asked for approval: Deploy the drawer. It is waiting for you to approve or deny it.');
  assert.equal(describeCard({ ...base, kind: 'multipart', more: 2, options: [] }), 'pm-demo asked a question: Which size? (and 2 more questions) It is waiting for your answer.');
  assert.equal(describeCard({ ...base, state: 'dismissed' }), 'pm-demo asked a question: Which size? It was dismissed without an answer.');
  const leaky = describeCard({ ...base, question: `Close ${APR}?`, state: 'answered', answer: `Done with ${QNR}` });
  assert.ok(!/apr_|qnr_/.test(leaky), leaky);
});
