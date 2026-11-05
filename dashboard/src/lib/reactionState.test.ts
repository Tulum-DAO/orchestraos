// Message reactions on the client (spec 2026-11-02-message-reactions-design.md, build step 2): one emoji per reactor,
// the same again removes it, badges grouped with counts, the viewer's own marked, carried-over rows keep theirs.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  applyChange, badgeGroups, nextEmoji, resolveRef, restoreRef, whoReacted, refTail, QUICK_EMOJI,
  type ReactionMap, type Reactor,
} from './reactionState.ts';

const sam: Reactor = { kind: 'user', id: 'sam' };
const pmx: Reactor = { kind: 'agent', id: 'pm-x' };
const dev: Reactor = { kind: 'agent', id: 'dev' };
const U = '11111111-2222-4333-8444-00000000006b';
const REF = `s2~${U}#0`;

test('the quick row is the six defaults, in order', () => {
  assert.deepEqual([...QUICK_EMOJI], ['👍', '👎', '❤️', '😂', '🎉', '😮']);
});

test('a change sets one emoji per reactor: another replaces it, null removes it, the last removal drops the ref', () => {
  let m: ReactionMap = {};
  m = applyChange(m, { ref: REF, reactor: sam, emoji: '👍', at: '1' });
  m = applyChange(m, { ref: REF, reactor: pmx, emoji: '👍', at: '2' });
  m = applyChange(m, { ref: REF, reactor: sam, emoji: '🎉', at: '3' });
  assert.deepEqual(m[REF].map((r) => [r.reactor.id, r.emoji]), [['pm-x', '👍'], ['sam', '🎉']]);
  m = applyChange(m, { ref: REF, reactor: sam, emoji: null, at: '4' });
  m = applyChange(m, { ref: REF, reactor: pmx, emoji: null, at: '5' });
  assert.equal(REF in m, false);
});

test('the PUT answer and its stream echo apply as one (idempotent), and the input map is never changed', () => {
  const m0: ReactionMap = {};
  const c = { ref: REF, reactor: sam, emoji: '👍', at: '1' };
  const once = applyChange(m0, c);
  assert.deepEqual(applyChange(once, c), once);
  assert.deepEqual(m0, {});
});

test('a tap on my own emoji removes it; any other replaces it', () => {
  const list = [{ emoji: '👍', reactor: sam, at: '1' }, { emoji: '❤️', reactor: pmx, at: '2' }];
  assert.equal(nextEmoji(list, sam, '👍'), null);
  assert.equal(nextEmoji(list, sam, '❤️'), '❤️');
  assert.equal(nextEmoji([], sam, '😮'), '😮');
});

test('restoreRef puts one message back as it was (rollback), empty removes it', () => {
  const before = [{ emoji: '👍', reactor: sam, at: '1' }];
  const after = applyChange({ [REF]: before }, { ref: REF, reactor: sam, emoji: null, at: '2' });
  assert.deepEqual(restoreRef(after, REF, before), { [REF]: before });
  assert.deepEqual(restoreRef({ [REF]: before }, REF, []), {});
});

test('badges: one per emoji in first-given order, a count, mine marked, who reacted', () => {
  const list = [
    { emoji: '🎉', reactor: dev, at: '2026-11-02T10:00:03Z' },
    { emoji: '👍', reactor: pmx, at: '2026-11-02T10:00:01Z' },
    { emoji: '👍', reactor: sam, at: '2026-11-02T10:00:02Z' },
  ];
  const g = badgeGroups(list, sam);
  assert.deepEqual(g.map((x) => [x.emoji, x.count, x.mine]), [['👍', 2, true], ['🎉', 1, false]]);
  assert.equal(whoReacted(g[0], sam), 'You and pm-x');
  assert.equal(whoReacted(g[1], sam, (r) => (r.id === 'dev' ? 'Dev' : r.id)), 'Dev');
  assert.equal(badgeGroups(list, null).some((x) => x.mine), false);
});

test('who reacted, many: two names and the rest counted', () => {
  const reactors: Reactor[] = ['a', 'b', 'c', 'd', 'e'].map((id) => ({ kind: 'agent', id }));
  assert.equal(whoReacted({ emoji: '👍', count: 5, mine: false, reactors }, null), 'a, b and 3 others');
  assert.equal(whoReacted({ emoji: '👍', count: 3, mine: false, reactors: reactors.slice(0, 3) }, null), 'a, b and c');
});

test('refTail splits <session>~<uuid>#<part>; anything else is not a ref', () => {
  assert.deepEqual(refTail(REF), { tail: `~${U}#0`, uuid: U });
  for (const bad of ['', 'abc', '~u#0', 's~#0', 's~u']) assert.equal(refTail(bad), null, bad);
});

test('a row shows its own reactions; a carried-over Claude message shows the older copy\'s and writes there', () => {
  const old = `s1~${U}#0`;
  const m: ReactionMap = { [old]: [{ emoji: '👍', reactor: sam, at: '1' }] };
  assert.deepEqual(resolveRef(REF, m, new Set([REF])), { ref: old, list: m[old] });
  // its own wins
  const both: ReactionMap = { ...m, [REF]: [{ emoji: '🎉', reactor: sam, at: '2' }] };
  assert.equal(resolveRef(REF, both, new Set([REF])).ref, REF);
  // the older copy is on screen itself: it keeps its own
  assert.deepEqual(resolveRef(REF, m, new Set([REF, old])), { ref: REF, list: [] });
  // a codex ordinal repeats across generations by chance: never borrowed
  const codex: ReactionMap = { 's1~17#0': [{ emoji: '👍', reactor: sam, at: '1' }] };
  assert.deepEqual(resolveRef('s2~17#0', codex, new Set(['s2~17#0'])), { ref: 's2~17#0', list: [] });
  // another part of the same line is another message
  assert.deepEqual(resolveRef(`s2~${U}#1`, m, new Set()), { ref: `s2~${U}#1`, list: [] });
});

test("resolveRef finds a carried-over copy through a tail index built once per map", () => {
  const old = `s1~${U}#0`;
  const m: ReactionMap = { [old]: [{ emoji: "👍", reactor: sam, at: "1" }], other: [{ emoji: "🎉", reactor: sam, at: "1" }] };
  assert.equal(resolveRef(REF, m, new Set()).ref, old);
  assert.equal(resolveRef(REF, m, new Set()).ref, old);            // second row, same map
  assert.equal(resolveRef(REF, { [old]: [] }, new Set()).list.length, 0);
});
