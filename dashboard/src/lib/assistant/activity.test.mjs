/**
 * Pure-logic tests for the B3 activity-pill derivation. Zero-dep:
 *   node --experimental-strip-types dashboard/src/lib/assistant/activity.test.mjs
 *
 * The pill is HONEST: lifecycle-derived by default, and if the (blessed, Track-A)
 * `activity` event is present it takes precedence. No <thinking> theater.
 */
import assert from 'node:assert';
import { initialActivity, deriveActivity } from './activity.ts';

// idle at rest
let a = initialActivity();
assert.equal(a.phase, 'idle');

// turn begins (send) -> thinking
a = deriveActivity(a, { type: '_begin' });
assert.equal(a.phase, 'thinking');

// start event keeps thinking
a = deriveActivity(a, { type: 'start', messageId: 'm', user: 'u', thread: 't', channel: 'bubble' });
assert.equal(a.phase, 'thinking');

// tool proposed -> running{tool}
a = deriveActivity(a, { type: 'tool_call_proposed', messageId: 'm', toolCallId: 't1', tool: 'tmux_capture', args: {}, taint: 'trusted', quadrant: 'read_trusted' });
assert.equal(a.phase, 'running');
assert.equal(a.tool, 'tmux_capture');

// tool resolved -> back to thinking (between work)
a = deriveActivity(a, { type: 'tool_call_result', messageId: 'm', toolCallId: 't1', ok: true, results: [] });
assert.equal(a.phase, 'thinking');

// first delta -> answering
a = deriveActivity(a, { type: 'delta', messageId: 'm', text: 'Hi' });
assert.equal(a.phase, 'answering');

// done -> idle (pill hides)
a = deriveActivity(a, { type: 'done', messageId: 'm', finishReason: 'stop' });
assert.equal(a.phase, 'idle');

// error -> error
let b = deriveActivity(initialActivity(), { type: '_begin' });
b = deriveActivity(b, { type: 'error', message: 'boom' });
assert.equal(b.phase, 'error');

// FORWARD-COMPAT: a server activity event overrides lifecycle + carries label
let c = deriveActivity(initialActivity(), { type: '_begin' });
c = deriveActivity(c, { type: 'activity', messageId: 'm', phase: 'retrieving', label: 'reading tmux + state' });
assert.equal(c.phase, 'retrieving');
assert.equal(c.label, 'reading tmux + state');
assert.equal(c.fromServer, true, 'server-driven phase flagged');

console.log('PASS: activity derivation — lifecycle phases + forward-compat server activity event');
