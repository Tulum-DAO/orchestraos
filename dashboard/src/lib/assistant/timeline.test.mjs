/**
 * Pure-logic tests for the B2 timeline reducer. Zero-dep, run with:
 *   node --experimental-strip-types dashboard/src/lib/assistant/timeline.test.mjs
 *
 * timeline.ts has only type-only imports (stripped at load), so Node can run
 * the real source directly — no build step, no drift. This is a logic smoke
 * test complementing the headless UI test.
 */
import assert from 'node:assert';
import { emptyTimeline, beginTurn, applyStreamEvent } from './timeline.ts';

let s = emptyTimeline();
s = beginTurn(s, 'check agents and update registry');
assert.equal(s.items.length, 2, 'user + assistant seg');
assert.equal(s.items[0].type, 'user_message');
assert.equal(s.items[1].type, 'assistant_message');

s = applyStreamEvent(s, { type: 'delta', text: 'Checking. ' });
assert.equal(s.items[1].content, 'Checking. ');

// read tool: proposed -> decision(allow/live) -> result
s = applyStreamEvent(s, { type: 'tool_call_proposed', messageId: 'm', toolCallId: 't1', tool: 'tmux_capture', args: {}, taint: 'trusted', quadrant: 'read_trusted' });
assert.equal(s.activeAssistantId, null, 'proposed closes active assistant seg');
assert.equal(s.items[2].type, 'tool_call');
assert.equal(s.items[2].phase, 'proposed');
s = applyStreamEvent(s, { type: 'tool_call_decision', messageId: 'm', toolCallId: 't1', decision: 'allow', mode: 'live', reason: 'read', overridable: true });
assert.equal(s.items[2].phase, 'decided');
s = applyStreamEvent(s, { type: 'tool_call_result', messageId: 'm', toolCallId: 't1', ok: true, results: ['gm', 'pm-clients'] });
assert.equal(s.items[2].phase, 'done');
assert.deepEqual(s.items[2].results, ['gm', 'pm-clients']);

// text after tool opens a NEW assistant segment
s = applyStreamEvent(s, { type: 'delta', text: 'Now the write. ' });
assert.equal(s.items[3].type, 'assistant_message');
assert.equal(s.items[3].content, 'Now the write. ');

// lethal write: proposed -> decision(block) -> skipped(blocked_lethal)
s = applyStreamEvent(s, { type: 'tool_call_proposed', messageId: 'm', toolCallId: 't2', tool: 'state_write', args: { path: 'registry.json' }, taint: 'untrusted', quadrant: 'write_untrusted' });
s = applyStreamEvent(s, { type: 'tool_call_decision', messageId: 'm', toolCallId: 't2', decision: 'block', mode: 'live', reason: 'lethal_quadrant', overridable: false });
s = applyStreamEvent(s, { type: 'tool_call_skipped', messageId: 'm', toolCallId: 't2', why: 'blocked_lethal' });
const lethal = s.items.find((i) => i.type === 'tool_call' && i.toolCallId === 't2');
assert.equal(lethal.phase, 'skipped');
assert.equal(lethal.skippedWhy, 'blocked_lethal');
assert.equal(lethal.overridable, false, 'lethal is never overridable (D8)');

console.log('PASS: timeline reducer — interleaving, read-executed, lethal-blocked, no-override');
