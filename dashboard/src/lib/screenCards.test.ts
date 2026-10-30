import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  reduce, EMPTY, inView, focusOf, screenPayload, screenLabel, briefLine, partView, resolvePicks, answerStep, MAX_SENT,
  type ScreenAction, type ScreenCard, type ScreenState,
} from './screenCards.ts';
import { startState, type MenuPart } from './multiPart.ts';

const card = (id: string, over: Partial<ScreenCard> = {}): ScreenCard => ({
  id, kind: 'decision', from: 'pm-demo', state: 'waiting', question: `Question ${id}?`, ...over,
});

const run = (...as: ScreenAction[]) => as.reduce(reduce, EMPTY as ScreenState);

test('a reported card is not in view until the observer says so; it leaves when it goes', () => {
  let s = run({ type: 'report', key: 'chat:a', card: card('a'), surface: 'chat' });
  assert.equal(inView(s).length, 0);
  s = reduce(s, { type: 'visible', key: 'chat:a', visible: true, now: 10 });
  assert.deepEqual(inView(s).map((e) => e.key), ['chat:a']);
  s = reduce(s, { type: 'visible', key: 'chat:a', visible: false, now: 20 });
  assert.equal(inView(s).length, 0);
  s = reduce(s, { type: 'gone', key: 'chat:a' });
  assert.deepEqual(s.entries, {});
});

test('an unchanged report changes nothing (no re-render loop); a changed one keeps visibility and touch', () => {
  const s1 = run({ type: 'report', key: 'k', card: card('a'), surface: 'chat' }, { type: 'visible', key: 'k', visible: true, now: 5 },
    { type: 'touch', key: 'k', now: 6 });
  assert.equal(reduce(s1, { type: 'report', key: 'k', card: card('a'), surface: 'chat' }), s1);
  const s2 = reduce(s1, { type: 'report', key: 'k', card: card('a', { state: 'answered' }), surface: 'chat' });
  assert.equal(s2.entries.k.visible, true);
  assert.equal(s2.entries.k.touchedAt, 6);
  assert.equal(s2.entries.k.card.state, 'answered');
});

test('focus: an open popover, else the card touched last still in view, else the only one in view', () => {
  let s = run(
    { type: 'report', key: 'chat:a', card: card('a'), surface: 'chat' }, { type: 'visible', key: 'chat:a', visible: true, now: 1 },
    { type: 'report', key: 'chat:b', card: card('b'), surface: 'chat' }, { type: 'visible', key: 'chat:b', visible: true, now: 2 },
  );
  assert.equal(focusOf(s), null);                       // two in view, none touched: unclear
  s = reduce(s, { type: 'touch', key: 'chat:a', now: 3 });
  assert.deepEqual([focusOf(s)?.entry.key, focusOf(s)?.why], ['chat:a', 'touched']);
  s = reduce(s, { type: 'touch', key: 'chat:b', now: 4 });
  assert.equal(focusOf(s)?.entry.key, 'chat:b');
  s = reduce(s, { type: 'visible', key: 'chat:b', visible: false, now: 5 });   // scrolled away: not in focus
  assert.equal(focusOf(s)?.entry.key, 'chat:a');
  s = reduce(reduce(s, { type: 'report', key: 'pop:c', card: card('c'), surface: 'popover' }), { type: 'visible', key: 'pop:c', visible: true, now: 6 });
  assert.deepEqual([focusOf(s)?.entry.key, focusOf(s)?.why], ['pop:c', 'popover']);
  s = reduce(s, { type: 'gone', key: 'pop:c' });          // the popover closed
  assert.equal(focusOf(s)?.entry.key, 'chat:a');
  s = run({ type: 'report', key: 'h:a', card: card('a'), surface: 'history' }, { type: 'visible', key: 'h:a', visible: true, now: 1 });
  assert.deepEqual([focusOf(s)?.entry.key, focusOf(s)?.why], ['h:a', 'only']);
});

