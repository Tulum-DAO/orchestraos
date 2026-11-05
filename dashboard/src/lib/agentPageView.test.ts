/**
 * What the agent page shows for ?sub=<agent_id> (agent-tree drill-in): the seat's own chat, or one
 * subagent's transcript, read-only, with a banner and a way back. The seat node's id is the Claude
 * session UUID, so selecting the root must mean "the seat", never /subagents/<uuid>.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { viewFor, subParamFor, subToDrop } from './agentPageView';
import type { AgentTreeNode } from './agentTree';

const load = (name: string): AgentTreeNode =>
  JSON.parse(readFileSync(new URL(`./fixtures/agent-tree/${name}.json`, import.meta.url), 'utf8'));
const t = load('frozen-parent');
const reviewer = t.children[0];
const helper = reviewer.children[0];

test('no sub: the seat chat, writable', () => {
  assert.deepEqual(viewFor('pm-demo', null, t), { readOnly: false, title: 'pm-demo', backTo: null });
});

test('sub = the seat root (session UUID) is the seat, not a subagent', () => {
  const v = viewFor('pm-demo', t.agent_id, t);
  assert.equal(v.readOnly, false);
  assert.equal(v.subagentId, undefined);
  // even a root id that looks like a subagent id (hex only) is the seat
  const hexRoot = { ...t, agent_id: 'aaaaaa111111' };
  assert.equal(viewFor('pm-demo', 'aaaaaa111111', hexRoot).readOnly, false);
});

test('a depth-2 helper: read-only, its label, helper of its parent, back to the seat', () => {
  const v = viewFor('pm-demo', helper.agent_id, t);
  assert.equal(v.readOnly, true);
  assert.equal(v.subagentId, helper.agent_id);
  assert.equal(v.title, 'Run the test suite');
  assert.equal(v.parentLabel, 'Review the branch');
  assert.equal(v.backTo, 'pm-demo');
  assert.equal(v.banner, 'Viewing Run the test suite (helper of Review the branch), read-only.');
});

test('a depth-1 subagent is a helper of the seat (by seat name, not the session label)', () => {
  const v = viewFor('pm-demo', reviewer.agent_id, t);
  assert.equal(v.parentLabel, 'pm-demo');
});

test('a deep link before the tree loads (or after a prune) still opens read-only by id', () => {
  const v = viewFor('pm-demo', 'a0000000000000abc', null);
  assert.equal(v.readOnly, true);
  assert.equal(v.subagentId, 'a0000000000000abc');
  assert.equal(v.title, 'a0000000000000abc');
  assert.equal(v.banner, 'Viewing a0000000000000abc (a helper), read-only.');
});

test('an unsafe sub id is ignored: the seat chat', () => {
  for (const bad of ['../x', 'ZZZ', '']) assert.equal(viewFor('gm', bad, t).readOnly, false, bad);
});

test('subParamFor: clicking the root clears ?sub, any other node sets it', () => {
  assert.equal(subParamFor(t, t.agent_id), null);
  assert.equal(subParamFor(t, helper.agent_id), helper.agent_id);
});

test('subToDrop: a selection the tree once held and a later snapshot pruned is dropped; a deep link loading is kept', () => {
  const sid = helper.agent_id;
  assert.equal(subToDrop({ sub: sid, tree: t, seen: null }).drop, false);
  assert.equal(subToDrop({ sub: sid, tree: t, seen: null }).seen, sid, 'now seen in the tree');
  const pruned = { ...t, children: [] };
  assert.equal(subToDrop({ sub: sid, tree: pruned, seen: sid }).drop, true);
  assert.equal(subToDrop({ sub: sid, tree: pruned, seen: null }).drop, false, 'never seen: a deep link, kept');
  assert.equal(subToDrop({ sub: sid, tree: null, seen: sid }).drop, false, 'no tree yet (or gone): keep');
  assert.equal(subToDrop({ sub: null, tree: pruned, seen: sid }).drop, false);
});
