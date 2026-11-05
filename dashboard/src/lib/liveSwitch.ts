/**
 * liveSwitch.ts — the sequencer behind every LIVE model / size / effort pick on a running agent (the composer's picker,
 * the Arturo panel's Model page). Settings defaults are not live switches and do not use it. One sequencer per agent,
 * shared by every picker mounted on it (hooks/useLiveSwitch.ts keeps them in a store).
 *
 * Without it each pick was its own PUT, so quick picks could reach the gateway out of order (the agent ending on an
 * OLDER pick), their answers could come back out of order (the picker showing an older pick), and a late failure could
 * put back a guessed previous value over a newer one.
 *
 *   idle ──pick──▶ in flight ──answer, nothing waiting──▶ drained: say the outcome, re-read when owed ──▶ idle
 *                   │   ▲
 *              pick │   │ answer: the next waiting part goes out (the answer is superseded: it is remembered, not said)
 *                   ▼   │
 *              in flight + ONE waiting slot {model?, effort?} (a newer pick replaces its part; latest wins)
 *
 * - At most one request in flight per agent. Picks made meanwhile wait in a single slot: a model pick replaces the
 *   waiting model, an effort pick the waiting effort; the ones in between are never sent.
 * - A model change and an effort never share a PUT: the gateway merges a pick with its own waiting switch (a busy
 *   agent's 202), so a combined PUT could revive a model the operator had just cancelled. The model goes alone; the
 *   effort follows, sent with the model the gateway last accepted (switched, or waiting for the turn to end), resolved
 *   when it is sent, else the server's last read (the model waiting to be switched to, else the one it is on).
 * - Shown at once: what the gateway took (settled), then the request in flight, then the waiting slot. A part that
 *   failed is simply not settled, so the picker falls back to what is true.
 * - Every send has a sequence number; only the answer to the request in flight is read. While busy the notice names
 *   what is in flight. When the chain drains the newest answer speaks; a failure earlier in the chain is said instead
 *   when the newest succeeded; a model and an effort that both went out are said together.
 * - Re-reads (GET /api/agents/:id/model): any failure, now; any chain with a model, now, or 8 s after a confirmed live
 *   switch (the seat's record lags). A pick cancels a re-read still to come; it is owed and scheduled again at the next
 *   drain. When a read lands the settled values give way to the server's, except a pick the server still has waiting
 *   (its pending model / effort) and a switched model whose record is still owed its later read.
 * - The last picker leaving drops the waiting pick; the request in flight is not cancelled (it may already be applied)
 *   and still answers into the same sequencer.
 * - A pick kept for the turn to end (202): it stays shown while the server lists it as pending, and is AWAITED. The
 *   agent's turn ending (busy -> idle on the page's status) re-reads TURN_END_READ_MS later, once the gateway's 5 s pass
 *   has applied or closed it; a fallback re-reads every TURN_POLL_MS (at most TURN_POLL_MAX times) in case that
 *   transition is missed. Both only while a picker is mounted. When the read finds nothing waiting: the gateway's
 *   `last_switch` for it (else the value the agent is on) says applied ("Switched to X.") or failed ("Couldn't apply X
 *   when the turn ended: <its reason>."), said once; a failed one leaves what shows at once, so picking it again is a
 *   new pick. A pick replaced elsewhere, or a read that failed, says nothing. Any 200 ends the wait (the gateway drops
 *   a waiting switch on every 200: cancelled, or replaced by a switch now).
 *
 * The reducer is pure: it returns effects (send, re-read, cancel a re-read, applied, watch / unwatch a kept pick) for
 * hooks/useLiveSwitch.ts.
 */
import { effortNotice, switchNotice, type SwitchBody, type SwitchNotice } from './agentModel';

/** A picked value and the words for it ("Opus 5.5", "High"). */
export interface Choice { id: string; label: string }
/** What a pick asks for: a model (a model row or a size), an effort level, or both while they wait. */
export interface SwitchIntent { model?: Choice; effort?: Choice }
export interface SwitchValues { model?: string; effort?: string }
/** The gateway's record of the session's last switch kept for a turn end (scripts/agent_model_pending.py `last`). */
export interface LastSwitch { model?: string; effort?: string; at?: string; outcome?: string; reason?: string | null }
/** What a re-read said: the model (and effort) it is on, what waits for the turn to end, and how the last such wait
 *  closed. */
