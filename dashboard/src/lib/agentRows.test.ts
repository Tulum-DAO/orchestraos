import { test } from 'node:test';
import assert from 'node:assert/strict';
import { agentRowsFrom } from './api.ts';

// `/api/agents` is served two ways in this fleet. Three call sites each decided for
// themselves which to tolerate: Feed and AgentPage took both, AgentRail took only the
// wrapped shape and rendered three EMPTY SECTIONS on the other, with no error — making
// "empty" and "broken" look identical, which is the thing that file forbids.

test('both response shapes yield the same rows', () => {
  const rows = [{ id: 'a' }, { id: 'b' }];
  assert.deepEqual(agentRowsFrom(rows), rows, 'bare array');
  assert.deepEqual(agentRowsFrom({ agents: rows }), rows, 'wrapped');
});

test('an UNREADABLE answer is undefined, not an empty list', () => {
  // The distinction the rail needs: "no agents" and "we could not read the answer" must
  // not collapse. Returning [] here is what would make a broken feed look like an idle one.
  for (const bad of [undefined, null, {}, { agents: null }, { agents: 'nope' }, 42, 'x']) {
    assert.equal(agentRowsFrom(bad), undefined, `${JSON.stringify(bad)} should be unreadable`);
  }
});

test('a genuinely empty fleet is an empty list, NOT undefined', () => {
  // Positive control: the helper must distinguish empty from unreadable in BOTH directions,
  // or "unreadable" is just a synonym for falsy and the distinction buys nothing.
  assert.deepEqual(agentRowsFrom([]), []);
  assert.deepEqual(agentRowsFrom({ agents: [] }), []);
});
