import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  answerFromStored, back, canNext, canSkip, captureProblem, cardKey, cardNote, choose, decisionParts, questionnaireAnswers,
  questionnaireBody, questionnaireParts, type QuestionnaireQuestion, onePart, singleBody, fromAskUserQuestion, letter, matchBatchRecord, next, nothingAnswered, outcome, partRows,
  setWords, skipPart, startState, submitAnswers, takesWords, type CardState, type MenuPart, type Step,
} from './multiPart.ts';

const PARTS: MenuPart[] = [
  { index: 0, question: 'Which colour?', select: 'single', options: [{ n: '1', label: 'Blue' }, { n: '2', label: 'Green' }, { n: '3', label: 'Type something.', input_kind: 'free_text' }], has_free_text: true },
  { index: 1, question: 'Which sizes?', select: 'multi', options: [{ n: '1', label: 'Small' }, { n: '2', label: 'Medium' }, { n: '3', label: 'Large' }] },
  { index: 2, question: 'Which finish?', select: 'single', options: [{ n: '1', label: 'Matte' }, { n: '2', label: 'Gloss' }] },
];

/** Runs clicks on a fresh card and returns every submit it would send. */
function run(steps: ((s: CardState) => Step | CardState)[]) {
  let s = startState(PARTS);
  const sent: ReturnType<typeof submitAnswers>[] = [];
  for (const f of steps) {
    const r = f(s);
    if ('submit' in r) { s = r.state; if (r.submit) sent.push(submitAnswers(PARTS, s.answers)); } else s = r;
  }
  return { s, sent };
}
const pick = (n: string) => (s: CardState) => choose(PARTS, s, n);
const skipIt = (s: CardState) => skipPart(PARTS, s);
const nextIt = (s: CardState) => next(PARTS, s);

test("rows leave out the CLI's own write-in row; badges are letters; words only where the part has a write-in", () => {
  assert.deepEqual(partRows(PARTS[0]).map((o) => o.label), ['Blue', 'Green']);
  assert.deepEqual([0, 1, 2].map(letter), ['A', 'B', 'C']);
  assert.equal(takesWords(PARTS[0]), true);
  assert.equal(takesWords(PARTS[1]), false);
});

test('answer all three: a pick moves on, ticks wait for Next, the last pick sends once', () => {
  const { s, sent } = run([pick('2'), pick('1'), pick('3'), nextIt, pick('2')]);
  assert.equal(s.at, 2);
  assert.deepEqual(sent, [[{ part: 0, ns: ['2'] }, { part: 1, ns: ['1', '3'] }, { part: 2, ns: ['2'] }]]);
});

test('skip 1, answer 2 and 3: the skipped part goes empty', () => {
  const { sent } = run([skipIt, pick('2'), nextIt, pick('1')]);
  assert.deepEqual(sent, [[{ part: 0, skipped: true }, { part: 1, ns: ['2'] }, { part: 2, ns: ['1'] }]]);
});

test('answer only 3: sends at once with the first two empty', () => {
  const { sent } = run([skipIt, skipIt, pick('1')]);
  assert.deepEqual(sent, [[{ part: 0, skipped: true }, { part: 1, skipped: true }, { part: 2, ns: ['1'] }]]);
});

test('Back and change 1: the old pick is still marked, the new one replaces it, and it sends once at the end', () => {
  const { s, sent } = run([pick('1'), pick('2'), nextIt, back, back]);
  assert.equal(s.at, 0);
  assert.deepEqual(s.answers[0].ns, ['1']);              // still marked
  assert.equal(sent.length, 0);
  const after = run([pick('1'), pick('2'), nextIt, back, back, pick('2'), nextIt, pick('1')]);
  assert.deepEqual(after.sent, [[{ part: 0, ns: ['2'] }, { part: 1, ns: ['2'] }, { part: 2, ns: ['1'] }]]);
});

test('a multi-select part never moves on from a tick, and Next waits for one', () => {
  const { s } = run([pick('1'), pick('3')]);
  assert.equal(s.at, 1);
  assert.equal(canNext(s), true);
  const untick = run([pick('1'), pick('3'), pick('3')]);
  assert.equal(canNext(untick.s), false);
  assert.equal(next(PARTS, untick.s).state.at, 1);
});

