import { test } from 'node:test';
import assert from 'node:assert';
import { isAtBottom } from './useStickToBottom.ts';

test('only the bottom itself counts as at the bottom: a reader up even 60 px is not followed', () => {
  assert.equal(isAtBottom({ scrollHeight: 1000, scrollTop: 600, clientHeight: 400 }), true);
  assert.equal(isAtBottom({ scrollHeight: 1000, scrollTop: 597, clientHeight: 400 }), true);    // rounding
  assert.equal(isAtBottom({ scrollHeight: 1000, scrollTop: 540, clientHeight: 400 }), false);   // 60 px up
  assert.equal(isAtBottom({ scrollHeight: 1000, scrollTop: 300, clientHeight: 400 }), false);   // reading history
});
