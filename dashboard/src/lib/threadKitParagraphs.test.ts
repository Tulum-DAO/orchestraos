import { test } from 'node:test';
import assert from 'node:assert/strict';
import { completeParts, completeText } from './threadKit/paragraphs.ts';

test('a paragraph shows once a blank line ends it; the one still being written is held back', () => {
  assert.deepEqual(completeText('The sample is read'), { text: '', pending: true });
  assert.deepEqual(completeText('The sample is ready.\n'), { text: '', pending: true });
  assert.deepEqual(completeText('The sample is ready.\nIt has two parts.\n\nThe first'), { text: 'The sample is ready.\nIt has two parts.', pending: true });
  assert.deepEqual(completeText('One.\n\nTwo.\n\n'), { text: 'One.\n\nTwo.', pending: false });
});

test('at the end of the turn everything shows, the last half-line included', () => {
  assert.deepEqual(completeText('One.\n\nTwo, without a break', true), { text: 'One.\n\nTwo, without a break', pending: false });
});

test('a code fence shows only once it is closed', () => {
  const open = 'Run this:\n\n```sh\nls sample\n';
  assert.deepEqual(completeText(open), { text: 'Run this:', pending: true });
  assert.deepEqual(completeText(`${open}cat example\n`), { text: 'Run this:', pending: true });
  assert.deepEqual(completeText(`${open}\`\`\`\n`), { text: 'Run this:\n\n```sh\nls sample\n```', pending: false });
  // a blank line inside a fence is not the end of anything
  assert.deepEqual(completeText('```\na\n\nb\n'), { text: '', pending: true });
});

test('a table shows only when a line that is not part of it follows', () => {
  const t = '| Layout | Columns |\n|--|--|\n| Grid | 3 |\n';
  assert.deepEqual(completeText(t), { text: '', pending: true });
  assert.deepEqual(completeText(`${t}| List | 1 |\n`), { text: '', pending: true });
  assert.deepEqual(completeText(`${t}| List | 1 |\n\n`), { text: `${t}| List | 1 |`, pending: false });
  assert.deepEqual(completeText(`${t}Which one?\n`), { text: t.trimEnd(), pending: true });
});

test('a list is complete when something that is not part of it follows; its items\' continuation lines belong to it', () => {
  const l = 'Plan:\n- read the sample\n  and the example\n- write it up\n';
  assert.deepEqual(completeText(l), { text: 'Plan:', pending: true });            // the text before a list is finished
  assert.deepEqual(completeText(`${l}That is all.\n`), { text: l.trimEnd(), pending: true });
  assert.deepEqual(completeText('1. one\n2. two\n\n'), { text: '1. one\n2. two', pending: false });
});

test('a heading is complete at the end of its line', () => {
  assert.deepEqual(completeText('## Results\nThe sample'), { text: '## Results', pending: true });
});

test('with tool runs: text before a run is finished; the last text part shows its complete paragraphs', () => {
  const tools = { kind: 'tools' as const, calls: [] };
  const r = completeParts([{ kind: 'text', text: 'Checking.' }, tools, { kind: 'text', text: 'Found it.\n\nNow the' }]);
  assert.deepEqual(r.parts, [{ kind: 'text', text: 'Checking.' }, tools, { kind: 'text', text: 'Found it.' }]);
  assert.equal(r.pending, true);
  const halfway = completeParts([{ kind: 'text', text: 'Checking' }]);
  assert.deepEqual([halfway.parts, halfway.pending], [[], true]);
  assert.deepEqual(completeParts([tools]).parts, [tools]);
});