test('skipping everything sends nothing', () => {
  const { s, sent } = run([skipIt, skipIt, skipIt]);
  assert.equal(sent.length, 0);
  assert.equal(nothingAnswered(s), true);
});

test('on a single-choice part a pick and own words are exclusive: picking clears the words, typing clears the pick', () => {
  const alone = run([(s) => setWords(PARTS, s, ' teal '), nextIt, skipIt, pick('2')]);
  assert.deepEqual(alone.sent[0][0], { part: 0, ns: [], text: 'teal' });
  const pickAfterWords = run([(s) => setWords(PARTS, s, 'darker'), pick('1'), skipIt, pick('2')]);
  assert.deepEqual(pickAfterWords.sent[0][0], { part: 0, ns: ['1'] });
  const wordsAfterPick = run([pick('1'), back, (s) => setWords(PARTS, s, 'darker'), nextIt, skipIt, pick('2')]);
  assert.deepEqual(wordsAfterPick.sent[0][0], { part: 0, ns: [], text: 'darker' });
});

test('an answered part shows only what was chosen; a skipped one says so', () => {
  const { s } = run([(x) => setWords(PARTS, x, 'darker'), nextIt, skipIt]);
  assert.deepEqual(outcome(PARTS[0], s.answers[0]), { labels: [], words: 'darker', skipped: false });
  assert.deepEqual(outcome(PARTS[1], s.answers[1]), { labels: [], words: '', skipped: true });
});

const ASKED = { questions: [
  { question: 'Which colour?', options: [{ label: 'Blue' }, { label: 'Green', description: 'Softer' }] },
  { question: 'Which sizes?', multiSelect: true, options: [{ label: 'Small' }, { label: 'Large' }] },
  { question: 'Which finish?', options: [{ label: 'Matte' }] },
] };
const rows = (r: ReturnType<typeof fromAskUserQuestion>) => r!.answers.map((a) => [a.ns, a.text, a.skipped]);

test("an answered AskUserQuestion in the transcript reads back into the card's parts", () => {
  const r = fromAskUserQuestion(ASKED, 'User has answered your questions: "Which colour?"="a darker blue", "Which sizes?"="Small, Large". You can now continue.');
  assert.ok(r);
  assert.equal(r.parts[1].select, 'multi');
  assert.equal(r.parts[0].options[1].detail, 'Softer');
  assert.deepEqual(rows(r), [[[], 'a darker blue', false], [['1', '2'], '', false], [[], '', true]]);
  assert.equal(fromAskUserQuestion(ASKED, undefined), null);
  assert.equal(fromAskUserQuestion({}, 'x'), null);
});

test('an option label containing a comma stays whole', () => {
  const input = { questions: [{ question: 'Which flags?', multiSelect: true,
    options: [{ label: 'Red, white and blue' }, { label: 'Red' }, { label: 'Green' }] }] };
  const r = fromAskUserQuestion(input, 'User has answered your questions: "Which flags?"="Red, white and blue, Green". Go on.');
  assert.deepEqual(rows(r), [[['1', '3'], '', false]]);
  const single = { questions: [{ question: 'Which one?', options: [{ label: 'Yes, ship it' }, { label: 'No' }] }] };
  assert.deepEqual(rows(fromAskUserQuestion(single, '"Which one?"="Yes, ship it".')), [[['1'], '', false]]);
});

test('a label or a question containing a quote is read by the question texts, not split at the quote', () => {
  const input = { questions: [
    { question: 'Use the "fast" path?', options: [{ label: 'Say "yes"' }, { label: 'No' }] },
    { question: 'Which size?', options: [{ label: 'Small' }, { label: 'Large' }] },
  ] };
  const r = fromAskUserQuestion(input, 'User has answered your questions: "Use the "fast" path?"="Say "yes"", "Which size?"="Large". You can now continue.');
  assert.deepEqual(rows(r), [[['1'], '', false], [['2'], '', false]]);
});

test('two parts with the same question text: nothing to draw (the CLI refuses that call)', () => {
  const input = { questions: [{ question: 'Same?', options: [{ label: 'A' }] }, { question: 'Same?', options: [{ label: 'B' }] }] };
  assert.equal(fromAskUserQuestion(input, '"Same?"="A", "Same?"="B".'), null);
});

