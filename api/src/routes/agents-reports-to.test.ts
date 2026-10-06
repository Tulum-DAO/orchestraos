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

/**
 * THE GAP THE HELPER TEST ABOVE DOES NOT COVER.
 *
 * The row builder spreads `...hierarchyFieldsFor(def)` and then, further down the SAME object
 * literal, `...def, ...state`. So the raw registry value lands back on top of the normalised
 * one and the route can emit the very `''` the helper exists to remove — with every test above
 * still green, because they test the helper and the wire carries something else.
 *
 * applyIdentityPrecedence runs AFTER those spreads and is where identity is made to stick. It
 * re-asserted name/tier/always_on and simply did not know about reports_to.
 */
import { applyIdentityPrecedence } from './agents-identity.js';

const rowAfterSpreads = (def: Record<string, unknown>) => {
  // The route's shape: normalised first, raw def last — exactly as agents.ts builds it.
  const agent: Record<string, unknown> = { id: 'seat', ...rowFrom(def), ...def };
  applyIdentityPrecedence(agent, def, 'seat', 'seat', 'vps', true);
  return agent;
};

test('an empty reports_to cannot survive the raw def spread onto the wire', () => {
  // Pre-fix this returned '' — the route emitted a parent named nothing.
  const agent = rowAfterSpreads({ tier: 'T1', reports_to: '' });
  assert.equal(agent.reports_to, undefined);
  assert.equal('reports_to' in agent, false, 'absent must mean the key is gone, not undefined');
  assert.equal('reports_to' in JSON.parse(JSON.stringify(agent)), false);
});

test('a real parent still reaches the wire, and tier still defaults', () => {
  // The control: the rule above removes empty strings, not parents.
  const agent = rowAfterSpreads({ reports_to: 'gm' });
  assert.equal(agent.reports_to, 'gm');
  assert.equal(agent.tier, 'T2');
});
