/**
 * TranscriptTailer — the F1 streaming seam (SPEC_transcript-ecosystem §4).
 *
 * Tails an agent's append-only session JSONL and pushes newly-normalized
 * transcript items in the F0 v2 contract shape (grammar_version, canonical
 * summary, capped input — via normalizeTranscript; NEVER a parallel grammar).
 *
 * Model:
 *  - snapshot: full backfill (last `limit` items + server-paired render_items)
 *  - delta:    only the items appended since the subscriber's offset
 *  - resume:   SSE Last-Event-ID = `<session_id>:<offset>` where offset is the
 *              item count in the FULL normalized transcript. Valid id -> delta
 *              of the missed tail; stale/foreign id -> fresh snapshot.
 *  - reset:    session rotation or file truncation -> snapshot { reset: true }.
 *
 * A headless seat's cut turns (the supervisor's interrupted.json) get the same "[TURN INTERRUPTED]"
 * line as the poll route, after the turn its message opened, once. The file is watched beside the
 * log (it is written when the process dies, usually with nothing appended to the log). A line that
 * lands at the end goes out as a delta; one that lands before the tail goes out as a snapshot,
 * because a delta only appends and every later item's offset moved.
 *
 * A tailer whose seat has no transcript AND no tmux window for GONE_TICKS beats in a row (an agent with no pane,
 * a retired seat whose page is still open) stops: tick() says 'gone' once and every later tick is a no-op, so an
 * open page no longer asks tmux about a window that is not coming back. A new subscriber starts it again.
 *
 * The live-STATE lane (working/idle/waiting/stranded v2 detector) is NOT here
 * by design — state stays its own lane per the locked congruence guardrail.
 *
 * Testable with a manual tick() + injectable resolver (no timers inside);
 * the route (transcript-stream.ts) owns the interval + per-agent registry.
 */
import { readFileSync, statSync } from 'fs';
import {
  normalizeTranscript, buildRenderItems, dropWakeReplies, GRAMMAR_VERSION,
  mergeInterruptedTurns, readInterruptedTurns, interruptedTurnsSig, type InterruptedTurn,
} from '../routes/chat-transcript.js';
import { storeRules } from './conversation-store.js';
import { stampRefs } from '../lib/messageRef.js';

/** Upper bound on items the SSE tailer will normalize per tick (see the call site). */
const TAIL_ITEM_CAP = 2000;
/** Beats in a row with no transcript and no tmux window before a tailer stops (about 30 s at the route's 1 s beat). */
export const GONE_TICKS = 30;

export interface TailEvent {
  type: 'snapshot' | 'delta';
  /** SSE event id: `<session_id>:<full-transcript item offset after this event>` */
  id: string;
  agent_id: string;
  session_id: string | null;
  grammar_version: number;
  /** snapshot: last `limit` items; delta: only the new items. F0 item shapes. */
  items: any[];
  /** snapshot only: server-paired render nodes for the items window. */
  render_items?: any[];
  /** snapshot only: true when pushed because the session rotated/truncated. */
  reset?: boolean;
}

type Resolver = () => { path: string | null; sid: string | null };
type Interrupted = { read: () => InterruptedTurn[]; sig: () => string };
type Subscriber = (ev: TailEvent) => void;

/** Item identity for aligning two windows of the same log (usage excluded: that is what changed). */
const itemKey = (it: any): string =>
  JSON.stringify([it?.kind, it?.role, it?.uuid, it?.id, it?.tool_use_id, String(it?.text ?? '').slice(0, 200)]);

/** How far `next` slid relative to `prev` (index in next = index in prev + shift), found by
 *  locating prev's last item in next; null when it cannot be found. 0 below the cap. */
export function alignShift(prev: any[], next: any[]): number | null {
  if (!prev.length) return 0;
  const last = prev.length - 1;
  const key = itemKey(prev[last]);
  if (next.length > last && itemKey(next[last]) === key) return 0;
  for (let j = next.length - 1; j >= 0; j--) if (itemKey(next[j]) === key) return j - last;
  return null;
}

export class TranscriptTailer {
  private agentId: string;
  private resolve: Resolver;
  private limit: number;
  private subs = new Set<Subscriber>();

  private sid: string | null = null;
  private path: string | null = null;
  private fileSize = -1;
  private items: any[] = [];
  private rev = 0;           // bumped when an already-sent item changes (see sidTag)
  private interrupted: Interrupted;
  private interruptedSig = '';
  private rulesSig = '';
  private windowGone: () => boolean;
  private goneBeats = 0;
  private stopped = false;