export interface ReadInfo {
  model?: string | null;
  effort?: string | null;
  pending?: { model?: string; effort?: string; since?: string } | null;
  last_switch?: LastSwitch | null;
  /** The gateway could not read its record of waiting switches (then `pending` and `last_switch` say nothing). */
  pending_error?: string;
}

export interface SentSwitch {
  seq: number;
  /** One part only: a model, or an effort. */
  intent: SwitchIntent;
  /** The PUT's model: the one picked, or for an effort the model resolved when it was sent. */
  model: string;
  effort?: string;
}

/** Picks the gateway kept for the turn to end (a 202), read again when it ends. `since`: the gateway's time for that
 *  wait (its `pending_since`), so an older `last_switch` is never taken for it. `polls`: fallback reads left. */
export interface Awaiting { model?: Choice; effort?: Choice; since: string | null; polls: number }

interface Chain {
  /** The latest failure among superseded answers, and its kind: forgotten once a later send of that kind succeeds. */
  failure: { kind: 'model' | 'effort'; notice: SwitchNotice } | null;
  /** The latest model answer taken while superseded (said with a following effort's). */
  model: { status: number; body: SwitchBody; label: string } | null;
}

export interface LiveSwitchState {
  /** The last sequence number given out. */
  seq: number;
  inFlight: SentSwitch | null;
  waiting: SwitchIntent | null;
  /** Values the gateway took, shown until a read gives way to the server's. */
  settled: SwitchValues;
  /** The last model the gateway accepted (switched, or waiting for the turn to end), until a read. */
  accepted: string | null;
  notice: SwitchNotice | null;
  chain: Chain;
  /** Re-reads owed: now, and/or after the record of a confirmed live switch caught up, and/or (`turn`) a turn end seen
   *  while a request was out: its read once the gateway has had its pass after the drain. */
  owed: { now: boolean; later: boolean; turn: boolean };
  /** Picks kept for the turn to end, until a read says how that went. */
  awaiting: Awaiting | null;
}

export type LiveSwitchEvent =
  /** `baseModel`: the model the server's last read says an effort goes with, used only when nothing was accepted. */
  | { type: 'pick'; intent: SwitchIntent; baseModel: string | null }
  | { type: 'reply'; seq: number; status: number; body: SwitchBody; baseModel: string | null }
  /** A re-read asked at drain `token` has landed (`later`: the one after a switched record caught up). */
  | { type: 'read'; token: number; info: ReadInfo | null; later: boolean }
  /** The last picker on this agent left. */
  | { type: 'leave' }
  /** A notice from a page itself (a pick refused before asking), or null to clear one. */
  | { type: 'say'; notice: SwitchNotice | null }
  /** The agent's turn ended (busy -> idle on a page's status). */
  | { type: 'turnEnd' }
  /** The watch on a kept pick came due. */
  | { type: 'poll' }
  /** A picker mounted on a sequencer that had none (one kept alive by a request still out). */
  | { type: 'join' };

export type LiveSwitchEffect =
  | { kind: 'send'; seq: number; model: string; effort?: string }
  /** Re-read the agent's model, after `delayMs`; then dispatch {type:'read', token, later}. */
  | { kind: 'resync'; delayMs: number; token: number; later: boolean }
  | { kind: 'cancelResync' }
  /** A confirmed live model switch (200 switched): for a page that keeps its own copy (the panel's Model row). */
  | { kind: 'applied'; model: string }
  /** (Re)start the one watch on a kept pick: after `delayMs`, dispatch {type:'poll'}. */
  | { kind: 'watch'; delayMs: number }
  | { kind: 'unwatch' };

/** A confirmed live switch shows at once; the seat's record catches up a few seconds later and a read made before then
 *  still names the old model, so that re-read waits. */
export const RESYNC_AFTER_SWITCH_MS = 8000;
/** After the turn ends: the gateway applies a kept pick on its next pass (every 5 s, scripts/watch_gateway.py
 *  _pending_model_switch_loop), so the read waits for one. */
export const TURN_END_READ_MS = 6000;
/** The fallback while a pick is kept, in case the turn end is not seen: every 20 s, for 15 min (the gateway's own
 *  stale mark, agent_model_pending.STALE_AFTER_S); a turn end still reads after that. */
