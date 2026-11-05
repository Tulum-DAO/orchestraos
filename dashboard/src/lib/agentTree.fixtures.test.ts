/**
 * The five agent-tree fixtures (scripts/agent_tree.py --json output, scrubbed) parse and every node
 * carries every key of the API contract. If agent_tree.py's shape drifts, re-capture and this fails.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const FIXTURE_NAMES = ['idle-seat', 'one-running', 'frozen-parent', 'mixed', 'depth3'] as const;

const CONTRACT_KEYS = [
  'agent_id', 'parent_agent_id', 'depth', 'type', 'label', 'state', 'started_at', 'last_activity_at',
  'current_tool', 'last_tool', 'tokens_own', 'tokens_rollup', 'tokens_cache_read', 'ctx_tokens',
  'elapsed_s', 'transcript_path', 'children',
];
const STATES = new Set(['running', 'waiting', 'finished', 'stale']);

function load(name: string): Record<string, unknown> {
  return JSON.parse(readFileSync(new URL(`./fixtures/agent-tree/${name}.json`, import.meta.url), 'utf8'));
}

function walk(n: Record<string, unknown>, visit: (n: Record<string, unknown>) => void): void {
  visit(n);
  for (const c of n.children as Record<string, unknown>[]) walk(c, visit);
}

for (const name of FIXTURE_NAMES) {
  test(`fixture ${name} parses and every node has every contract key`, () => {
    const tree = load(name);
    assert.equal(tree.depth, 0, 'root is the seat');
    assert.equal(tree.type, 'seat');
    walk(tree, (n) => {
      for (const k of CONTRACT_KEYS) assert.ok(k in n, `${name}: node ${String(n.agent_id)} lacks ${k}`);
      assert.ok(STATES.has(n.state as string), `${name}: bad state ${String(n.state)}`);
      assert.ok(Array.isArray(n.children));
      assert.ok(String(n.transcript_path).startsWith('/fixture/'), `${name}: transcript_path not scrubbed`);
      for (const c of n.children as Record<string, unknown>[]) {
        assert.equal(c.depth, (n.depth as number) + 1, `${name}: child depth`);
        assert.equal(c.parent_agent_id, n.agent_id, `${name}: child parent id`);
      }
    });
  });
}

test('fixture shapes: idle has no children, frozen-parent has a running helper at depth 2, depth3 reaches 3', () => {
  assert.equal((load('idle-seat').children as unknown[]).length, 0);
  let helper: Record<string, unknown> | undefined;
  walk(load('frozen-parent'), (n) => { if (n.depth === 2 && n.state === 'running') helper = n; });
  assert.ok(helper, 'frozen-parent has a running depth-2 helper');
  assert.equal(helper!.current_tool, 'Bash');
  let max = 0;
  walk(load('depth3'), (n) => { max = Math.max(max, n.depth as number); });
  assert.equal(max, 3);
  const states = new Set<string>();
  walk(load('mixed'), (n) => { if (n.depth === 1) states.add(n.state as string); });
  for (const s of ['finished', 'stale', 'running']) assert.ok(states.has(s), `mixed has a ${s} sibling`);
});
