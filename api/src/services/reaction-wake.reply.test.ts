/**
 * The agent's emoji answer to a reaction wake becomes a real reaction (follow-up to spec "Agent wake"): after a wake
 * is delivered, the agent's first text after that wake is watched for (every 2 s, up to 90 s); one emoji is stored
 * as the agent's reaction on the SAME ref the operator reacted to and published on the bus. NO_REPLY, a no-reply
 * phrase or real words: nothing. Never over an agent reaction already there. A newer wake for the agent, and stop,
 * end the watch. A fake clock drives every timer.
 *
 * Run: ORCHESTRA_CONFIG=../orchestra.example.toml npx tsx --test src/services/reaction-wake.reply.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { ReactionChange, ReactionKey, StoredReaction } from '../lib/reactions-store.js';
import { createReactionWake, WAKE_DEBOUNCE_MS, REPLY_POLL_MS, REPLY_WATCH_MS, type WakeDeps } from './reaction-wake.js';

const flush = async () => { for (let k = 0; k < 5; k++) await new Promise((r) => setImmediate(r)); };

function clock() {
  let now = 0;
  let seq = 0;
  const timers = new Map<number, { at: number; fn: () => void }>();
  return {
    pending: () => timers.size,
    setTimer: (fn: () => void, ms: number) => { const id = ++seq; timers.set(id, { at: now + ms, fn }); return id; },
    clearTimer: (h: unknown) => { timers.delete(h as number); },
    async advance(ms: number) {
      const until = now + ms;
      for (;;) {
        const due = [...timers.entries()].filter(([, t]) => t.at <= until).sort((a, b) => a[1].at - b[1].at)[0];
        if (!due) break;
        timers.delete(due[0]);
        now = due[1].at;
        due[1].fn();
        await flush();
      }
      now = until;
    },
  };
}

const REF = 's1~a1#0';
const base = () => [
  { kind: 'text', role: 'user', text: 'Ship it', uuid: 'u1', ref: 's1~u1#0' },
  { kind: 'text', role: 'assistant', text: 'Deployed.', uuid: 'a1', ref: REF },
];
const wakeItem = (uuid: string) => ({ kind: 'text', role: 'user', text: '', reaction_wake: true, uuid, ref: `s1~${uuid}#0` });
const said = (uuid: string, text: string) => ({ kind: 'text', role: 'assistant', text, uuid, ref: `s1~${uuid}#0` });

function harness() {
  const c = clock();
  const items: any[] = base();
  const stored: Record<string, StoredReaction[]> = {};
  const toggled: { key: ReactionKey; emoji: string }[] = [];
  const published: ReactionChange[] = [];
  const deps: WakeDeps = {
    agentExists: () => true,
    resolveSession: (id) => `tmux-${id}`,
    inject: async () => ({ httpStatus: 200, ok: true, delivered: true }),
    messageText: () => 'Deployed.',
    chatItems: () => items.slice(),
    reactions: {
      list: () => stored,
      toggle: (key, emoji) => {
        toggled.push({ key, emoji });
        (stored[key.ref] ??= []).push({ emoji, reactor: key.reactor, at: 'now' });
        return { ...key, emoji, at: 'now' };
      },
    },
    publish: (ch) => { published.push(ch); },
    setTimer: c.setTimer,
    clearTimer: c.clearTimer,
    log: () => {},
  };
  return { c, items, stored, toggled, published, deps, wake: createReactionWake(deps) };
}

const user = (emoji: string, ref = REF): ReactionChange => ({
  surface: 'agent-chat', conv: 'pm-x', ref, reactor: { kind: 'user', id: 'sam' }, emoji, at: '2026-11-02T10:00:00Z' });

test('constants: every 2 s, up to 90 s', () => {
  assert.equal(REPLY_POLL_MS, 2000);
  assert.equal(REPLY_WATCH_MS, 90_000);
});

test('an emoji answer becomes the agent\'s reaction on the SAME ref, published like the PUT route', async () => {
  const h = harness();
  h.wake.onChange(user('👍'));
  await h.c.advance(WAKE_DEBOUNCE_MS);                 // delivered; the wake is not in the log yet
  await h.c.advance(REPLY_POLL_MS);
  assert.equal(h.toggled.length, 0);
  h.items.push(wakeItem('w1'), said('r1', '🙏'));
  await h.c.advance(REPLY_POLL_MS);
  assert.deepEqual(h.toggled, [{ key: { surface: 'agent-chat', conv: 'pm-x', ref: REF, reactor: { kind: 'agent', id: 'pm-x' } }, emoji: '🙏' }]);
  assert.deepEqual(h.published.map((p) => [p.ref, p.reactor.kind, p.emoji]), [[REF, 'agent', '🙏']]);
  await h.c.advance(REPLY_WATCH_MS);
  assert.equal(h.toggled.length, 1, 'once');
  assert.equal(h.c.pending(), 0, 'the watch is over: no timer left');
});

test('NO_REPLY, a no-reply phrase, real words: nothing stored, the watch ends', async () => {
  for (const text of ['NO_REPLY', '(no reply needed)', 'Thanks! I will tag it.']) {
    const h = harness();
    h.wake.onChange(user('👍'));
    await h.c.advance(WAKE_DEBOUNCE_MS);
    h.items.push(wakeItem('w1'), said('r1', text), said('r2', '🎉'));
    await h.c.advance(REPLY_POLL_MS);
    assert.equal(h.toggled.length, 0, text);
    assert.equal(h.c.pending(), 0, text);
  }
});

test('only the agent\'s FIRST text after THIS wake: an older wake\'s emoji answer and a tool call do not count', async () => {
  const h = harness();
  h.items.push(wakeItem('w0'), said('r0', '👀'));      // an earlier wake, already answered
  h.wake.onChange(user('👍'));
  await h.c.advance(WAKE_DEBOUNCE_MS + REPLY_POLL_MS);
  assert.equal(h.toggled.length, 0);
  h.items.push(wakeItem('w1'), { kind: 'tool_use', tool: 'Bash', uuid: 't1' });
  await h.c.advance(REPLY_POLL_MS);
  assert.equal(h.toggled.length, 0);
  h.items.push(said('r1', '🎉'));
  await h.c.advance(REPLY_POLL_MS);
  assert.deepEqual(h.toggled.map((t) => t.emoji), ['🎉']);
});

test('the operator typing before the agent answers ends the watch', async () => {
  const h = harness();
  h.wake.onChange(user('👍'));
  await h.c.advance(WAKE_DEBOUNCE_MS);
  h.items.push(wakeItem('w1'), { kind: 'text', role: 'user', text: 'also this', uuid: 'u2' }, said('r1', '👍'));
  await h.c.advance(REPLY_POLL_MS);
  assert.equal(h.toggled.length, 0);
});

test('idempotent: an agent reaction already on the ref (orchestra react) is never overwritten', async () => {
  const h = harness();
  h.wake.onChange(user('👍'));
  await h.c.advance(WAKE_DEBOUNCE_MS);
  h.stored[REF] = [{ emoji: '👍', reactor: { kind: 'user', id: 'sam' }, at: 'x' }, { emoji: '❤️', reactor: { kind: 'agent', id: 'pm-x' }, at: 'y' }];
  h.items.push(wakeItem('w1'), said('r1', '🙏'));
  await h.c.advance(REPLY_POLL_MS);
  assert.equal(h.toggled.length, 0);
  assert.equal(h.published.length, 0);
});

test('gives up after 90 s', async () => {
  const h = harness();
  h.wake.onChange(user('👍'));
  await h.c.advance(WAKE_DEBOUNCE_MS);
  await h.c.advance(REPLY_WATCH_MS);
  assert.equal(h.c.pending(), 0);
  h.items.push(wakeItem('w1'), said('r1', '🙏'));
  await h.c.advance(REPLY_POLL_MS * 3);
  assert.equal(h.toggled.length, 0);
});

test('a newer wake for the same agent supersedes the watch: its answer goes to the NEW ref only', async () => {
  const h = harness();
  h.wake.onChange(user('👍'));
  await h.c.advance(WAKE_DEBOUNCE_MS);
  h.wake.onChange(user('🎉', 's1~u1#0'));
  await h.c.advance(WAKE_DEBOUNCE_MS);
  h.items.push(wakeItem('w1'), wakeItem('w2'), said('r2', '🙌'));
  await h.c.advance(REPLY_POLL_MS);
  assert.deepEqual(h.toggled.map((t) => [t.key.ref, t.emoji]), [['s1~u1#0', '🙌']]);
});

test('stop cancels the watch', async () => {
  const h = harness();
  h.wake.onChange(user('👍'));
  await h.c.advance(WAKE_DEBOUNCE_MS);
  h.wake.stop();
  assert.equal(h.c.pending(), 0);
  h.items.push(wakeItem('w1'), said('r1', '🙏'));
  await h.c.advance(REPLY_POLL_MS * 2);
  assert.equal(h.toggled.length, 0);
});

test('without chatItems/reactions deps there is no watch (unchanged behaviour)', async () => {
  const h = harness();
  const c = clock();
  const w = createReactionWake({ agentExists: () => true, resolveSession: (id) => id, messageText: () => '',
    inject: async () => ({ httpStatus: 200, ok: true, delivered: true }), setTimer: c.setTimer, clearTimer: c.clearTimer, log: () => {} });
  w.onChange(user('👍'));
  await c.advance(WAKE_DEBOUNCE_MS);
  assert.equal(c.pending(), 0);
  void h;
});

test('the wake lands in the log BEFORE inject resolves (Claude writes it at submit): its emoji answer is still stored', async () => {
  const h = harness();
  const w = createReactionWake({ ...h.deps, inject: async () => { h.items.push(wakeItem('w1')); return { httpStatus: 200, ok: true, delivered: true }; } });
  w.onChange(user('👍'));
  await h.c.advance(WAKE_DEBOUNCE_MS);
  h.items.push(said('r1', '🙏'));
  await h.c.advance(REPLY_POLL_MS);
  assert.deepEqual(h.toggled.map((t) => [t.key.ref, t.emoji]), [[REF, '🙏']]);
});

test('a retried wake: the snapshot is taken before the attempt that delivered it', async () => {
  const h = harness();
  let n = 0;
  const w = createReactionWake({ ...h.deps, inject: async () => {
    if (n++ === 0) { h.items.push(wakeItem('w0'), said('r0', '👀')); return { httpStatus: 409, ok: false, reason: 'busy' }; }
    h.items.push(wakeItem('w1'));
    return { httpStatus: 200, ok: true, delivered: true };
  } });
  w.onChange(user('👍'));
  await h.c.advance(WAKE_DEBOUNCE_MS);
  await h.c.advance(30_000);
  assert.equal(h.toggled.length, 0, 'the earlier, unrelated wake answer is not ours');
  h.items.push(said('r1', '🙏'));
  await h.c.advance(REPLY_POLL_MS);
  assert.deepEqual(h.toggled.map((t) => t.emoji), ['🙏']);
});