test("the durable record's answers win over the result text, skips included", () => {
  const record = { id: 'apr_sample', answered_at: '2026-10-29T10:00:00Z',
    parts: [
      { index: 0, question: 'Which colour?', select: 'single' as const, options: [{ n: '1', label: 'Blue' }, { n: '2', label: 'Green' }, { n: '3', label: 'Type something.', input_kind: 'free_text' }] },
      { index: 1, question: 'Which sizes?', select: 'multi' as const, options: [{ n: '1', label: 'Small' }, { n: '2', label: 'Large' }] },
      { index: 2, question: 'Which finish?', select: 'single' as const, options: [{ n: '1', label: 'Matte' }] },
    ],
    answers: [{ part: 0, ns: [], text: 'teal' }, { part: 1, skipped: true }, { part: 2, ns: ['1'] }] };
  // a result text that would read differently: the record is what the person sent
  const r = fromAskUserQuestion(ASKED, '"Which colour?"="Blue".', record);
  assert.deepEqual(rows(r), [[[], 'teal', false], [[], '', true], [['1'], '', false]]);
});

test('a durable record is matched by its questions, the newest first', () => {
  const part = (q: string) => ({ index: 0, question: q, select: 'single' as const, options: [] });
  const recs = [
    { id: 'old', answered_at: '2026-10-28T10:00:00Z', parts: [part('Which colour?'), part('Which sizes?'), part('Which finish?')], answers: [] },
    { id: 'new', answered_at: '2026-10-29T10:00:00Z', parts: [part('which colour'), part('Which sizes?'), part('Which finish?')], answers: [] },
    { id: 'other', answered_at: '2026-10-29T11:00:00Z', parts: [part('Something else?')], answers: [] },
  ];
  assert.equal(matchBatchRecord(recs, ['Which colour?', 'Which sizes?', 'Which finish?'])?.id, 'new');
  assert.equal(matchBatchRecord(recs, ['Unrelated?']), null);
});

test('why a menu could not be read', () => {
  assert.match(captureProblem('not_on_part_zero'), /first part/);
  assert.match(captureProblem('menu_gone'), /gone/);
  assert.match(captureProblem(undefined), /Try again/);
});

const MENU = onePart('Ship the sample?', [
  { n: '1', label: 'Yes' }, { n: '2', label: 'No' }, { n: '3', label: 'Other', input_kind: 'free_text' },
], { words: true, lean: '1' });

test('a single question is a card of one part: a pick sends at once, there is no Skip, the write-in row is the field', () => {
  assert.deepEqual(partRows(MENU[0]).map((o) => o.label), ['Yes', 'No']);
  assert.equal(canSkip(MENU, startState(MENU)), false);
  assert.equal(skipPart(MENU, startState(MENU)).state.answers[0].skipped, false);
  const r = choose(MENU, startState(MENU), '2');
  assert.equal(r.submit, true);
  assert.deepEqual(singleBody(r.state.answers[0]), { option_n: '2' });
});

test('a one-part card sends a pick or words, never both', () => {
  let s = setWords(MENU, startState(MENU), ' later please ');
  assert.deepEqual(singleBody(s.answers[0]), { answer_text: 'later please' });
  s = choose(MENU, s, '1').state;
  assert.deepEqual(singleBody(s.answers[0]), { option_n: '1' });
  assert.equal(singleBody({ ns: [], text: ' ', skipped: false }), null);
});

test('an answered decision reads back from what was stored; a write-in number with words is the words', () => {
  assert.deepEqual(answerFromStored(MENU[0], '2', null), { ns: ['2'], text: '', skipped: false });
  assert.deepEqual(answerFromStored(MENU[0], '3', 'next week'), { ns: [], text: 'next week', skipped: false });
  assert.deepEqual(answerFromStored(MENU[0], null, 'next week'), { ns: [], text: 'next week', skipped: false });
  assert.equal(answerFromStored(MENU[0], null, null).skipped, true);
});

test('an answered part shows typed words even where the part had no write-in (an older client sent them)', () => {
  assert.deepEqual(outcome(PARTS[1], { ns: ['1'], text: 'and XL', skipped: false }), { labels: ['Small'], words: 'and XL', skipped: false });
});

