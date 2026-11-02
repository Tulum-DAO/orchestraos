/**
 * The reaction wake (spec "Agent wake"): a USER add or change on agent-chat, debounced 3 s per (agent, ref), injected
 * with no hold and no force; a busy refusal is retried ONCE after 30 s, then dropped. Removals and agent reactions
 * are never delivered. A fake clock drives every timer.
 *
 * Run: ORCHESTRA_CONFIG=../orchestra.example.toml npx tsx --test src/services/reaction-wake.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { ReactionChange } from '../lib/reactions-store.js';
import type { GatewayInjectResult } from '../routes/agent-send.js';
import { createReactionWake, startReactionWake, messageTextIn, wakeOutcome, WAKE_DEBOUNCE_MS, WAKE_RETRY_MS,
  type WakeDeps } from './reaction-wake.js';
import { publishReaction } from './reaction-bus.js';

const flush = async () => { for (let k = 0; k < 5; k++) await new Promise((r) => setImmediate(r)); };

function clock() {
  let now = 0;
  let seq = 0;
  const timers = new Map<number, { at: number; fn: () => void }>();
  return {
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

const DELIVERED: GatewayInjectResult = { httpStatus: 200, ok: true, delivered: true };
const COMPOSER: GatewayInjectResult = { httpStatus: 409, ok: false, reason: 'busy', composer_text: 'draft' };

function harness(texts: Record<string, string> = { 's1~a1#0': 'Deployed the fix to staging.' }) {
  const c = clock();
  const sent: { session: string; text: string }[] = [];
  const replies: GatewayInjectResult[] = [];
  const deps: WakeDeps = {
    agentExists: (id) => id !== 'ghost',
    resolveSession: (id) => `tmux-${id}`,
    inject: async (session, text) => { sent.push({ session, text }); return replies.shift() ?? DELIVERED; },
    messageText: (_agent, ref) => texts[ref] ?? null,
    setTimer: c.setTimer,
    clearTimer: c.clearTimer,
    log: () => {},
  };
  return { c, sent, replies, deps, wake: createReactionWake(deps) };
}

const ch = (emoji: string | null, over: Partial<ReactionChange> = {}): ReactionChange => ({
  surface: 'agent-chat', conv: 'pm-x', ref: 's1~a1#0', reactor: { kind: 'user', id: 'sam' }, emoji,
  at: '2026-11-02T10:00:00Z', ...over,
});

test('a user reaction reaches the agent 3 s later, as the exact wake line, to its session', async () => {
  const h = harness();
  h.wake.onChange(ch('👍'));
  await h.c.advance(WAKE_DEBOUNCE_MS - 1);
  assert.equal(h.sent.length, 0);
  await h.c.advance(1);
  assert.deepEqual(h.sent, [{ session: 'tmux-pm-x',
    text: '<reaction from="user:sam" emoji="👍" on="Deployed the fix to staging."/> You may reply, react (`orchestra react`), or do nothing.' }]);
  assert.equal(h.wake.pendingCount(), 0);
});

test('debounce per (agent, message): a change within 3 s sends once, the newest emoji', async () => {
  const h = harness();
  h.wake.onChange(ch('👍'));
  await h.c.advance(1000);
  h.wake.onChange(ch('❤️'));
  await h.c.advance(WAKE_DEBOUNCE_MS - 1);
  assert.equal(h.sent.length, 0);
  await h.c.advance(1);
  assert.equal(h.sent.length, 1);
  assert.ok(h.sent[0].text.includes('emoji="❤️"'));
});

test('two messages are two wakes', async () => {
  const h = harness({ 's1~a1#0': 'one', 's1~a2#0': 'two' });
  h.wake.onChange(ch('👍'));
  h.wake.onChange(ch('🎉', { ref: 's1~a2#0' }));
  await h.c.advance(WAKE_DEBOUNCE_MS);
  assert.deepEqual(h.sent.map((s) => /on="([^"]*)"/.exec(s.text)?.[1]), ['one', 'two']);
});

test('never delivered: a removal (and it cancels one not sent yet), an agent reaction, another surface', async () => {
  const h = harness();
  h.wake.onChange(ch(null));
  h.wake.onChange(ch('👍'));
  await h.c.advance(1000);
  h.wake.onChange(ch(null));
  h.wake.onChange(ch('👍', { reactor: { kind: 'agent', id: 'pm-x' } }));
  h.wake.onChange(ch('👍', { surface: 'arturo', conv: 't1', ref: '3' }));
  await h.c.advance(WAKE_DEBOUNCE_MS * 3);
  assert.equal(h.sent.length, 0);
  assert.equal(h.wake.pendingCount(), 0);
});

test('composer text: retried once after 30 s, then delivered', async () => {
  const h = harness();
  h.replies.push(COMPOSER);
  h.wake.onChange(ch('👍'));
  await h.c.advance(WAKE_DEBOUNCE_MS);
  assert.equal(h.sent.length, 1);
  await h.c.advance(WAKE_RETRY_MS - 1);
  assert.equal(h.sent.length, 1);
  await h.c.advance(1);
  assert.equal(h.sent.length, 2);
  assert.equal(h.wake.pendingCount(), 0);
});

test('refused twice: dropped, no third try', async () => {
  const h = harness();
  h.replies.push(COMPOSER, COMPOSER);
  h.wake.onChange(ch('👍'));
  await h.c.advance(WAKE_DEBOUNCE_MS + WAKE_RETRY_MS * 4);
  assert.equal(h.sent.length, 2);
  assert.equal(h.wake.pendingCount(), 0);
});

test('maybe typed (unverified submit, timeout): never sent twice', async () => {
  for (const r of [{ httpStatus: 502, ok: false, reason: 'unverified_submit' }, { httpStatus: 0, ok: false, timedOut: true }]) {
    const h = harness();
    h.replies.push(r as GatewayInjectResult);
    h.wake.onChange(ch('👍'));
    await h.c.advance(WAKE_DEBOUNCE_MS + WAKE_RETRY_MS * 2);
    assert.equal(h.sent.length, 1, JSON.stringify(r));
  }
});

test('a new reaction while a retry waits replaces it', async () => {
  const h = harness();
  h.replies.push(COMPOSER);
  h.wake.onChange(ch('👍'));
  await h.c.advance(WAKE_DEBOUNCE_MS);
  h.wake.onChange(ch('🎉'));
  await h.c.advance(WAKE_DEBOUNCE_MS);
  assert.equal(h.sent.length, 2);
  assert.ok(h.sent[1].text.includes('emoji="🎉"'));
  await h.c.advance(WAKE_RETRY_MS * 2);
  assert.equal(h.sent.length, 2, 'the old retry never fires');
});

test('an unknown agent gets nothing; a message the chat cannot find gets on=""', async () => {
  const h = harness({});
  h.wake.onChange(ch('👍', { conv: 'ghost' }));
  h.wake.onChange(ch('👍', { ref: 's1~zz#0' }));
  await h.c.advance(WAKE_DEBOUNCE_MS);
  assert.equal(h.sent.length, 1);
  assert.ok(h.sent[0].text.includes(' on=""/>'));
});

test('wakeOutcome classes', () => {
  assert.equal(wakeOutcome(DELIVERED), 'delivered');
  assert.equal(wakeOutcome(COMPOSER), 'retry');
  assert.equal(wakeOutcome({ httpStatus: 409, ok: false, reason: 'busy', state: 'waiting' }), 'retry');
  assert.equal(wakeOutcome({ httpStatus: 0, ok: false, unreachable: true }), 'retry');
  assert.equal(wakeOutcome({ httpStatus: 502, ok: false, reason: 'initializing' }), 'retry');
  assert.equal(wakeOutcome({ httpStatus: 502, ok: false, reason: 'unverified_submit' }), 'drop');
  assert.equal(wakeOutcome({ httpStatus: 0, ok: false, timedOut: true }), 'drop');
  assert.equal(wakeOutcome({ httpStatus: 404, ok: false, error: 'no such session' }), 'drop');
});

test('messageTextIn: by ref; a carried-over message by its ~uuid#part tail; else null', () => {
  const items = [
    { kind: 'text', role: 'assistant', text: 'old copy', ref: 'sOld~a1#0' },
    { kind: 'text', role: 'user', text: 'carried', ref: 'sNew~u9#0' },
    { kind: 'tool_use', tool: 'Bash', ref: 'sNew~t1#0' },
  ];
  assert.equal(messageTextIn(items, 'sOld~a1#0'), 'old copy');
  assert.equal(messageTextIn(items, 'sGone~u9#0'), 'carried');
  assert.equal(messageTextIn(items, 'sNew~t1#0'), null);
  assert.equal(messageTextIn(items, 'sNew~nope#0'), null);
});

test('startReactionWake listens on the reaction bus until stopped', async () => {
  const h = harness();
  const stop = startReactionWake(h.deps);
  publishReaction(ch('👍'));
  await h.c.advance(WAKE_DEBOUNCE_MS);
  assert.equal(h.sent.length, 1);
  stop();
  publishReaction(ch('🎉'));
  await h.c.advance(WAKE_DEBOUNCE_MS * 2);
  assert.equal(h.sent.length, 1);
});