test('a card drawn twice at once (drawer and chat) is one card on screen', () => {
  const s = run(
    { type: 'report', key: 'chat:a', card: card('a'), surface: 'chat' }, { type: 'visible', key: 'chat:a', visible: true, now: 1 },
    { type: 'report', key: 'history:a', card: card('a'), surface: 'history' }, { type: 'visible', key: 'history:a', visible: true, now: 2 },
  );
  assert.equal(inView(s).length, 1);
  assert.equal(focusOf(s)?.why, 'only');
  assert.equal(focusOf(s)?.entry.key, 'history:a');
});

const parts: MenuPart[] = [
  { index: 0, question: 'Which size?', select: 'single', options: [{ n: '1', label: 'Circle' }, { n: '2', label: 'Square' }, { n: '3', label: 'Type something.', input_kind: 'free_text' }] },
  { index: 1, question: 'Which colours?', select: 'multi', options: [{ n: '1', label: 'Blue' }, { n: '2', label: 'Green' }, { n: '3', label: 'Red' }] },
  { index: 2, question: 'Anything else?', select: 'single', options: [], has_free_text: true, pickAndWords: true },
];

test('multi-part: the part on show is reported with its lettered options and what is picked or typed', () => {
  const st = { answers: [{ ns: ['2'], text: '', skipped: false }, { ns: ['1', '3'], text: 'and teal', skipped: false }, { ns: [], text: '', skipped: false }], at: 1 };
  assert.deepEqual(partView(parts, st), {
    n: 2, of: 3, question: 'Which colours?', select: 'multi', options: ['A. Blue', 'B. Green', 'C. Red'],
    picked: ['A. Blue', 'C. Red'], typed: 'and teal',
  });
  assert.deepEqual(partView(parts, { ...st, at: 0 })?.options, ['A. Circle', 'B. Square']);   // the write-in row is not drawn
  assert.equal(partView(parts, { ...st, at: 9 }), undefined);
});

test('paging a part re-reports the card, so the context follows the part on show', () => {
  const k = 'chat:a';
  let s = run({ type: 'report', key: k, card: card('a', { kind: 'multipart', part: partView(parts, startState(parts)) }), surface: 'chat' },
    { type: 'visible', key: k, visible: true, now: 1 });
  assert.equal(screenPayload(s)?.payload.cards[0].part?.question, 'Which size?');
  s = reduce(s, { type: 'report', key: k, card: card('a', { kind: 'multipart', part: partView(parts, { ...startState(parts), at: 1 }) }), surface: 'chat' });
  assert.equal(screenPayload(s)?.payload.cards[0].part?.question, 'Which colours?');
  assert.equal(screenPayload(s)?.payload.cards[0].part?.n, 2);
});

test('the payload is small: the focused card in full, every other one line, at most MAX_SENT, refs not ids', () => {
  let s: ScreenState = EMPTY;
  for (let i = 0; i < MAX_SENT + 3; i++) {
    const key = `chat:${i}`;
    s = reduce(s, { type: 'report', key, card: card(`apr_0000000${i % 10}_${i}`, { question: `Sample question ${i}?`, summary: 'Some words.' }), surface: 'chat' });
    s = reduce(s, { type: 'visible', key, visible: true, now: i + 1 });
  }
  s = reduce(s, { type: 'touch', key: 'chat:2', now: 100 });
  const out = screenPayload(s)!;
  assert.equal(out.payload.cards.length, MAX_SENT);
  assert.equal(out.payload.focus, 'c1');
  assert.equal(out.refs.c1, 'chat:2');
  assert.deepEqual(Object.keys(out.payload.cards[0]).sort(), ['from', 'kind', 'question', 'ref', 'state', 'summary', 'surface']);
  assert.ok(out.payload.cards.slice(1).every((c) => Object.keys(c).join() === 'ref,line'));
  assert.ok(!JSON.stringify(out.payload).includes('apr_'), 'no card id reaches Arturo');
  assert.ok(JSON.stringify(out.payload).length < 2000);
});

