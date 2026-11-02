// The reaction wake (spec "Agent wake"). The operator's reaction in an agent's chat reaches that agent as one line
// (lib/reaction-wake-text.ts) so it can reply, react back (`orchestra react`) or answer NO_REPLY:
//   - only a USER add or change on surface agent-chat; a removal is never delivered (and cancels a wake not sent yet);
//     an agent's own reaction never wakes anyone;
//   - debounced 3 s per (agent, message): picking 👍 then ❤️ sends one wake, with ❤️;
//   - injected through the gateway's /agent-message with NO hold and NO force (agent-send.ts postAgentMessage,
//     hold:false). Not handleAgentSend: its fallback stores the wake as mail from the operator and its composer-hold
//     409 collides with the operator typing;
//   - refused while nothing was typed (the pane holds composer text, a menu, the CLI is starting, the gateway is
//     down): retried ONCE after 30 s, then dropped. The reaction stays stored and shown; the agent never hears of it.
//     A refusal where the text may have been typed (unverified submit, timeout) is never sent again.
//   - once delivered, the agent's FIRST text after that wake is watched for (chat items every 2 s, up to 90 s): one
//     emoji (the old habit of answering a reaction with a 👍 message) is stored as the agent's own reaction on the
//     message the operator reacted to, and published like the PUT route, so every view shows a badge; the chat hides
//     the text (chat-transcript markWakeReplies). NO_REPLY, a "no reply" phrase or real words: nothing. Never over an
//     agent reaction already there (`orchestra react` won). A newer wake for the agent, and stop, end the watch.
// Express-free and dependency-injected (timers too) so it is unit-tested; server.ts wires the real deps.
import type { ReactionChange, ReactionKey, StoredReaction, Surface } from '../lib/reactions-store.js';
import type { GatewayInjectResult } from '../routes/agent-send.js';
import { reactionWakeText, silentReply } from '../lib/reaction-wake-text.js';
import { onReaction, publishReaction } from './reaction-bus.js';

export const WAKE_DEBOUNCE_MS = 3000;
export const WAKE_RETRY_MS = 30_000;
export const REPLY_POLL_MS = 2000;
export const REPLY_WATCH_MS = 90_000;

export interface WakeDeps {
  agentExists(agentId: string): boolean;
  /** agent id -> tmux session (registry tmux_session, else the id). */
  resolveSession(agentId: string): string;
  /** The gateway's /agent-message with no hold and no force. */
  inject(session: string, text: string): Promise<GatewayInjectResult>;
  /** The reacted message's text, or null when the chat does not have it. */
  messageText(agentId: string, ref: string): string | null;
  /** The agent's chat items (chat-transcript.ts chatItemsFor: wake turns flagged reaction_wake). Without it, or
   *  without `reactions`, an emoji answer is not turned into a reaction. */
  chatItems?(agentId: string): readonly any[];
  /** The reactions store (lib/reactions-store.ts ReactionStore). */
  reactions?: {
    list(surface: Surface, conv: string): Record<string, StoredReaction[]>;
    toggle(key: ReactionKey, emoji: string): ReactionChange;
  };
  /** The reaction bus (default services/reaction-bus.ts publishReaction). */
  publish?(change: ReactionChange): void;
  setTimer?(fn: () => void, ms: number): unknown;
  clearTimer?(handle: unknown): void;
  log?(line: string): void;
}

export type WakeOutcome = 'delivered' | 'retry' | 'drop';

/** delivered; retry (nothing was typed: a 409 refusal, the CLI starting, the gateway unreachable); drop (anything
 *  else, and above all a refusal where the text may have been typed). */
export function wakeOutcome(r: GatewayInjectResult): WakeOutcome {
  if (r.ok && r.delivered) return 'delivered';
  if (r.timedOut || r.reason === 'unverified_submit') return 'drop';
  if (r.httpStatus === 409 || r.unreachable || r.reason === 'initializing') return 'retry';
  return 'drop';
}

/** The text of the message `ref` names in an agent's chat items (chat-transcript.ts chatItemsFor, refs stamped).
 *  A message carried into a rebuilt session keeps its uuid but changes session, so the `~<uuid>#<part>` tail is
 *  matched too (newest first). null when nothing matches or the item has no text (a tool call). */
