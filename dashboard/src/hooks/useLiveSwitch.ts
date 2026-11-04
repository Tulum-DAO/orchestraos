/**
 * useLiveSwitch — a running agent's model / size / effort picks, sequenced (lib/liveSwitch.ts). Used by every LIVE
 * picker (the composer, the Arturo panel's Model page); Settings defaults are not live switches.
 *
 * ONE sequencer per agent, in a module-level store shared by every picker mounted on that agent: they share its queue,
 * what it shows, the model the gateway last accepted, and its notices, so a pick made in one picker can never be sent
 * with a model another picker's stale read still names. A sequencer lives while a picker is subscribed or a request is
 * out; with neither it is dropped.
 *
 * `deps.send` is lib/agentModel.ts switchAgentModel. `deps.resync` re-reads the agent's model into the page's own copy
 * (GET /api/agents/:id/model) and resolves with what it read. `deps.baseModel` is the page's last read: the model
 * waiting to be switched to, else the one it is on. The page shows `shown` over its copy. `deps.status` is the
 * agent's status as the page reads it (the agents poll with the live status stream over it): the sequencer's one
 * signal that the agent's turn ended (working / thinking / stalled -> anything else), when it re-reads a pick the
 * gateway kept for the turn end. A page without it still gets the fallback re-read.
 */
import { useCallback, useEffect, useState, useSyncExternalStore } from 'react';
import {
  idleSwitch, inTurn, liveSwitchStep, shownOf, type LiveSwitchEffect, type LiveSwitchEvent, type LiveSwitchState, type ReadInfo,
  type SwitchIntent, type SwitchValues,
} from '../lib/liveSwitch';
import type { SwitchBody, SwitchNotice } from '../lib/agentModel';

export interface LiveSwitchDeps {
  send: (agentId: string, model: string, effort?: string) => Promise<{ status: number; body: SwitchBody }>;
  resync: () => Promise<ReadInfo | null | void> | void;
  baseModel: string | null;
  /** A confirmed live model switch (200, switched). */
  onApplied?: (v: { model: string }) => void;
  /** The agent's status as this page reads it (its turn end); absent when the page has none. */
  status?: string | null;
  /** A timer (tests pass their own); returns its cancel. */
  later?: (fn: () => void, ms: number) => () => void;
}

export interface LiveSwitch {
  /** The newest intent over the server's values (empty: show the server's). */
  shown: SwitchValues;
  notice: SwitchNotice | null;
  /** A request is out or waiting. */
  busy: boolean;
  pick: (intent: SwitchIntent) => void;
  /** A notice from the page itself (a pick refused before asking), or null to clear it. */
  say: (notice: SwitchNotice | null) => void;
}

const defaultLater = (fn: () => void, ms: number) => { const t = setTimeout(fn, ms); return () => clearTimeout(t); };
const baseOf = (info: ReadInfo | null | void) => (info ? info.pending?.model ?? info.model ?? null : null);

/** One picker's hold on its agent's sequencer. */
class Subscriber {
  deps: LiveSwitchDeps;
  agentId: string | null = null;
  notify: () => void = () => {};
  constructor(deps: LiveSwitchDeps) { this.deps = deps; }
  update(agentId: string, deps: LiveSwitchDeps) {
    const moved = deps.baseModel !== this.deps.baseModel;
    this.deps = deps;
    // A fresh read in this page (its base changed): the sequencer's fallback from now on.
    const engine = this.agentId === agentId ? engines.get(agentId) : undefined;
    if (engine && moved && deps.baseModel) engine.readBase = deps.baseModel;
    if (engine && deps.status !== undefined) engine.seen(deps.status);
  }
}

class Engine {
  state: LiveSwitchState = idleSwitch();
  subs = new Set<Subscriber>();
  /** The server's last read, from any picker: what an effort goes with when nothing was accepted since. */
  readBase: string | null = null;
  private agentId: string;
  private lastDeps: LiveSwitchDeps | null = null;
  private cancelTimer: (() => void) | null = null;
  /** The watch on a pick kept for the turn end (its own timer: a pick cancels a re-read, never this). */
  private cancelWatch: (() => void) | null = null;
  /** When the armed watch comes due (Date.now ms): a later watch never pushes an earlier one back. */
  private watchDue: number | null = null;
  /** Whether the agent was mid-turn at the last status a page gave (null: none given yet). */
  private midTurn: boolean | null = null;
  constructor(agentId: string) { this.agentId = agentId; }

  private deps(): LiveSwitchDeps | null {
    const first = this.subs.values().next().value;
    if (first) this.lastDeps = first.deps;
    return this.lastDeps;
  }

  step(ev: LiveSwitchEvent) {
    const r = liveSwitchStep(this.state, ev);
    if (r.state !== this.state) {
      this.state = r.state;
      this.subs.forEach((s) => s.notify());
    }
    r.effects.forEach((e) => this.perform(e));
    this.dropIfIdle();
  }