  constructor(opts: { agentId: string; resolve: Resolver; limit?: number; interrupted?: Interrupted;
    windowGone?: () => boolean }) {
    this.agentId = opts.agentId;
    this.resolve = opts.resolve;
    this.limit = opts.limit ?? 150;
    this.windowGone = opts.windowGone ?? (() => false);
    this.interrupted = opts.interrupted ?? {
      read: () => readInterruptedTurns(opts.agentId),
      sig: () => interruptedTurnsSig(opts.agentId),
    };
  }

  get subscriberCount(): number {
    return this.subs.size;
  }

  /** The newest items read so far (the live step: the tool call still running). Read only. */
  recentItems(n = 50): readonly any[] {
    return this.items.slice(-n);
  }

  /** Whether the items read so far hold this uuid (live text: its finished line has landed). */
  hasItemUuid(uuid: string): boolean {
    for (let i = this.items.length - 1; i >= 0; i--) if (this.items[i]?.uuid === uuid) return true;
    return false;
  }

  /** Re-resolve + re-read the transcript. Returns what changed. */
  private refresh(): { rotated: boolean; grewFrom: number | null; restated?: boolean } {
    const { path, sid } = this.resolve();
    const prevSid = this.sid;
    const prevCount = this.items.length;
    const rotated = this.sid !== null && (sid !== prevSid || path !== this.path);

    if (!path) {
      this.sid = sid;
      this.path = null;
      this.fileSize = -1;
      this.items = [];
      return { rotated, grewFrom: null };
    }

    // Cheap no-op check: same file, same size -> nothing to re-normalize.
    let size = -1;
    try { size = statSync(path).size; } catch { size = -1; }
    let isig = '';
    try { isig = this.interrupted.sig(); } catch { isig = ''; }
    // The store's rules for this session (seen-only entries, seed rows): marking an entry seen-only changes what is
    // shown without the log growing, so it re-reads and re-sends the window.
    let rsig = '';
    try { rsig = storeRules(sid).sig; } catch { rsig = ''; }
    if (!rotated && path === this.path && size === this.fileSize && isig === this.interruptedSig && rsig === this.rulesSig) {
      return { rotated: false, grewFrom: null };
    }

    let lines: string[] = [];
    try { lines = readFileSync(path, 'utf-8').split('\n'); } catch { lines = []; }
    // BOUNDED. This runs on a ~1 s poll, and codex rollouts reach 13-25 MB: normalizing an
    // unbounded item list every tick is a self-inflicted load collapse (congruence
    // DEC-1790239929422621, both peers). The chat view never renders more than a few hundred
    // items anyway, and the cap is well above the poll route's own 500 ceiling.
    const env = normalizeTranscript(
      lines, this.agentId, sid, TAIL_ITEM_CAP, path.includes('antigravity-cli'), false, null, true);
    let turns: InterruptedTurn[] = [];
    try { turns = this.interrupted.read(); } catch { turns = []; }
    env.items = mergeInterruptedTurns(env.items, turns);
    // The server's message ref on each log item (lib/messageRef.ts): the same `<session>~<uuid>#<part>` the poll
    // route's chatWindow gives it, so a reaction made from either view names one message. Cut-turn lines get none.
    // Parts are numbered over this read (up to TAIL_ITEM_CAP items): only a line cut by that cap, at the very oldest
    // edge of a huge log, could number differently from the poll route.
    env.items = stampRefs(env.items, sid);
    // A reaction wake's silent or emoji answer (chat-transcript markWakeReplies): numbered like the poll route, never sent.
    env.items = dropWakeReplies(env.items);
    // A cut-turn line new this beat and placed before the old tail: every later item moved, which
    // a delta (append only) cannot say. Sent as a snapshot.
    const sentUuids = new Set(this.items.map((it) => it?.uuid));
    const insertedBeforeTail = !rotated && path === this.path && prevCount > 0
      && env.items.some((it, i) => it?.interrupted_turn && !sentUuids.has(it.uuid) && i < prevCount);

    const truncated = !rotated && env.items.length < prevCount;
    // Codex reports a response's tokens after its items: `usage` can land on an item a delta
    // already carried. Deltas only append, so a usage change on a sent item is re-sent as a
    // (non-reset) snapshot. Only the last few sent items can gain one. At the item cap the window
    // slides, so old and new are aligned on the last sent item, never compared by bare index.
    let restated = false;
    if (!rotated && !truncated && path === this.path && prevCount > 0) {
      const shift = alignShift(this.items, env.items);
      if (shift === null) restated = true;               // cannot line them up: re-send everything
      else {
        for (let i = prevCount - 1; i >= Math.max(0, prevCount - 20); i--) {
          if (JSON.stringify(this.items[i]?.usage) !== JSON.stringify(env.items[i + shift]?.usage)) { restated = true; break; }
        }
      }
    }
    if (insertedBeforeTail) restated = true;
    if (!rotated && path === this.path && prevCount > 0 && rsig !== this.rulesSig) restated = true;
    this.rulesSig = rsig;
    this.sid = sid;
    this.path = path;
    this.fileSize = size;
    this.interruptedSig = isig;
    this.items = env.items;

    if (rotated || truncated) return { rotated: true, grewFrom: null, restated: false };
    if (restated) { this.rev++; return { rotated: false, grewFrom: null, restated: true }; }
    if (env.items.length > prevCount) return { rotated: false, grewFrom: prevCount, restated: false };
    return { rotated: false, grewFrom: null, restated: false };
  }

