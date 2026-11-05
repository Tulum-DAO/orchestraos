// The reactions state machine behind useReactions (lib/reactionSync.ts): taps serialized per message and coalesced, late
// echoes ignored, a GET snapshot never clobbering newer changes, rollback touching only the viewer's own entry.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  abortRead, applyServer, beginRead, createReactionStore, finishRead, initialSync, settle, takeSend, tap, viewMap,
  type ReactionsApiLike, type SyncState,
} from './reactionSync.ts';
import { badgeGroups, type ReactionChange, type ReactionMap, type Reactor } from './reactionState.ts';
import type { PutResult } from './reactionsApi.ts';

const sam: Reactor = { kind: 'user', id: 'sam' };
const pmx: Reactor = { kind: 'agent', id: 'pm-x' };
const REF = 's1~11111111-2222-4333-8444-00000000006b#0';
const REF2 = 's1~11111111-2222-4333-8444-00000000006c#0';
const ch = (emoji: string | null, at: string, reactor: Reactor = sam, ref = REF): ReactionChange => ({ ref, reactor, emoji, at });
const ok = (change: ReactionChange): PutResult => ({ ok: true, change });
const mineIn = (s: SyncState, ref = REF) => (viewMap(s, sam)[ref] ?? []).filter((r) => r.reactor.id === 'sam').map((r) => r.emoji);

test('double tap: add then remove, one request at a time, never a re-added flicker', () => {
  let s = tap(initialSync(), REF, '👍', sam);
  let t = takeSend(s, REF, sam); s = t.state;
  assert.equal(t.send, '👍');
  assert.deepEqual(mineIn(s), ['👍']);
  s = tap(s, REF, '👍', sam);                                   // second tap while the first is in flight: removes
  assert.deepEqual(mineIn(s), []);
  t = takeSend(s, REF, sam); s = t.state;
  assert.equal(t.send, null);                                    // nothing sent while one is in flight
  assert.deepEqual(mineIn(s), []);
  const r1 = settle(s, REF, ok(ch('👍', '2026-11-02T10:00:01Z')), sam); s = r1.state;
  assert.equal(r1.send, '👍');                                   // now the removal: the PUT toggles the confirmed emoji off
  assert.deepEqual(mineIn(s), []);                               // still empty while the removal is out: no flicker
  const r2 = settle(s, REF, ok(ch(null, '2026-11-02T10:00:02Z')), sam); s = r2.state;
  assert.equal(r2.send, null);
  assert.deepEqual(mineIn(s), []);
  assert.deepEqual(s.intents, {});
  s = applyServer(s, ch('👍', '2026-11-02T10:00:01Z'));          // the first change's echo, late
  assert.deepEqual(mineIn(s), []);
});

test('late echoes: removal at 2 applied first, the add at 1 arriving after is ignored (echo2, reply2, reply1)', () => {
  let s = initialSync();
  s = applyServer(s, ch(null, '2'));
  s = applyServer(s, ch(null, '2'));
  s = applyServer(s, ch('👍', '1'));
  assert.deepEqual(s.base, {});
  // another reactor is judged on its own stamp
  s = applyServer(s, ch('❤️', '1', pmx));
  assert.deepEqual(s.base[REF].map((r) => r.emoji), ['❤️']);
  // an equal `at` is the same change again (idempotent), a newer one wins
  s = applyServer(s, ch('❤️', '1', pmx));
  s = applyServer(s, ch('🎉', '3', pmx));
  assert.deepEqual(s.base[REF].map((r) => r.emoji), ['🎉']);
});