export const TURN_POLL_MS = 20_000;
export const TURN_POLL_MAX = 45;
/** The states a turn is running in: the ones the gateway keeps a switch for (MID_TURN_STATES | STALLED_STATES). */
const TURN_STATES = new Set(['working', 'thinking', 'stalled']);
export const inTurn = (status: string | null | undefined) => TURN_STATES.has((status ?? '').trim().toLowerCase());

const NO_CHAIN: Chain = { failure: null, model: null };
const NOT_OWED = { now: false, later: false, turn: false };

export function idleSwitch(): LiveSwitchState {
  return { seq: 0, inFlight: null, waiting: null, settled: {}, accepted: null, notice: null, chain: NO_CHAIN, owed: NOT_OWED,
           awaiting: null };
}

const valuesOf = (i: SwitchIntent | null | undefined): SwitchValues =>
  ({ ...(i?.model ? { model: i.model.id } : {}), ...(i?.effort ? { effort: i.effort.id } : {}) });

/** What the picker shows over the server's values: settled, then in flight, then waiting. */
export function shownOf(s: LiveSwitchState): SwitchValues {
  return { ...s.settled, ...valuesOf(s.inFlight?.intent), ...valuesOf(s.waiting) };
}

const pendingNotice = (sent: SentSwitch): SwitchNotice => sent.intent.model
  ? { tone: 'wait', text: `Switching to ${sent.intent.model.label}…` }
  : { tone: 'wait', text: `Setting ${sent.intent.effort?.label ?? ''} effort…` };

function outcomeNotice(sent: SentSwitch, status: number, body: SwitchBody): SwitchNotice {
  return sent.intent.model ? switchNotice(status, body, sent.intent.model.label)
    : effortNotice(status, body, sent.intent.effort?.label ?? '');
}

const taken = (status: number, body: SwitchBody) => (status === 200 || status === 202) && !!body?.ok;
/** No answer said whether it applied: the request timed out, or the gateway / the dashboard proxy could not be heard
 *  from. The switch may still go through. */
export const unknownOutcome = (status: number, body: SwitchBody) =>
  (status === 0 && body?.error === 'timeout') || body?.error === 'gateway unreachable' || body?.error === 'upstream unreachable';
const TONE_RANK = { ok: 0, wait: 1, error: 2 } as const;

/** A model answer and the effort answer after it, said together. */
function bothNotice(m: NonNullable<Chain['model']>, sent: SentSwitch, status: number, body: SwitchBody): SwitchNotice {
  const effort = sent.intent.effort?.label ?? '';
  if (m.status === 202 && status === 202) {
    return { tone: 'wait', text: `${m.label} and ${effort} effort will apply when the agent finishes its current turn.` };
  }
  const a = switchNotice(m.status, m.body, m.label);
  const b = effortNotice(status, body, effort);
  return { tone: TONE_RANK[a.tone] >= TONE_RANK[b.tone] ? a.tone : b.tone, text: `${a.text} ${b.text}` };
}

/** The next request from what is waiting: the model alone when there is one (the effort keeps waiting), else the
 *  effort with the model resolved now. Null when nothing can be sent. */
function nextSend(s: LiveSwitchState, waiting: SwitchIntent, baseModel: string | null):
  { sent: SentSwitch; rest: SwitchIntent | null } | null {
  const seq = s.seq + 1;
  if (waiting.model) {
    return { sent: { seq, intent: { model: waiting.model }, model: waiting.model.id },
             rest: waiting.effort ? { effort: waiting.effort } : null };
  }
  const model = s.accepted ?? baseModel;
  if (!waiting.effort || !model) return null;
  return { sent: { seq, intent: { effort: waiting.effort }, model, effort: waiting.effort.id }, rest: null };
}

function sendEffect(sent: SentSwitch): LiveSwitchEffect {
  return { kind: 'send', seq: sent.seq, model: sent.model, ...(sent.effort ? { effort: sent.effort } : {}) };
}

/** Drop waiting parts the request in flight already asks for. */
function trimWaiting(waiting: SwitchIntent, inFlight: SentSwitch): SwitchIntent | null {
  const p = inFlight.intent;
  const w: SwitchIntent = {
    ...(waiting.model && waiting.model.id !== p.model?.id ? { model: waiting.model } : {}),
    ...(waiting.effort && waiting.effort.id !== p.effort?.id ? { effort: waiting.effort } : {}),
  };
  return w.model || w.effort ? w : null;
}