  /** The session tag in event ids: the sid, plus a revision once an already-sent item changed
   *  (late usage), so a client resuming from an older id is no longer "caught up" and gets a
   *  snapshot. Ids stay opaque to clients (they only echo Last-Event-ID). */
  private sidTag(): string {
    return `${this.sid ?? ''}${this.rev ? `~${this.rev}` : ''}`;
  }

  private eventId(): string {
    return `${this.sidTag()}:${this.items.length}`;
  }

  private snapshot(reset = false): TailEvent {
    const windowed = this.items.slice(-this.limit);
    const ev: TailEvent = {
      type: 'snapshot',
      id: this.eventId(),
      agent_id: this.agentId,
      session_id: this.sid,
      grammar_version: GRAMMAR_VERSION,
      items: windowed,
      render_items: buildRenderItems(windowed),
    };
    if (reset) ev.reset = true;
    return ev;
  }

  private delta(fromOffset: number): TailEvent {
    return {
      type: 'delta',
      id: this.eventId(),
      agent_id: this.agentId,
      session_id: this.sid,
      grammar_version: GRAMMAR_VERSION,
      items: this.items.slice(fromOffset),
    };
  }

  /**
   * Subscribe. With a valid `lastEventId` (`<sid>:<offset>`) the subscriber
   * resumes: it immediately receives a delta of anything it missed (nothing if
   * caught up). Otherwise it receives a full snapshot.
   */
  subscribe(cb: Subscriber, lastEventId?: string): () => void {
    this.refresh();
    let resumed = false;
    if (lastEventId) {
      const i = lastEventId.lastIndexOf(':');
      const sid = i >= 0 ? lastEventId.slice(0, i) : '';
      const offset = i >= 0 ? Number(lastEventId.slice(i + 1)) : NaN;
      if (sid && sid === this.sidTag() && Number.isInteger(offset) && offset >= 0 && offset <= this.items.length) {
        resumed = true;
        if (offset < this.items.length) cb(this.delta(offset));
      }
    }
    if (!resumed) cb(this.snapshot());
    this.subs.add(cb);
    return () => { this.subs.delete(cb); };
  }

  get isStopped(): boolean {
    return this.stopped;
  }

  /** A new subscriber to a stopped tailer: tick again (its seat may be back). */
  restart(): void {
    this.stopped = false;
    this.goneBeats = 0;
  }

  /** One poll-tail beat: re-read, broadcast delta / reset-snapshot as needed. 'gone' once, when it stops. */
  tick(): 'gone' | void {
    if (this.subs.size === 0 || this.stopped) return;
    const { rotated, grewFrom, restated } = this.refresh();
    let gone = false;
    try { gone = this.path === null && this.windowGone(); } catch { gone = false; }
    this.goneBeats = gone ? this.goneBeats + 1 : 0;
    if (this.goneBeats >= GONE_TICKS) {
      this.stopped = true;
      return 'gone';
    }
    if (rotated || restated) {
      // reset only for a new session or a shrunk log; a late usage update is a plain snapshot.
      const ev = this.snapshot(rotated);
      for (const cb of this.subs) cb(ev);
      return;
    }
    if (grewFrom !== null) {
      const ev = this.delta(grewFrom);
      for (const cb of this.subs) cb(ev);
    }
  }
}