test('taps are coalesced: only the last wish is sent once the one in flight settles', () => {
  let s = tap(initialSync(), REF, '👍', sam);
  const t = takeSend(s, REF, sam); s = t.state;
  assert.equal(t.send, '👍');
  for (const e of ['❤️', '😂', '🎉']) { s = tap(s, REF, e, sam); s = takeSend(s, REF, sam).state; }
  assert.deepEqual(mineIn(s), ['🎉']);
  const r = settle(s, REF, ok(ch('👍', '1')), sam);
  assert.equal(r.send, '🎉');
  // a wish that ends where the server already is sends nothing
  let u = tap(initialSync(), REF, '👍', sam);
  u = takeSend(u, REF, sam).state;
  u = tap(u, REF, '👍', sam);                                   // removed again before anything came back
  u = tap(u, REF, '👍', sam);                                   // and wanted again
  const r2 = settle(u, REF, ok(ch('👍', '1')), sam);
  assert.equal(r2.send, null);
  assert.deepEqual(mineIn(r2.state), ['👍']);
});

test('a GET snapshot never clobbers: changes that arrived while it was in flight are applied over it', () => {
  let s = applyServer(initialSync(), ch('👍', '1', pmx));
  const b = beginRead(s); s = b.state;
  s = applyServer(s, ch('🎉', '5', pmx, REF2));                  // arrives during the GET, which was answered before it
  s = applyServer(s, ch(null, '6', pmx));                        // and a removal
  s = finishRead(s, b.token, { [REF]: [{ emoji: '👍', reactor: pmx, at: '1' }] });
  assert.deepEqual(s.base[REF2]?.map((r) => r.emoji), ['🎉']);
  assert.equal(REF in s.base, false);
  assert.deepEqual(s.buffer, []);                                // nothing kept once no GET is out
  // a snapshot that already holds a NEWER entry than a buffered older change keeps the newer one
  let u = initialSync();
  const b2 = beginRead(u); u = b2.state;
  u = applyServer(u, ch('👍', '1', pmx));
  u = finishRead(u, b2.token, { [REF]: [{ emoji: '🎉', reactor: pmx, at: '2' }] });
  assert.deepEqual(u.base[REF].map((r) => r.emoji), ['🎉']);
  // a failed GET keeps what is shown and drops its buffer
  let v = applyServer(initialSync(), ch('👍', '1', pmx));
  const b3 = beginRead(v); v = b3.state;
  v = abortRead(applyServer(v, ch('❤️', '2', pmx)), b3.token);
  assert.deepEqual(v.base[REF].map((r) => r.emoji), ['❤️']);
  assert.deepEqual(v.buffer, []);
});

test('the viewer\'s pending wish is laid over a snapshot that does not have it yet', () => {
  let s = tap(initialSync(), REF, '👍', sam);
  s = takeSend(s, REF, sam).state;
  const b = beginRead(s); s = b.state;
  s = finishRead(s, b.token, {});
  assert.deepEqual(mineIn(s), ['👍']);
});

test('a refused request reverts only the viewer\'s own entry of that message, nobody else\'s', () => {
  const base: ReactionMap = {
    [REF]: [{ emoji: '👍', reactor: pmx, at: '1' }, { emoji: '❤️', reactor: sam, at: '2' }],
    [REF2]: [{ emoji: '🎉', reactor: sam, at: '3' }],
  };
  let s = finishRead(beginRead(initialSync()).state, 0, base);
  s = tap(s, REF, '😂', sam);
  s = takeSend(s, REF, sam).state;
  assert.deepEqual(viewMap(s, sam)[REF].map((r) => r.emoji).sort(), ['👍', '😂']);
  // meanwhile the agent changes its own reaction
  s = applyServer(s, ch('🎉', '4', pmx));
  const r = settle(s, REF, { ok: false, error: 'no' }, sam);
  assert.equal(r.error, 'no');
  assert.deepEqual(viewMap(r.state, sam)[REF].map((x) => [x.reactor.id, x.emoji]).sort(), [['pm-x', '🎉'], ['sam', '❤️']]);
  assert.deepEqual(viewMap(r.state, sam)[REF2].map((x) => x.emoji), ['🎉']);
});