function drainEffects(s: LiveSwitchState): LiveSwitchEffect[] {
  const out: LiveSwitchEffect[] = [];
  if (s.owed.now) out.push({ kind: 'resync', delayMs: 0, token: s.seq, later: false });
  if (s.owed.later) out.push({ kind: 'resync', delayMs: RESYNC_AFTER_SWITCH_MS, token: s.seq, later: true });
  if (s.awaiting && s.owed.turn) out.push({ kind: 'watch', delayMs: TURN_END_READ_MS });
  else if (s.awaiting && s.awaiting.polls > 0) out.push({ kind: 'watch', delayMs: TURN_POLL_MS });
  return out;
}

/** What a reply leaves awaited: a 202 keeps its part waiting (the gateway keeps the other half of one already
 *  waiting), any 200 means nothing waits any more; a failure, or no answer, changes nothing at the gateway's record. */
function awaitAfter(a: Awaiting | null, sent: SentSwitch, status: number, body: SwitchBody): Awaiting | null {
  if (!taken(status, body)) return a;
  if (status !== 202) return null;
  const part = sent.intent.model ? { model: sent.intent.model } : { effort: sent.intent.effort };
  return { ...(a ?? {}), ...part, since: body.pending_since ?? a?.since ?? null, polls: TURN_POLL_MAX };
}

type Parts = { model?: boolean; effort?: boolean };

const unwatchIf = (before: Awaiting | null, after: Awaiting | null): LiveSwitchEffect[] =>
  before && !after ? [{ kind: 'unwatch' }] : [];

const stripStop = (t: string) => t.trim().replace(/\.+$/, '');

/** A read with an awaited pick in it: what is still kept, what is no longer shown (`drop`: ruled failed, or gone with
 *  no word of why), and what is said. */
function turnVerdict(s: LiveSwitchState, info: ReadInfo | null): {
  awaiting: Awaiting | null; keep: Parts; drop: Parts; notice: SwitchNotice | null; lag: boolean;
  /** Every awaited part gone with no word of why: a "will apply when the turn ends" notice no longer holds. */
  gone?: boolean;
} {
  const a = s.awaiting;
  const none = { awaiting: null, keep: {}, drop: {}, notice: null, lag: false };
  if (!a) return none;
  // Only a part still shown as picked: one the operator moved on from is not awaited any more.
  const parts = (['model', 'effort'] as const).filter((k) => a[k] && s.settled[k] === a[k]!.id);
  if (!parts.length) return none;
  const all = (ks: readonly ('model' | 'effort')[]): Parts => Object.fromEntries(ks.map((k) => [k, true]));
  // A read that failed, or a record the gateway could not read, says nothing: keep waiting.
  if (!info || info.pending_error) return { awaiting: a, keep: all(parts), drop: {}, notice: null, lag: false };
  const p = info.pending;
  if (p) {
    // Still waiting for ours: kept. A part it no longer carries was replaced from elsewhere: dropped, nothing said.
    const still = parts.filter((k) => p[k] === a[k]!.id);
    if (!still.length) return none;
    const next: Awaiting = { since: p.since ?? a.since, polls: a.polls };
    for (const k of still) next[k] = a[k];
    return { awaiting: next, keep: all(still), drop: {}, notice: null, lag: false };
  }
  // Nothing waits: the turn ended and the gateway applied or closed it, or it was cancelled or replaced elsewhere (a
  // 200 there records nothing). The gateway's record says which only when it is this pick's: closed at or after the
  // wait began, both times readable (fail closed).
  const last = info.last_switch;
  const ts = a.since ? Date.parse(a.since) : NaN;
  const ta = last?.at ? Date.parse(last.at) : NaN;
  const ours = !!last && Number.isFinite(ts) && Number.isFinite(ta) && ta >= ts;
  const verdict = (k: 'model' | 'effort') => {
    const id = a[k]!.id;
    const byLast = ours && last![k] === id && (last!.outcome === 'switched' || last!.outcome === 'failed');
    // Without its record: on it now is applied; anything else is gone with no word of why (said: nothing).
    const outcome: 'applied' | 'failed' | 'gone' = byLast ? (last!.outcome === 'switched' ? 'applied' : 'failed')
      : info[k] === id ? 'applied' : 'gone';
    return { k, outcome, reason: outcome === 'failed' && last!.reason ? stripStop(last!.reason) : null,
             lag: outcome === 'applied' && info[k] !== id };
  };
  const vs = parts.map(verdict);
  const keep: Parts = {};
  const drop: Parts = {};
  for (const v of vs) {
    if (v.lag) keep[v.k] = true;   // the gateway switched it; the seat's record has not caught up
    if (v.outcome !== 'applied') drop[v.k] = true;
  }
  const label = (k: 'model' | 'effort') => (k === 'model' ? a.model!.label : `${a.effort!.label} effort`);
  const texts: string[] = [];
  for (const v of vs) {
    if (v.outcome === 'applied') texts.push(v.k === 'model' ? `Switched to ${a.model!.label}.` : `Effort set to ${a.effort!.label}.`);
  }
  const bad = vs.filter((v) => v.outcome === 'failed');
  if (bad.length) {
    const reason = bad.find((v) => v.reason)?.reason;
    texts.push(`Couldn't apply ${bad.map((v) => label(v.k)).join(' and ')} when the turn ended${reason ? `: ${reason}` : ''}.`);
  }
  const notice: SwitchNotice | null = texts.length ? { tone: bad.length ? 'error' : 'ok', text: texts.join(' ') } : null;
  return { awaiting: null, keep, drop, notice, lag: vs.some((v) => v.lag), gone: vs.every((v) => v.outcome === 'gone') };
}

