import { test } from 'node:test';
import assert from 'node:assert/strict';
import { reviveArgv } from './agents.js';

test('revive runs the revive script for a safe id with auto as the default mode', () => {
  const argv = reviveArgv('qa-demo', undefined);
  assert.ok(Array.isArray(argv));
  assert.match((argv as string[])[0], /scripts\/revive_agent\.py$/);
  assert.deepEqual((argv as string[]).slice(1), ['qa-demo', '--mode', 'auto']);
  assert.deepEqual((reviveArgv('qa-demo', 'resume') as string[]).slice(1), ['qa-demo', '--mode', 'resume']);
  assert.deepEqual((reviveArgv('qa-demo', 'fresh') as string[]).slice(1), ['qa-demo', '--mode', 'fresh']);
});

test('a bad id or mode never reaches the script', () => {
  for (const id of ['../x', 'a b', '', 'x;rm -rf /', 42]) assert.deepEqual(reviveArgv(id, 'auto'), { error: 'bad agent id' });
  assert.deepEqual(reviveArgv('qa-demo', 'sideways'), { error: 'mode must be auto, resume or fresh' });
});