test('badge order is stable from the pending placeholder to the server\'s own time', () => {
  let s = finishRead(beginRead(initialSync()).state, 0, { [REF]: [{ emoji: '🎉', reactor: pmx, at: '2026-11-02T10:00:00Z' }] });
  s = tap(s, REF, '👍', sam);
  const before = badgeGroups(viewMap(s, sam)[REF], sam).map((g) => g.emoji);
  s = takeSend(s, REF, sam).state;
  s = settle(s, REF, ok(ch('👍', '2026-11-02T10:00:05Z')), sam).state;
  assert.deepEqual(badgeGroups(viewMap(s, sam)[REF], sam).map((g) => g.emoji), before);
  assert.deepEqual(before, ['🎉', '👍']);
});

// ---- the store, with a fake API ----
function fake() {
  const puts: { ref: string; emoji: string; done: (r: PutResult) => void }[] = [];
  const subs: { h: Parameters<ReactionsApiLike['subscribe']>[0]; off: boolean }[] = [];
  const fetches: ((m: ReactionMap) => void)[] = [];
  const timers: { f: () => void; ms: number }[] = [];
  const api: ReactionsApiLike = {
    fetch: () => new Promise((res) => { fetches.push(res); }),
    put: (ref, emoji) => new Promise((res) => { puts.push({ ref, emoji, done: res }); }),
    loadMe: async () => sam,
    subscribe: (h) => { const e = { h, off: false }; subs.push(e); return () => { e.off = true; }; },
  };
  const cleared = new Set<unknown>();
  const opts = { setTimer: (f: () => void, ms: number) => { timers.push({ f, ms }); return timers.length; }, clearTimer: (t: unknown) => { cleared.add(t); } };
  return { api, puts, subs, fetches, timers, cleared, opts };
}
const tick = () => new Promise((r) => setTimeout(r, 0));

test('the store: three quick taps send two requests (the first, then the last wish); the view never flickers', async () => {
  const f = fake();
  const st = createReactionStore('pm-x', f.api, f.opts);
  const seen: string[][] = [];
  st.subscribe(() => seen.push((st.getSnapshot().map[REF] ?? []).map((r) => r.emoji)));
  const stop = st.start();
  await tick();
  f.fetches[0]({});
  await tick();
  st.toggle(REF, '👍'); st.toggle(REF, '❤️'); st.toggle(REF, '🎉');
  assert.equal(f.puts.length, 1);
  assert.deepEqual(st.getSnapshot().map[REF].map((r) => r.emoji), ['🎉']);
  f.puts[0].done(ok(ch('👍', '1')));
  await tick();
  assert.equal(f.puts.length, 2);
  assert.equal(f.puts[1].emoji, '🎉');
  f.puts[1].done(ok(ch('🎉', '2')));
  await tick();
  assert.deepEqual(st.getSnapshot().map[REF].map((r) => r.emoji), ['🎉']);
  assert.ok(seen.every((x) => x.length <= 1), JSON.stringify(seen));
  stop();
});

test('the store: a refused tap rolls back, shows why, and re-reads; the error clears by timer', async () => {
  const f = fake();
  const st = createReactionStore('pm-x', f.api, f.opts);
  const stop = st.start(); await tick(); f.fetches[0]({}); await tick();
  st.toggle(REF, '👍');
  f.puts[0].done({ ok: false, error: 'nope' });
  await tick();
  assert.equal(st.getSnapshot().error, 'nope');
  assert.deepEqual(st.getSnapshot().map, {});
  assert.equal(f.fetches.length, 2);                              // re-read
  f.timers.find((t) => t.ms === 5000)!.f();
  assert.equal(st.getSnapshot().error, null);
  stop();
});