test('keys while the card has focus: A–Z pick on the part on show, Return moves on; a field, a button or a modifier keeps its key', () => {
  const s0 = startState(PARTS);
  assert.deepEqual(cardKey(PARTS, s0, 'b')!.state.answers[0].ns, ['2']);
  assert.equal(cardKey(PARTS, s0, 'B')!.state.at, 1);                 // a pick on a single-choice part moves on
  assert.equal(cardKey(PARTS, s0, 'C'), null);                        // the write-in row is the field, not a letter
  assert.equal(cardKey(PARTS, s0, 'a', { inField: true }), null);
  assert.equal(cardKey(PARTS, s0, 'Enter', { onButton: true }), null);
  assert.equal(cardKey(PARTS, s0, 'a', { modifier: true }), null);
  assert.equal(cardKey(PARTS, s0, 'Enter')!.state.at, 0);             // nothing answered: Return does not move on
  const answered1 = choose(PARTS, s0, '1').state;
  assert.equal(cardKey(PARTS, back(answered1), 'Enter')!.state.at, 1);
});

test('a pick-any part keeps typed words when a tick is added or removed, and sends both', () => {
  const multi = onePart('Which sizes?', [{ n: '1', label: 'Small' }, { n: '2', label: 'Large' }, { n: '3', label: 'Type something.', input_kind: 'free_text' }],
    { words: true, multi: true });
  let s = setWords(multi, startState(multi), 'and an XL');
  s = choose(multi, s, '1').state;
  s = choose(multi, s, '2').state;
  s = choose(multi, s, '2').state;
  assert.deepEqual(s.answers[0], { ns: ['1'], text: 'and an XL', skipped: false });
  assert.deepEqual(submitAnswers(multi, s.answers), [{ part: 0, ns: ['1'], text: 'and an XL' }]);
});

test('a card that does not block an agent takes a pick and words together; a terminal menu takes one or the other', () => {
  const posted = decisionParts({ question: 'Ship it?', options: [{ n: '1', label: 'Yes' }, { n: '2', label: 'No' }] });
  let s = setWords(posted, startState(posted), 'but after lunch');
  const r = choose(posted, s, '1');
  assert.equal(r.submit, true);
  assert.deepEqual(singleBody(r.state.answers[0]), { option_n: '1', answer_text: 'but after lunch' });
  s = setWords(posted, choose(posted, startState(posted), '2').state, 'and say why');   // typing keeps the pick
  assert.deepEqual(s.answers[0].ns, ['2']);
  const terminal = decisionParts({ question: 'Ship it?', terminal: true,
    options: [{ n: '1', label: 'Yes' }, { n: '2', label: 'Type something.' }] });
  const t = choose(terminal, setWords(terminal, startState(terminal), 'but after lunch'), '1');
  assert.deepEqual(singleBody(t.state.answers[0]), { option_n: '1' });                   // the pick replaced the words
  assert.equal(takesWords(decisionParts({ question: 'Ok?', terminal: true, options: [{ n: '1', label: 'Yes' }] })[0]), false);
  assert.equal(takesWords(posted[0]), true);                                             // a posted card always takes a note
});

test('a questionnaire question takes a pick and words together, and keeps both from a saved draft', () => {
  const qs: QuestionnaireQuestion[] = [{ n: 1, prompt: 'Which day?', kind: 'menu', required: 1,
    answer: { option_n: '1', answer_text: 'morning', state: 'draft' }, menu: { options: [{ n: '1', label: 'Monday' }] } }];
  const parts = questionnaireParts(qs);
  const answers = questionnaireAnswers(parts, qs);
  assert.deepEqual(answers[0], { ns: ['1'], text: 'morning', skipped: false });
  assert.deepEqual(questionnaireBody(parts, answers), { '1': { option_n: '1', answer_text: 'morning' } });
});

test("the can't-send-yet note shows only while the card waits for input, never next to Sending", () => {
  const waiting = 'You can answer in a moment, once this question reaches the server.';
  assert.equal(cardNote({ phase: 'answer', waiting }), waiting);
  assert.equal(cardNote({ phase: 'sending', waiting }), null);
  assert.equal(cardNote({ phase: 'done', waiting }), null);
  assert.equal(cardNote({ phase: 'sending', problem: 'Couldn’t send.', waiting }), 'Couldn’t send.');
  assert.equal(cardNote({ phase: 'answer', emptyNote: 'Nothing to send yet.', waiting }), 'Nothing to send yet.');
});
