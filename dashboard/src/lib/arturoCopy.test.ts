import { test } from 'node:test';
import assert from 'node:assert/strict';
import { replyCopyText } from './arturoCopy';

test('a plain reply is copied as it is shown', () => {
  assert.equal(replyCopyText('  Example reply.\nSecond line.  '), 'Example reply.\nSecond line.');
});

test('a reply that ran tools gives its text parts in order, without the tool cards', () => {
  const parts = [
    { kind: 'text' as const, text: 'Checking.' },
    { kind: 'tool' as const, name: 'example_tool', status: 'done' } as never,
    { kind: 'text' as const, text: 'Done.' },
  ];
  assert.equal(replyCopyText('ignored', parts), 'Checking.\n\nDone.');
});

test('an empty reply copies nothing', () => {
  assert.equal(replyCopyText(''), '');
  assert.equal(replyCopyText('   '), '');
});