test('the store: a stream the browser closed is replaced after a pause, and the conversation is read again', async () => {
  const f = fake();
  const st = createReactionStore('pm-x', f.api, f.opts);
  const stop = st.start(); await tick(); f.fetches[0]({}); await tick();
  f.subs[0].h.onClosed?.();
  assert.equal(f.subs.length, 1);
  f.timers.find((t) => t.ms === 3000)!.f();
  assert.equal(f.subs.length, 2);
  assert.equal(f.fetches.length, 2);
  f.subs[1].h.onChange(ch('👍', '1', pmx));
  assert.deepEqual(st.getSnapshot().map[REF].map((r) => r.emoji), ['👍']);
  stop();
  assert.equal(f.subs[1].off, true);
});

test('the store: closed-stream retries back off 3s, 6s, 12s ... capped at 60s, and reset once a stream delivers', async () => {
  const f = fake();
  const st = createReactionStore('pm-x', f.api, f.opts);
  const stop = st.start(); await tick();
  const waits: number[] = [];
  for (let i = 0; i < 7; i++) {
    f.subs[f.subs.length - 1].h.onClosed?.();
    const t = f.timers.filter((x) => ![5000, 30000].includes(x.ms)).pop()!;
    waits.push(t.ms);
    t.f();
  }
  assert.deepEqual(waits, [3000, 6000, 12000, 24000, 48000, 60000, 60000]);
  f.subs[f.subs.length - 1].h.onChange(ch('👍', '1', pmx));                // a live stream: healthy again
  f.subs[f.subs.length - 1].h.onClosed?.();
  assert.equal(f.timers.filter((x) => ![5000, 30000].includes(x.ms)).pop()!.ms, 3000);
  // a stream that stayed open long enough resets it too
  const t = f.timers.filter((x) => ![5000, 30000].includes(x.ms)).pop()!; t.f();
  f.timers.filter((x) => x.ms === 30000).pop()!.f();
  f.subs[f.subs.length - 1].h.onClosed?.();
  assert.equal(f.timers.filter((x) => ![5000, 30000].includes(x.ms)).pop()!.ms, 3000);
  stop();
});

test('the store: a GET or PUT answered after stop() leaves nothing stuck; stop then start works as new', async () => {
  const f = fake();
  const st = createReactionStore('pm-x', f.api, f.opts);
  let stop = st.start(); await tick(); f.fetches[0]({}); await tick();
  st.toggle(REF, '👍');                                           // PUT 0 in flight
  stop();
  stop = st.start(); await tick();                                // a GET (fetches[1]) is out for the new run
  f.puts[0].done(ok(ch('👍', '1')));                              // the old run's answers arrive late
  f.fetches[1]({});
  await tick();
  assert.deepEqual(st.getSnapshot().map, {});                     // ignored: the new run's GET is still out
  st.toggle(REF, '❤️');                                           // not blocked by the old in-flight request
  assert.equal(f.puts.length, 2);
  assert.equal(f.puts[1].emoji, '❤️');
  f.fetches[2]?.({});
  f.puts[1].done(ok(ch('❤️', '2')));
  await tick();
  assert.deepEqual(st.getSnapshot().map[REF].map((r) => r.emoji), ['❤️']);
  // changes arrive and apply (no GET left open to buffer them forever)
  f.subs[f.subs.length - 1].h.onChange(ch('🎉', '3', pmx));
  assert.equal(st.getSnapshot().map[REF].length, 2);
  stop();
});

test('the store: timers of a stopped run do nothing after stop then start (no extra subscription)', async () => {
  const f = fake();
  const st = createReactionStore('pm-x', f.api, f.opts);
  let stop = st.start(); await tick();
  f.subs[0].h.onClosed?.();
  f.subs[0].h.onClosed?.();                                       // twice: still one retry waiting
  const old = f.timers.filter((x) => x.ms !== 5000);
  assert.equal(old.filter((x) => x.ms === 3000).length, 1);
  stop();
  stop = st.start(); await tick();
  assert.equal(f.subs.length, 2);
  old.forEach((t) => t.f());                                      // the old run's timers fire anyway (a missed clear)
  assert.equal(f.subs.length, 2);
  stop();
});