test('no focus: every card one line, newest in view first; nothing in view: no payload', () => {
  const s = run(
    { type: 'report', key: 'a', card: card('a', { question: 'Which size?' }), surface: 'approvals' }, { type: 'visible', key: 'a', visible: true, now: 1 },
    { type: 'report', key: 'b', card: card('b', { kind: 'approval', from: 'dev-demo', question: 'Deploy the drawer?' }), surface: 'approvals' },
    { type: 'visible', key: 'b', visible: true, now: 2 },
  );
  const out = screenPayload(s)!;
  assert.equal(out.payload.focus, null);
  assert.deepEqual(out.payload.cards, [
    { ref: 'c1', line: 'Approval from dev-demo, waiting: Deploy the drawer?' },
    { ref: 'c2', line: 'Decision from pm-demo, waiting: Which size?' },
  ]);
  assert.equal(screenLabel(s), '2 cards on screen');
  assert.equal(screenPayload(EMPTY), null);
  assert.equal(screenLabel(EMPTY), null);
});

test('the context card names the focused card by its question', () => {
  const s = run({ type: 'report', key: 'a', card: card('apr_0a1b2c3d_1', { question: 'Which size?' }), surface: 'popover' },
    { type: 'visible', key: 'a', visible: true, now: 1 });
  assert.equal(screenLabel(s), 'Which size?');
});

test('a brief line carries the part on show and an answer', () => {
  assert.equal(briefLine(card('a', { kind: 'multipart', question: 'Which size?', part: { n: 2, of: 3, question: 'Which colour?', select: 'single', options: [], picked: [], typed: '' } })),
    'Multi-part question from pm-demo, waiting: Which size? (part 2 of 3: Which colour?)');
  assert.equal(briefLine(card('a', { state: 'answered', answer: 'Circle', question: 'Which size?' })), 'Decision from pm-demo, answered: Which size? (answered: Circle)');
});

test('picks by letter, label or both; unknown picks named', () => {
  assert.deepEqual(resolvePicks(parts[0], ['b']), { ns: ['2'], unknown: [] });
  assert.deepEqual(resolvePicks(parts[0], ['circle']), { ns: ['1'], unknown: [] });
  assert.deepEqual(resolvePicks(parts[0], ['B. Square', 'A']), { ns: ['2', '1'], unknown: [] });
  assert.deepEqual(resolvePicks(parts[0], ['C']), { ns: [], unknown: ['C'] });          // the hidden write-in row is not C
  assert.deepEqual(resolvePicks(parts[0], ['Hexagon', '']), { ns: [], unknown: ['Hexagon'] });
});

test('an answer from Arturo takes the same step the taps would, on the part on show', () => {
  const s0 = startState(parts);
  const a = answerStep(parts, s0, { picks: ['B'] });
  assert.ok(!('error' in a));
  if ('error' in a) return;
  assert.deepEqual(a.state.answers[0].ns, ['2']);
  assert.equal(a.state.at, 1);                      // a pick on a pick-one part moves on
  assert.equal(a.submit, false);
  const b = answerStep(parts, a.state, { picks: ['A', 'C'] });
  if ('error' in b) throw new Error(b.error);
  assert.deepEqual(b.state.answers[1].ns, ['1', '3']);
  assert.equal(b.state.at, 2);
  const c = answerStep(parts, b.state, { picks: [], words: 'that is all' });
  if ('error' in c) throw new Error(c.error);
  assert.equal(c.submit, true);                      // the last part sends, as the last tap does
  assert.equal(c.state.answers[2].text, 'that is all');
});

test('an answer that the card cannot take is refused with why', () => {
  const s0 = startState(parts);
  assert.deepEqual(answerStep(parts, s0, { picks: ['A', 'B'] }), { error: 'Pick one option on this question.' });
  assert.deepEqual(answerStep(parts, s0, { picks: ['A'], words: 'and more' }), { error: 'This question takes a pick or words, not both.' });
  assert.deepEqual(answerStep(parts, s0, { picks: ['Z'] }), { error: 'There is no option Z on this question.' });
  assert.deepEqual(answerStep(parts, { ...s0, at: 1 }, { picks: [], words: 'teal' }), { error: 'This question takes a pick, not words.' });
  assert.deepEqual(answerStep(parts, s0, { picks: [] }), { error: 'Nothing to answer with.' });
});
