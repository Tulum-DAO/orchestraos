import { test } from 'node:test';
import assert from 'node:assert/strict';
import { choiceAnswerText, isCardEcho } from './choiceCard.ts';
import { choiceAnswerFromText, choose, onePart, partRows, startState, takesWords } from './multiPart.ts';

const card = (multi: boolean) => onePart('Pick', ['Mac', 'iPhone', 'None of these'].map((label, i) => ({ n: String(i + 1), label })),
  { words: true, multi, exclusive: multi ? '3' : undefined });

test('options are lettered rows, words alone can always be sent, and a single pick sends at once', () => {
  const parts = card(false);
  assert.deepEqual(partRows(parts[0]).map((o) => [o.n, o.label]), [['1', 'Mac'], ['2', 'iPhone'], ['3', 'None of these']]);
  assert.equal(takesWords(parts[0]), true);
  assert.equal(choose(parts, startState(parts), '2').submit, true);
});

test('"None of these" stands alone: picking it clears the rest, picking another clears it', () => {
  const parts = card(true);
  let s = choose(parts, startState(parts), '1').state;
  s = choose(parts, s, '2').state;
  assert.deepEqual(s.answers[0].ns, ['1', '2']);
  s = choose(parts, s, '3').state;
  assert.deepEqual(s.answers[0].ns, ['3']);
  s = choose(parts, s, '1').state;
  assert.deepEqual(s.answers[0].ns, ['1']);
});

test('an answered card reads back what it sent, words as words', () => {
  const parts = card(true);
  assert.deepEqual(choiceAnswerFromText(parts[0], 'Mac, iPhone'), { ns: ['1', '2'], text: '', skipped: false });
  assert.deepEqual(choiceAnswerFromText(parts[0], 'Mac — only the work one'), { ns: ['1'], text: 'only the work one', skipped: false });
  assert.deepEqual(choiceAnswerFromText(parts[0], 'something else entirely'), { ns: [], text: 'something else entirely', skipped: false });
});

test('what is sent: the picks in the card\'s order, the words after them, or the words alone', () => {
  assert.equal(choiceAnswerText(['Mac', 'iPhone', 'Watch'], ['Watch', 'Mac'], ''), 'Mac, Watch');
  assert.equal(choiceAnswerText(['Yes', 'No'], ['Yes'], ' but later '), 'Yes — but later');
  assert.equal(choiceAnswerText(['Yes', 'No'], [], 'neither'), 'neither');
  assert.equal(choiceAnswerText(['Yes', 'No'], [], '  '), '');
});

test('a card\'s own pick is not echoed as a chat bubble; words alone stay the operator\'s message', () => {
  const card = { options: ['Alpha', 'Beta', 'None of these'], multi: true, exclusive: 'None of these' };
  assert.equal(isCardEcho(card, 'Beta'), true);
  assert.equal(isCardEcho(card, 'Alpha, Beta'), true);
  assert.equal(isCardEcho(card, 'Alpha — only the small one'), true);
  assert.equal(isCardEcho(card, 'something else entirely'), false);
  assert.equal(isCardEcho({ options: ['Yes', 'No'] }, 'Yes please'), false);
});
