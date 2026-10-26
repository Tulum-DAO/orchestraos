import { test } from 'node:test';
import assert from 'node:assert';
import { isAtBottom } from './useStickToBottom.ts';

test('within the threshold of the bottom counts as at the bottom', () => {
  assert.equal(isAtBottom({ scrollHeight: 1000, scrollTop: 600, clientHeight: 400 }), true);
  assert.equal(isAtBottom({ scrollHeight: 1000, scrollTop: 540, clientHeight: 400 }), true);    // 60 px up
  assert.equal(isAtBottom({ scrollHeight: 1000, scrollTop: 300, clientHeight: 400 }), false);   // reading history
});