export function messageTextIn(items: readonly any[], ref: string): string | null {
  const textOf = (it: any): string | null => (it && typeof it.text === 'string' ? it.text : null);
  const exact = items.find((it) => it?.ref === ref);
  if (exact) return textOf(exact);
  const tilde = ref.indexOf('~');
  if (tilde < 0) return null;
  const tail = ref.slice(tilde);
  for (let i = items.length - 1; i >= 0; i--) {
    const r = items[i]?.ref;
    if (typeof r === 'string' && r.endsWith(tail)) return textOf(items[i]);
  }
  return null;
}

interface Pending { change: ReactionChange; timer: unknown }

const wakeId = (it: any, i: number): string => String(it?.uuid ?? it?.ref ?? `#${i}`);
const isWake = (it: any): boolean => it?.kind === 'text' && it.role === 'user' && it.reaction_wake === true;

/** The ids of the wake turns in a chat. */
export function wakeIds(items: readonly any[]): Set<string> {
  const ids = new Set<string>();
  items.forEach((it, i) => { if (isWake(it)) ids.add(wakeId(it, i)); });
  return ids;
}

/** Where the watched wake stands in the chat: 'unseen' (not in the log yet: the (skip+1)-th wake not in `before`),
 *  'waiting' (seen, no answer yet), the agent's first text after it, or 'none' (the next user turn came first). */
export function wakeAnswer(items: readonly any[], before: ReadonlySet<string>, skip: number):
    'unseen' | 'waiting' | 'none' | { text: string } {
  let n = 0;
  let at = -1;
  for (let i = 0; i < items.length; i++) {
    if (!isWake(items[i]) || before.has(wakeId(items[i], i))) continue;
    if (n++ === skip) { at = i; break; }
  }
  if (at < 0) return 'unseen';
  for (let i = at + 1; i < items.length; i++) {
    const it = items[i];
    if (it?.kind !== 'text') continue;
    if (it.role === 'user') return 'none';
    if (it.role === 'assistant' && typeof it.text === 'string' && it.text.trim()) return { text: it.text };
  }
  return 'waiting';
}

interface Watch { change: ReactionChange; before: Set<string>; skip: number; seen: boolean; timer: unknown; waited: number }

const why = (r: GatewayInjectResult) => String(r.reason || r.state || r.error || r.httpStatus);

