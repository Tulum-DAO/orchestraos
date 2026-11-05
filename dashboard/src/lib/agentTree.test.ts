/**
 * The agent-tree view logic (lib/agentTree.ts), pinned against the real-shape fixtures: row order,
 * "waiting on <helper>" for a frozen parent, collapse + "+N live", token/age text, snapshot merge.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  rowsOf, waitingOn, toolText, fmtTokens, fmtAge, defaultCollapsed, mergeSnapshot, hasSubtree,
  workingCount, findNode, type AgentTreeNode,
} from './agentTree';

const load = (name: string): AgentTreeNode =>
  JSON.parse(readFileSync(new URL(`./fixtures/agent-tree/${name}.json`, import.meta.url), 'utf8'));
const NOW = Date.parse('2026-11-05T03:00:00Z');
const byLabel = (t: AgentTreeNode, label: string) => {
  const stack = [t];
  while (stack.length) { const n = stack.pop()!; if (n.label === label) return n; stack.push(...n.children); }
  throw new Error(`no node ${label}`);
};

test('frozen parent: the depth-1 row and the seat row both read "waiting on <helper>"', () => {
  const t = load('frozen-parent');
  const rows = rowsOf(t, new Set(), NOW);
  assert.deepEqual(rows.map((r) => [r.depth, r.state]), [[0, 'waiting'], [1, 'waiting'], [2, 'running']]);
  assert.equal(rows[0].waitingOn, 'Run the test suite');
  assert.equal(rows[1].waitingOn, 'Run the test suite');
  assert.equal(rows[0].tool, 'waiting on Run the test suite');
  assert.equal(rows[1].tool, 'waiting on Run the test suite');
  assert.equal(rows[2].tool, 'Bash');
  assert.equal(rows[2].waitingOn, undefined);
});

test('waitingOn follows the most recently active live path to its deepest running node', () => {
  const t = load('depth3');
  const web = byLabel(t, 'Build the web view');
  assert.equal(waitingOn(web), 'Run the test suite');
  assert.equal(waitingOn(byLabel(t, 'Run the test suite')), null, 'a leaf waits on nothing');
  assert.equal(waitingOn(load('idle-seat')), null);
});

test('waitingOn picks the live branch whose subtree wrote last', () => {
  const t = load('depth3');
  byLabel(t, 'Build the phone view').last_activity_at = new Date(NOW - 1000).toISOString();
  byLabel(t, 'Run the test suite').last_activity_at = new Date(NOW - 5000).toISOString();
  assert.equal(waitingOn(t), 'Build the phone view');
  byLabel(t, 'Run the test suite').last_activity_at = new Date(NOW).toISOString();
  assert.equal(waitingOn(t), 'Run the test suite', 'a deep write makes its whole branch the recent one');
});

test('running and waiting nodes sort first, then by last activity, newest first', () => {
  const rows = rowsOf(load('mixed'), new Set(), NOW);
  assert.deepEqual(rows.slice(1).map((r) => r.state), ['running', 'stale', 'finished']);
  const d3 = rowsOf(load('depth3'), new Set(), NOW).filter((r) => r.depth === 1);
  assert.deepEqual(d3.slice(0, 2).map((r) => r.state), ['running', 'running']);
  assert.deepEqual(d3.slice(2).map((r) => r.state), ['finished', 'finished']);
  // among the two finished, the most recently active comes first
  const fin = d3.slice(2).map((r) => r.ageS);
  assert.ok(fin[0] <= fin[1], `newest first: ${fin}`);
});

test('a live node sorts above a finished one even when the finished one wrote more recently', () => {
  const t = load('mixed');
  const fin = byLabel(t, 'Gather the inputs');
  fin.last_activity_at = new Date(NOW - 1000).toISOString();
  const rows = rowsOf(t, new Set(), NOW);
  assert.deepEqual(rows.slice(1).map((r) => r.state), ['running', 'finished', 'stale']);
});

test('pre-order walk: every child row follows its parent at depth + 1', () => {
  const rows = rowsOf(load('depth3'), new Set(), NOW);
  assert.equal(rows.length, 11);
  for (let i = 1; i < rows.length; i++) assert.ok(rows[i].depth <= rows[i - 1].depth + 1);
});

test('collapsing a node hides its descendants and shows "+1 live"', () => {
  const t = load('frozen-parent');
  const reviewer = byLabel(t, 'Review the branch');
  const rows = rowsOf(t, new Set([reviewer.agent_id]), NOW);
  assert.equal(rows.length, 2);
  assert.equal(rows[1].collapsed, true);
  assert.equal(rows[1].hasChildren, true);
  assert.equal(rows[1].liveHidden, 1);
  assert.equal(rows[1].badge, '+1 live');
  const open = rowsOf(t, new Set(), NOW);
  assert.equal(open[1].badge, undefined);
  assert.equal(open[1].collapsed, false);
});

test('toolText: current tool, else "last: X" while running, else nothing', () => {
  const n = load('one-running').children[0];
  assert.equal(toolText({ ...n, current_tool: 'Read' }), 'Read');
  assert.equal(toolText({ ...n, current_tool: null, last_tool: 'Edit', state: 'running' }), 'last: Edit');
  assert.equal(toolText({ ...n, current_tool: null, last_tool: 'Edit', state: 'finished' }), '');
  assert.equal(toolText({ ...n, current_tool: null, last_tool: null, state: 'running' }), '');
});

test('fmtTokens: one number when own equals rollup, "own / rollup" when children add tokens', () => {
  assert.equal(fmtTokens(20400, 20400), '20.4k');
  assert.equal(fmtTokens(267000, 2_900_000), '267k / 2.9M');
  assert.equal(fmtTokens(950, 950), '950');
  assert.equal(fmtTokens(0, 0), '0');
});

test('fmtAge: seconds, minutes, hours + minutes', () => {
  assert.equal(fmtAge(7), '7s');
  assert.equal(fmtAge(53 * 60), '53m');
  assert.equal(fmtAge(72 * 60), '1h 12m');
  assert.equal(fmtAge(-3), '0s');
});

test('rows carry age from last_activity_at against the given clock', () => {
  const t = load('frozen-parent');
  const last = Date.parse(t.last_activity_at!);
  assert.equal(rowsOf(t, new Set(), last + 61_000)[0].ageS, 61);
});

test('defaultCollapsed(maxDepth 2) collapses depth 2 and deeper; web (no max) collapses nothing', () => {
  const t = load('depth3');
  const c = defaultCollapsed(t, { maxDepth: 2 });
  assert.ok(c.size > 0);
  const rows = rowsOf(t, c, NOW);
  assert.equal(Math.max(...rows.map((r) => r.depth)), 2, 'depth-3 rows hidden');
  for (const r of rows) if (r.depth >= 2 && r.hasChildren) assert.equal(r.collapsed, true);
  assert.equal(defaultCollapsed(t, {}).size, 0);
});

test('mergeSnapshot keeps collapse state and the selection, and drops a pruned selection', () => {
  const t = load('depth3');
  const helper = byLabel(t, 'Run the test suite');
  const web = byLabel(t, 'Build the web view');
  const prev = { tree: t, collapsed: new Set([web.agent_id]), selected: helper.agent_id };
  const kept = mergeSnapshot(prev, load('depth3'));
  assert.ok(kept.collapsed.has(web.agent_id));
  assert.equal(kept.selected, helper.agent_id);
  const pruned = mergeSnapshot(prev, load('frozen-parent'));
  assert.equal(pruned.selected, null, 'the helper id is not in this tree');
  assert.equal(mergeSnapshot({ ...prev, selected: null }, t).selected, null);
});

test('hasSubtree: false for an idle seat, true with children', () => {
  assert.equal(hasSubtree(load('idle-seat')), false);
  assert.equal(hasSubtree(load('one-running')), true);
  assert.equal(hasSubtree(null), false);
});

test('workingCount counts running + waiting nodes below the seat', () => {
  assert.equal(workingCount(load('frozen-parent')), 2);
  assert.equal(workingCount(load('mixed')), 1);
  assert.equal(workingCount(load('idle-seat')), 0);
});

test('findNode finds by id at any depth', () => {
  const t = load('depth3');
  const h = byLabel(t, 'Run the test suite');
  assert.equal(findNode(t, h.agent_id)?.label, 'Run the test suite');
  assert.equal(findNode(t, 'nope'), null);
});
