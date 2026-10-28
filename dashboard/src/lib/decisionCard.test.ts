import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  approvalAction, canSendWords, withInputKinds, isBuiltInExtra, isLongOption, keyAction, linkedMenuCard, riskText, visibleOptions,
} from './decisionCard';

const OPTIONS = [
  { n: '1', label: 'Example option', input_kind: 'direct' },
  { n: '2', label: 'Sample option', input_kind: 'direct' },
  { n: '3', label: 'Type something.', input_kind: 'free_text' },
  { n: '4', label: 'Chat about this', input_kind: 'chat' },
];

test("Claude Code's built-in extra rows are not drawn; other write-ins are", () => {
  assert.deepEqual(visibleOptions(OPTIONS).map((o) => o.n), ['1', '2']);
  assert.equal(isBuiltInExtra({ label: 'Other / write in…' }), false);
  assert.deepEqual(visibleOptions([{ n: '1', label: 'Other / write in…', input_kind: 'free_text' }]).map((o) => o.n), ['1']);
});

test('words alone need a write-in option to ride, even a hidden one', () => {
  assert.equal(canSendWords(OPTIONS), true);
  assert.equal(canSendWords(OPTIONS.slice(0, 2)), false);
});

test('keys: 1–9 pick an existing option, Return sends, e goes to the field; typing stays in the field', () => {
  const options = OPTIONS.slice(0, 2);
  assert.deepEqual(keyAction('2', { inField: false, options }), { kind: 'pick', n: '2' });
  assert.equal(keyAction('7', { inField: false, options }), null);
  assert.deepEqual(keyAction('Enter', { inField: false, options }), { kind: 'send' });
  assert.deepEqual(keyAction('Enter', { inField: true, options }), { kind: 'send' });
  assert.deepEqual(keyAction('e', { inField: false, options }), { kind: 'focus-text' });
  assert.equal(keyAction('e', { inField: true, options }), null);
  assert.equal(keyAction('1', { inField: true, options }), null);
});

test('a long option is cut short until it is opened', () => {
  assert.equal(isLongOption({ label: 'Short' }), false);
  assert.equal(isLongOption({ label: 'x'.repeat(91) }), true);
  assert.equal(isLongOption({ label: 'one\ntwo' }), true);
});

test('risk reads "· N risk" material; none when absent', () => {
  assert.equal(riskText('Medium'), 'medium risk');
  assert.equal(riskText(null), '');
});

test('approve goes at once; deny needs a reason from the field', () => {
  assert.deepEqual(approvalAction('approve', ''), { kind: 'approve' });
  assert.deepEqual(approvalAction('deny', '   '), { kind: 'need-reason' });
  assert.deepEqual(approvalAction('deny', ' Not now '), { kind: 'deny', reason: 'Not now' });
});

test("a live menu's card is found by agent (any generation) and question", () => {
  const items = [
    { id: 'a', kind: 'menu', agent_id: 'dev-demo-gen2', title: 'Pick one?', menu: { question: 'Pick  one?' } },
    { id: 'b', kind: 'menu', agent_id: 'pm-demo', title: 'Pick one?', menu: { question: 'Pick one?' } },
    { id: 'c', kind: null, agent_id: 'dev-demo', title: 'Pick one?' },
  ];
  assert.equal(linkedMenuCard(items, 'dev-demo', 'Pick one?')?.id, 'a');
  assert.equal(linkedMenuCard(items, 'dev-demo', 'Something else?'), undefined);
  assert.equal(linkedMenuCard([{ ...items[0], menu: { question: 'Pick one?', read_only: true } }], 'dev-demo', 'Pick one?'), undefined);
});

test("a live menu's options get the server's kinds from their labels", () => {
  const out = withInputKinds([{ n: '1', label: 'Example' }, { n: '2', label: 'Type something.' }, { n: '3', label: 'Chat about this' },
    { n: '4', label: 'Write-in...' }, { n: '5', label: 'Sample', input_kind: 'free_text' }]);
  assert.deepEqual(out.map((o) => o.input_kind), ['direct', 'free_text', 'chat', 'free_text', 'free_text']);
});
