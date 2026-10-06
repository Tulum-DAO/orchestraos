import { test } from 'node:test';
import assert from 'node:assert/strict';

/**
 * `reports_to` on /agents — the hierarchy field (quest-orchestra, for Shaw's Quest view:
 * T0s on top, T1s below, each T1's agents under it, standalone agents set apart).
 *
 * The row builder reads it from the registry def exactly as it reads `tier`. These pin the
 * one thing that is easy to get wrong: a seat with NO parent must come back ABSENT, not as
 * an empty string, because "unknown" and "reports to nobody" are different claims and only
 * one of them is true for the ~15 of 35 live seats that set nothing.
 */

// THE REAL FUNCTION THE ROUTE CALLS — not a mirror of it. The previous version of this file
// re-implemented the two lines under test and asserted against the copy, so deleting
// `reports_to` from the route left all three tests green. It pinned nothing: it asserted that
// JS `||` and JSON.stringify work.
import { hierarchyFieldsFor as rowFrom } from './agentHierarchy.js';

test('a seat with a parent carries it', () => {
  assert.equal(rowFrom({ tier: 'T1', reports_to: 'gm' }).reports_to, 'gm');
  assert.equal(rowFrom({ reports_to: 'pm-intentmagic' }).reports_to, 'pm-intentmagic');
});

test('a seat with NO parent is ABSENT, not empty-string', () => {
  // A consumer must be able to tell "we do not know" from "it reports to ''". An empty
  // string sorts and renders as a real parent named nothing, which is how a standalone
  // seat ends up filed under a group that does not exist.
  assert.equal(rowFrom({ tier: 'T1' }).reports_to, undefined);
  assert.equal(rowFrom({ tier: 'T1', reports_to: '' }).reports_to, undefined);
  assert.equal('reports_to' in JSON.parse(JSON.stringify(rowFrom({ tier: 'T1' }))), false,
    'an absent parent must not be serialised at all');
});

test('reports_to does not disturb tier', () => {
  // Positive control: the new field sits beside the old one rather than replacing its
  // defaulting, which is the regression a careless addition here would cause.
  assert.equal(rowFrom({ reports_to: 'gm' }).tier, 'T2', 'tier must still default');
  assert.equal(rowFrom({ tier: 'T0', reports_to: 'gm' }).tier, 'T0');
});