export function liveSwitchStep(s: LiveSwitchState, ev: LiveSwitchEvent): { state: LiveSwitchState; effects: LiveSwitchEffect[] } {
  switch (ev.type) {
    case 'say':
      return { state: { ...s, notice: ev.notice }, effects: [] };

    case 'leave': {
      // Nobody to show it to: the watch stops (a picker coming back re-arms it: 'join').
      const effects: LiveSwitchEffect[] = s.awaiting ? [{ kind: 'unwatch' }] : [];
      return s.waiting ? { state: { ...s, waiting: null }, effects } : { state: s, effects };
    }

    case 'join':
      return { state: s, effects: s.awaiting && s.awaiting.polls > 0 ? [{ kind: 'watch', delayMs: TURN_POLL_MS }] : [] };

    case 'turnEnd':
      if (!s.awaiting) return { state: s, effects: [] };
      // A request out: its drain reads (owed); else once the gateway has had its pass.
      if (s.inFlight || s.waiting) return { state: { ...s, owed: { ...s.owed, turn: true } }, effects: [] };
      return { state: s, effects: [{ kind: 'watch', delayMs: TURN_END_READ_MS }] };

    case 'poll': {
      if (!s.awaiting || s.inFlight || s.waiting) return { state: s, effects: [] };   // a drain re-arms it
      return {
        state: { ...s, awaiting: { ...s.awaiting, polls: Math.max(0, s.awaiting.polls - 1) } },
        effects: [{ kind: 'resync', delayMs: 0, token: s.seq, later: false }],
      };
    }

    case 'pick': {
      const { model, effort } = ev.intent;
      if (!model && !effort) return { state: s, effects: [] };
      if (s.inFlight) {
        const shown = shownOf(s);
        // The same as the newest intent: nothing new. (From idle it is sent: picking the model it is on is how a switch
        // waiting for the turn to end is cancelled.)
        if ((!model || shown.model === model.id) && (!effort || shown.effort === effort.id)) return { state: s, effects: [] };
        const merged = { ...(s.waiting ?? {}), ...(model ? { model } : {}), ...(effort ? { effort } : {}) };
        return { state: { ...s, waiting: trimWaiting(merged, s.inFlight), notice: pendingNotice(s.inFlight) },
                 effects: [{ kind: 'cancelResync' }] };
      }
      const next = nextSend(s, ev.intent, ev.baseModel);
      if (!next) return { state: s, effects: [] };
      return {
        state: { ...s, seq: next.sent.seq, inFlight: next.sent, waiting: next.rest, notice: pendingNotice(next.sent), chain: NO_CHAIN },
        effects: [{ kind: 'cancelResync' }, sendEffect(next.sent)],
      };
    }

    case 'reply': {
      const sent = s.inFlight;
      if (!sent || ev.seq !== sent.seq) return { state: s, effects: [] };
      const ok = taken(ev.status, ev.body);
      const isModel = !!sent.intent.model;
      const kind = isModel ? 'model' : 'effort';
      // A later success of the same kind supersedes an earlier failure of it.
      const failure = ok && s.chain.failure?.kind === kind ? null : s.chain.failure;
      const switched = ok && ev.status === 200 && ev.body.switching === 'switched';
      const awaiting = awaitAfter(s.awaiting, sent, ev.status, ev.body);
      const after: LiveSwitchState = {
        ...s,
        inFlight: null,
        awaiting,
        // Real server state even when superseded.
        accepted: isModel && (ok || ev.body?.model_switched) ? sent.model : s.accepted,
        settled: ok ? { ...s.settled, ...valuesOf(sent.intent) } : s.settled,
        owed: {
          now: s.owed.now || !ok || (isModel && !switched),
          // A model switch whose outcome is unknown may still land at the gateway: the later read picks it up.
          later: s.owed.later || (isModel && (switched || unknownOutcome(ev.status, ev.body))),
          turn: s.owed.turn,
        },
      };
      const effects: LiveSwitchEffect[] = [
        ...(isModel && switched ? [{ kind: 'applied', model: sent.model } as const] : []), ...unwatchIf(s.awaiting, awaiting)];
      const next = s.waiting ? nextSend(after, s.waiting, ev.baseModel) : null;
      if (next) {
        const chain: Chain = {
          failure: ok ? failure : { kind, notice: outcomeNotice(sent, ev.status, ev.body) },
          model: isModel && ok ? { status: ev.status, body: ev.body, label: sent.intent.model!.label } : s.chain.model,
        };
        return {
          state: { ...after, seq: next.sent.seq, inFlight: next.sent, waiting: next.rest, chain, notice: pendingNotice(next.sent) },
          effects: [...effects, sendEffect(next.sent)],
        };
      }
      // Drained: the newest answer speaks, unless it succeeded after an earlier failure in the chain.
      let notice: SwitchNotice;
      if (!ok) notice = outcomeNotice(sent, ev.status, ev.body);
      else if (failure) notice = failure.notice;
      else if (!isModel && s.chain.model) notice = bothNotice(s.chain.model, sent, ev.status, ev.body);
      else notice = outcomeNotice(sent, ev.status, ev.body);
      const drained = { ...after, waiting: null, notice, chain: NO_CHAIN };
      // The turn end's read is now scheduled (drainEffects): no longer owed.
      return { state: { ...drained, owed: { ...drained.owed, turn: false } }, effects: [...effects, ...drainEffects(drained)] };
    }

    case 'read': {
      if (ev.token !== s.seq || s.inFlight || s.waiting) return { state: s, effects: [] };
      const v = turnVerdict(s, ev.info);
      // A kept pick the gateway switched while the seat's record still names the old one: read again once it caught up.
      const lagRead = v.lag && !ev.later;
      const owed = ev.later ? NOT_OWED : { now: false, later: s.owed.later || lagRead, turn: false };
      const p = ev.info?.pending;
      // A switched model's record may lag until its later read: kept until then. A pick the server still has waiting:
      // kept while it waits; an awaited one while a read cannot say (turnVerdict).
      // A part just ruled failed (or gone) is never kept, not even by a later read still owed.
      const keepModel = s.settled.model !== undefined && !v.drop.model
        && (owed.later || p?.model === s.settled.model || !!v.keep.model);
      const keepEffort = s.settled.effort !== undefined && !v.drop.effort && (p?.effort === s.settled.effort || !!v.keep.effort);
      // An error not yet cleared (a chain's failure) is said with it, never replaced.
      const notice = !v.notice ? (v.gone && s.notice?.tone === 'wait' ? null : s.notice) : s.notice?.tone === 'error'
        ? { tone: 'error' as const, text: `${s.notice.text} ${v.notice.text}` } : v.notice;
      const settled: SwitchValues = {
        ...(keepModel ? { model: s.settled.model } : {}),
        ...(keepEffort ? { effort: s.settled.effort } : {}),
      };
      const effects: LiveSwitchEffect[] = [
        ...(lagRead ? [{ kind: 'resync', delayMs: RESYNC_AFTER_SWITCH_MS, token: s.seq, later: true } as const] : []),
        ...(v.awaiting && v.awaiting.polls > 0 ? [{ kind: 'watch', delayMs: TURN_POLL_MS } as const] : []),
        ...unwatchIf(s.awaiting, v.awaiting),
      ];
      return {
        state: { ...s, settled, accepted: owed.later ? s.accepted : null, owed, awaiting: v.awaiting, notice },
        effects,
      };
    }
  }
}
