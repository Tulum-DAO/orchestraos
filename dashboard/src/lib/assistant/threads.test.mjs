/**
 * Pure-logic tests for thread replay → timeline hydration (Phase 1).
 *   node --experimental-strip-types dashboard/src/lib/assistant/threads.test.mjs
 *
 * Proves a resumed thread reconstructs the flat TurnItem[] timeline in order,
 * INCLUDING persisted tool-call/gate cards (the operator ruling #3: gate lifecycle is
 * persisted and replayed, not text-only).
 */
import assert from 'node:assert';
import { hydrateTimeline } from './thread-hydrate.ts';

const detail = {
  thread: 'thr-gate',
  title: 'Registry update (blocked)',
  turns: [
    { role: 'user', text: 'demo the tool gate', createdAt: 't0', channel: 'page' },
    {
      role: 'assistant', text: 'Let me check. The registry update is a write.', createdAt: 't1', channel: 'page',
      toolCalls: [
        { toolCallId: 'tc-r1', tool: 'tmux_capture', args: {}, taint: 'trusted', quadrant: 'read_trusted', decision: 'allow', mode: 'live', reason: 'read', overridable: true, ok: true, results: ['gm — running'] },
        { toolCallId: 'tc-w1', tool: 'state_write', args: { path: 'registry.json' }, taint: 'untrusted', quadrant: 'write_untrusted', decision: 'block', mode: 'live', reason: 'lethal_quadrant', overridable: false, skippedWhy: 'blocked_lethal' },
      ],
    },
    { role: 'assistant', text: 'I did NOT write.', createdAt: 't2', channel: 'page' },
  ],
};

const state = hydrateTimeline(detail);
const items = state.items;

// user msg, assistant text, 2 tool cards, assistant text = 5 items in order
assert.equal(items.length, 5, `expected 5 items, got ${items.length}`);
assert.equal(items[0].type, 'user_message');
assert.equal(items[0].content, 'demo the tool gate');
assert.equal(items[1].type, 'assistant_message');
assert.equal(items[2].type, 'tool_call');
assert.equal(items[2].tool, 'tmux_capture');
assert.equal(items[2].phase, 'done');            // executed read
assert.deepEqual(items[2].results, ['gm — running']);
assert.equal(items[3].type, 'tool_call');
assert.equal(items[3].tool, 'state_write');
assert.equal(items[3].phase, 'skipped');          // lethal blocked
assert.equal(items[3].skippedWhy, 'blocked_lethal');
assert.equal(items[3].overridable, false);        // D8 — never overridable
assert.equal(items[4].type, 'assistant_message');
assert.equal(items[4].content, 'I did NOT write.');

// no active assistant segment after hydration (a fresh send opens a new one)
assert.equal(state.activeAssistantId, null);

// unique React keys across all items
const ids = new Set(items.map((i) => i.id));
assert.equal(ids.size, items.length, 'item ids must be unique');

// empty thread hydrates to an empty timeline
const empty = hydrateTimeline({ thread: 'x', title: 'x', turns: [] });
assert.equal(empty.items.length, 0);

console.log('PASS: thread hydration — ordered turns + replayed gate cards (read done, lethal blocked), unique keys');