export function createReactionWake(deps: WakeDeps) {
  const setTimer = deps.setTimer ?? ((fn: () => void, ms: number) => { const t = setTimeout(fn, ms); t.unref?.(); return t; });
  const clearTimer = deps.clearTimer ?? ((h: unknown) => clearTimeout(h as NodeJS.Timeout));
  const log = deps.log ?? ((line: string) => console.log(`[reaction-wake] ${line}`));
  const pending = new Map<string, Pending>();
  const watches = new Map<string, Watch>();          // agent -> the watch for its newest delivered wake
  const publish = deps.publish ?? publishReaction;

  const endWatch = (agent: string, w?: Watch) => {
    const cur = watches.get(agent);
    if (cur && (!w || cur === w)) { clearTimer(cur.timer); watches.delete(agent); }
  };

  function react(c: ReactionChange, emoji: string): void {
    const r = deps.reactions!;
    const reactor = { kind: 'agent' as const, id: c.conv };
    const there = (r.list('agent-chat', c.conv)[c.ref] ?? []).some((x) => x.reactor.kind === 'agent' && x.reactor.id === c.conv);
    if (there) { log(`${c.conv} ${c.ref}: answered ${emoji}, but it already reacted; left as it is`); return; }
    const done = r.toggle({ surface: 'agent-chat', conv: c.conv, ref: c.ref, reactor }, emoji);
    publish(done);
    log(`${c.conv} ${c.ref}: answered ${emoji} as text; stored as its reaction`);
  }

  function poll(agent: string, w: Watch): void {
    if (watches.get(agent) !== w) return;
    let items: readonly any[] = [];
    try { items = deps.chatItems!(agent); } catch { items = []; }
    const got = wakeAnswer(items, w.before, w.skip);
    if (got !== 'unseen') w.seen = true;
    if (typeof got === 'object') {
      endWatch(agent, w);
      if (silentReply(got.text) === 'emoji') {
        try { react(w.change, got.text.trim()); } catch (e) { log(`${agent} ${w.change.ref}: reaction not stored (${String(e)})`); }
      }
      return;
    }
    if (got === 'none' || w.waited + REPLY_POLL_MS > REPLY_WATCH_MS) { endWatch(agent, w); return; }
    w.waited += REPLY_POLL_MS;
    w.timer = setTimer(() => poll(agent, w), REPLY_POLL_MS);
  }

  /** The chat's wake turns right BEFORE an inject: Claude writes the wake to its log at submit, before the gateway's
   *  verify returns, so a snapshot taken after 'delivered' would already hold it. null: no watching. */
  function snapshot(agent: string): Set<string> | null {
    if (!deps.chatItems || !deps.reactions) return null;
    try { return wakeIds(deps.chatItems(agent)); } catch { return new Set(); }
  }

  /** A wake was delivered: watch for the agent's answer to it (superseding the watch of an earlier wake). `snap` is
   *  the chat's wake turns taken before the attempt that delivered it. */
  function watch(c: ReactionChange, snap: Set<string> | null): void {
    if (!snap) return;
    const agent = c.conv;
    const prev = watches.get(agent);
    endWatch(agent);
    let before: Set<string> = snap, skip = 0;
    if (prev && !prev.seen) { before = prev.before; skip = prev.skip + 1; }   // that wake is not in the log yet: ours is after it
    const w: Watch = { change: c, before, skip, seen: false, timer: null, waited: REPLY_POLL_MS };
    w.timer = setTimer(() => poll(agent, w), REPLY_POLL_MS);
    watches.set(agent, w);
  }

  const cancel = (key: string) => {
    const p = pending.get(key);
    if (p) { clearTimer(p.timer); pending.delete(key); }
  };

  async function fire(key: string, entry: Pending, attempt: number): Promise<void> {
    if (pending.get(key) !== entry) return;
    const c = entry.change;
    if (!deps.agentExists(c.conv)) { pending.delete(key); log(`${c.conv}: not a registered agent; dropped`); return; }
    let on = '';
    try { on = deps.messageText(c.conv, c.ref) ?? ''; } catch { on = ''; }
    const text = reactionWakeText({ user: c.reactor.id, emoji: String(c.emoji), on });
    const snap = snapshot(c.conv);                     // before THIS attempt (a retry takes its own)
    let r: GatewayInjectResult;
    try { r = await deps.inject(deps.resolveSession(c.conv), text); }
    catch (e) { r = { httpStatus: 0, ok: false, unreachable: true, error: String(e) }; }
    if (pending.get(key) !== entry) return;            // a newer reaction on this message took over meanwhile
    const outcome = wakeOutcome(r);
    if (outcome === 'retry' && attempt === 0) {
      entry.timer = setTimer(() => { void fire(key, entry, 1); }, WAKE_RETRY_MS);
      log(`${c.conv} ${c.ref}: not delivered (${why(r)}); one retry in ${WAKE_RETRY_MS / 1000} s`);
      return;
    }
    pending.delete(key);
    if (outcome === 'delivered') watch(c, snap);
    log(outcome === 'delivered'
      ? `${c.conv} ${c.ref}: ${c.emoji} delivered`
      : `${c.conv} ${c.ref}: ${c.emoji} not delivered (${why(r)}); dropped, the reaction stays stored`);
  }

  function onChange(c: ReactionChange): void {
    if (c.surface !== 'agent-chat' || c.reactor.kind !== 'user') return;
    const key = `${c.conv}\u0000${c.ref}`;
    cancel(key);
    if (!c.emoji) return;
    const entry: Pending = { change: c, timer: null };
    entry.timer = setTimer(() => { void fire(key, entry, 0); }, WAKE_DEBOUNCE_MS);
    pending.set(key, entry);
  }

  return {
    onChange,
    pendingCount: () => pending.size,
    watchCount: () => watches.size,
    stop: () => { for (const k of [...pending.keys()]) cancel(k); for (const a of [...watches.keys()]) endWatch(a); },
  };
}

/** Listen on the reaction bus (services/reaction-bus.ts); returns the stop. */
export function startReactionWake(deps: WakeDeps): () => void {
  const wake = createReactionWake(deps);
  const off = onReaction(wake.onChange);
  return () => { off(); wake.stop(); };
}