  private stopTimer() { this.cancelTimer?.(); this.cancelTimer = null; }
  private stopWatch() { this.cancelWatch?.(); this.cancelWatch = null; this.watchDue = null; }

  /** A page's reading of the agent's status: mid-turn -> not is the turn end. */
  seen(status: string | null) {
    const mid = inTurn(status);
    const ended = this.midTurn === true && !mid;
    this.midTurn = mid;
    if (ended) this.step({ type: 'turnEnd' });
  }

  private perform(e: LiveSwitchEffect) {
    switch (e.kind) {
      case 'send': {
        const deps = this.deps();
        if (!deps) return;
        // Never cancelled: it may already be applied. Its answer is matched by seq.
        void deps.send(this.agentId, e.model, e.effort)
          .catch(() => ({ status: 0, body: {} as SwitchBody }))
          .then(({ status, body }) => this.step({ type: 'reply', seq: e.seq, status, body, baseModel: this.readBase }));
        return;
      }
      case 'resync': {
        if (!this.subs.size) return;          // no picker left to show it
        const go = () => {
          if (e.later) this.cancelTimer = null;
          // Every picker on this agent re-reads its own copy; the first answer is the sequencer's.
          const reads = [...this.subs].map((s) => Promise.resolve(s.deps.resync()).catch(() => null));
          void Promise.all(reads).then((infos) => {
            const info = infos.find((i): i is ReadInfo => !!i) ?? null;
            const base = baseOf(info);
            if (base) this.readBase = base;
            this.step({ type: 'read', token: e.token, info, later: e.later });
          });
        };
        if (e.delayMs > 0) {
          this.stopTimer();
          this.cancelTimer = (this.deps()?.later ?? defaultLater)(go, e.delayMs);
        } else go();
        return;
      }
      case 'cancelResync':
        this.stopTimer();
        return;
      case 'applied':
        this.subs.forEach((s) => s.deps.onApplied?.({ model: e.model }));
        return;
      case 'watch': {
        if (!this.subs.size) { this.stopWatch(); return; }   // only while a picker is mounted
        const due = Date.now() + e.delayMs;
        // The earlier deadline wins: a fallback watch never delays a turn end's read.
        if (this.cancelWatch && this.watchDue !== null && this.watchDue <= due) return;
        this.stopWatch();
        this.watchDue = due;
        this.cancelWatch = (this.deps()?.later ?? defaultLater)(() => {
          this.cancelWatch = null;
          this.watchDue = null;
          this.step({ type: 'poll' });
        }, e.delayMs);
        return;
      }
      case 'unwatch':
        this.stopWatch();
        return;
    }
  }

  attach(sub: Subscriber) {
    const first = !this.subs.size;
    this.subs.add(sub);
    if (sub.deps.baseModel) this.readBase = sub.deps.baseModel;
    if (sub.deps.status !== undefined && this.midTurn === null) this.midTurn = inTurn(sub.deps.status);
    if (first) this.step({ type: 'join' });
  }

  detach(sub: Subscriber) {
    this.subs.delete(sub);
    if (!this.subs.size) this.step({ type: 'leave' });
    this.dropIfIdle();
  }

  private dropIfIdle() {
    if (this.subs.size || this.state.inFlight) return;
    this.stopTimer();
    this.stopWatch();
    if (engines.get(this.agentId) === this) engines.delete(this.agentId);
  }
}

const engines = new Map<string, Engine>();
const IDLE = idleSwitch();

function attach(agentId: string, sub: Subscriber, notify: () => void): () => void {
  let engine = engines.get(agentId);
  if (!engine) { engine = new Engine(agentId); engines.set(agentId, engine); }
  sub.agentId = agentId;
  sub.notify = notify;
  engine.attach(sub);
  const held = engine;
  return () => {
    if (sub.agentId === agentId) sub.agentId = null;
    held.detach(sub);
  };
}

const snapshot = (agentId: string): LiveSwitchState => engines.get(agentId)?.state ?? IDLE;

export function useLiveSwitch(agentId: string, deps: LiveSwitchDeps): LiveSwitch {
  const [sub] = useState(() => new Subscriber(deps));
  // The latest callbacks and read, for answers that arrive later.
  useEffect(() => { sub.update(agentId, deps); });
  const subscribe = useCallback((notify: () => void) => attach(agentId, sub, notify), [agentId, sub]);
  const get = useCallback(() => snapshot(agentId), [agentId]);
  const state = useSyncExternalStore(subscribe, get, get);
  const pick = useCallback((intent: SwitchIntent) => {
    engines.get(agentId)?.step({ type: 'pick', intent, baseModel: engines.get(agentId)!.readBase });
  }, [agentId]);
  const say = useCallback((notice: SwitchNotice | null) => engines.get(agentId)?.step({ type: 'say', notice }), [agentId]);
  return { shown: shownOf(state), notice: state.notice, busy: !!(state.inFlight || state.waiting), pick, say };
}
