import test from 'node:test';
import assert from 'node:assert/strict';
import { isCommandTool, outputPreview, storedToolDensity, todoItems } from './toolCardDetail.ts';

test('tool density accepts only the three saved display modes', () => {
  assert.equal(storedToolDensity(() => 'verbose'), 'verbose');
  assert.equal(storedToolDensity(() => 'anything else'), 'normal');
});

test('a command preview keeps the first lines and says what is hidden', () => {
  assert.deepEqual(outputPreview('one\ntwo\nthree\nfour'), { text: 'one\ntwo\nthree', hiddenLines: 1 });
  assert.equal(isCommandTool('Bash'), true);
  assert.equal(isCommandTool('Read'), false);
});

test('TodoWrite becomes a checklist rather than a JSON argument blob', () => {
  assert.deepEqual(todoItems({ kind: 'tool', tool: 'TodoWrite', input: { todos: [
    { content: 'Read the parser', status: 'completed' }, { activeForm: 'Writing tests', status: 'in_progress' },
  ] }, key: 'todo' }), [
    { content: 'Read the parser', status: 'completed' }, { content: 'Writing tests', status: 'in_progress' },
  ]);
});
