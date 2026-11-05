import { test } from 'node:test';
import assert from 'node:assert/strict';
import { hydrateTurns } from './arturoResume.ts';
import { cardAnswer, storedAnswerText } from './choiceCard.ts';
import { buildTextBody, cardRefusal } from './arturo.ts';
import { choiceQuestion, fromChoiceTurn, historyItems, groupHistory } from './drawerHistory.ts';
import type { ThreadTurn } from './arturoThreads.ts';

const CARD = '0a1b2c3d4e5f6a7b';  // pragma: allowlist secret
const choices = { options: ['Circle', 'Square', 'Triangle'], multi: false, purpose: 'other' as const };
const cardTurn = (meta: Partial<NonNullable<ThreadTurn['meta']>> = {}): ThreadTurn => ({
  role: 'assistant', content: 'Here are three shapes.\nWhich shape should the sample use?', ts: 100,
  meta: { kind: 'card', card_id: CARD, choices, ...meta },
});

test('a tap sends the card it answers: the picks in the card\'s order, and the words', () => {
  assert.deepEqual(cardAnswer(CARD, ['A', 'B', 'C'], ['C', 'A'], '  both  '), { card_id: CARD, picks: ['A', 'C'], words: 'both' });
  assert.deepEqual(buildTextBody('Circle', 'web_x', null, undefined, 't_1', { card_id: CARD, picks: ['Circle'], words: '' }),
    { text: 'Circle', conversation_id: 'web_x', turn_id: 't_1', card_answer: { card_id: CARD, picks: ['Circle'], words: '' } });
  assert.equal('card_answer' in buildTextBody('hi', 'web_x'), false);
});

test('a stored answer reads as the words a tap sent', () => {
  assert.equal(storedAnswerText(choices.options, { picks: ['Square'] }), 'Square');
  assert.equal(storedAnswerText(choices.options, { picks: ['Square'], words: 'a big one' }), 'Square — a big one');
  assert.equal(storedAnswerText(choices.options, { picks: [], words: 'neither' }), 'neither');
  assert.equal(storedAnswerText(choices.options, null), '');
});

test('on reload the card shows its own answer from the server, whatever message followed it', () => {
  const turns: ThreadTurn[] = [
    cardTurn({ answer: { picks: ['Square'], at: 101 } }),
    { role: 'user', content: 'Square', ts: 101 },
    { role: 'assistant', content: 'Square it is.', ts: 102 },
    { role: 'user', content: 'Something else entirely', ts: 103 },
  ];
  const h = hydrateTurns(turns);
  assert.equal(h[0].answered, 'Square');
  assert.equal(h[0].answeredOnServer, true);
  assert.equal(h[1].cardEcho, true);                 // the tap is drawn on the card, not as a bubble
});

test('a card from before answers were kept is still answered by the message after it', () => {
  const h = hydrateTurns([cardTurn(), { role: 'user', content: 'Triangle', ts: 101 }]);
  assert.equal(h[0].answered, 'Triangle');
  assert.equal(h[0].answeredOnServer, undefined);
  const stored = hydrateTurns([cardTurn({ answer: { picks: [], words: 'none of these' } }), { role: 'user', content: 'Triangle', ts: 101 }]);
  assert.equal(stored[0].answered, 'none of these');  // the server's record wins over the next message
});

test('a send refused because its card was already answered, closed or unknown says which, with the stored answer', () => {
  assert.deepEqual(cardRefusal({ ok: false, error: 'card_answered', answer: { picks: ['Circle'], words: 'w' } }),
    { kind: 'answered', answer: { picks: ['Circle'], words: 'w' } });
  assert.deepEqual(cardRefusal({ ok: false, error: 'card_dismissed' }), { kind: 'dismissed' });
  assert.deepEqual(cardRefusal({ ok: false, error: 'card_unknown' }), { kind: 'unknown' });
  assert.equal(cardRefusal({ ok: false, error: 'busy' }), null);
  assert.equal(cardRefusal({ ok: true }), null);
});

test('Arturo\'s choice cards in History: waiting, answered with when, or dismissed', () => {
  const waiting = fromChoiceTurn(cardTurn(), 'Sample')!;
  assert.equal(waiting.kind, 'choice');
  assert.equal(waiting.state, 'waiting');
  assert.equal(waiting.question, 'Which shape should the sample use?');
  assert.equal(waiting.agent, 'Sample');
  const answered = fromChoiceTurn(cardTurn({ answer: { picks: ['Circle'], words: 'round', at: 150 } }), 'Sample')!;
  assert.deepEqual([answered.state, answered.outcome, answered.answeredAt, answered.by], ['resolved', 'answered', 150_000, 'you']);
  assert.deepEqual(answered.answer, [{ text: 'Circle — round', skipped: false }]);
  const closed = fromChoiceTurn(cardTurn({ dismissed: true }), 'Sample')!;
  assert.deepEqual([closed.state, closed.outcome, closed.answer], ['resolved', 'dismissed', []]);
  assert.equal(fromChoiceTurn({ role: 'assistant', content: 'plain reply', ts: 1 }, 'Sample'), null);
  assert.equal(fromChoiceTurn({ role: 'user', content: 'x', ts: 1, meta: cardTurn().meta }, 'Sample'), null);
});

test('History lists the open conversation\'s choice cards beside the other feeds', () => {
  const items = historyItems([], [], 'arturo', [cardTurn(), { role: 'user', content: 'Circle', ts: 101 }], 'Sample');
  assert.equal(items.length, 1);
  assert.equal(groupHistory(items).waiting[0].id, CARD);
});

test('a choice card\'s question comes from the reply that showed it', () => {
  assert.equal(choiceQuestion('Two options.\n**Which one do you want?** Pick below.'), 'Which one do you want?');
  assert.equal(choiceQuestion('Pick a layout.'), 'Pick a layout.');
  assert.equal(choiceQuestion(''), 'Pick one');
  assert.equal(choiceQuestion('Thanks. Which size works best for the example report?'), 'Which size works best for the example report?');
});
