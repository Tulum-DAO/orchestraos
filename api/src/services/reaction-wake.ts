// The reaction wake (spec "Agent wake"). The operator's reaction in an agent's chat reaches that agent as one line
// (lib/reaction-wake-text.ts) so it can reply, react back (`orchestra react`) or do nothing:
//   - only a USER add or change on surface agent-chat; a removal is never delivered (and cancels a wake not sent yet);
//     an agent's own reaction never wakes anyone;
//   - debounced 3 s per (agent, message): picking 👍 then ❤️ sends one wake, with ❤️;
//   - injected through the gateway's /agent-message with NO hold and NO force (agent-send.ts postAgentMessage,
//     hold:false). Not handleAgentSend: its fallback stores the wake as mail from the operator and its composer-hold
//     409 collides with the operator typing;
//   - refused while nothing was typed (the pane holds composer text, a menu, the CLI is starting, the gateway is
//     down): retried ONCE after 30 s, then dropped. The reaction stays stored and shown; the agent never hears of it.
//     A refusal where the text may have been typed (unverified submit, timeout) is never sent again.
// Express-free and dependency-injected (timers too) so it is unit-tested; server.ts wires the real deps.
import type { ReactionChange } from '../lib/reactions-store.js';
import type { GatewayInjectResult } from '../routes/agent-send.js';
import { reactionWakeText } from '../lib/reaction-wake-text.js';
import { onReaction } from './reaction-bus.js';

export const WAKE_DEBOUNCE_MS = 3000;
export const WAKE_RETRY_MS = 30_000;

export interface WakeDeps {
  agentExists(agentId: string): boolean;
  /** agent id -> tmux session (registry tmux_session, else the id). */
  resolveSession(agentId: string): string;
  /** The gateway's /agent-message with no hold and no force. */
  inject(session: string, text: string): Promise<GatewayInjectResult>;
  /** The reacted message's text, or null when the chat does not have it. */
  messageText(agentId: string, ref: string): string | null;
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

const why = (r: GatewayInjectResult) => String(r.reason || r.state || r.error || r.httpStatus);

export function createReactionWake(deps: WakeDeps) {
  const setTimer = deps.setTimer ?? ((fn: () => void, ms: number) => { const t = setTimeout(fn, ms); t.unref?.(); return t; });
  const clearTimer = deps.clearTimer ?? ((h: unknown) => clearTimeout(h as NodeJS.Timeout));
  const log = deps.log ?? ((line: string) => console.log(`[reaction-wake] ${line}`));
  const pending = new Map<string, Pending>();

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
    stop: () => { for (const k of [...pending.keys()]) cancel(k); },
  };
}

/** Listen on the reaction bus (services/reaction-bus.ts); returns the stop. */
export function startReactionWake(deps: WakeDeps): () => void {
  const wake = createReactionWake(deps);
  const off = onReaction(wake.onChange);
  return () => { off(); wake.stop(); };
}
