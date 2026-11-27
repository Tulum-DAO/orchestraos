/**
 * TranscriptTailer — the F1 streaming seam (SPEC_transcript-ecosystem §4).
 *
 * Tails an agent's append-only session JSONL and pushes newly-normalized
 * transcript items in the F0 v2 contract shape (grammar_version, canonical
 * summary, capped input — via normalizeTranscript; NEVER a parallel grammar).
 *
 * Model:
 *  - snapshot: full backfill (last `limit` items + server-paired render_items)
 *  - delta:    only the items appended since the subscriber's last one
 *  - resume:   SSE Last-Event-ID = `<tag>:<sequence>`: every item the tailer
 *              reads gets the next number, and an id names its last item's.
 *              Below the cap that is the item count in the FULL normalized
 *              transcript and the tag is the session id, as it always was. A
 *              window the cap cut numbers from its own read and the tag carries
 *              that numbering's epoch. Valid id -> delta of the missed tail;
 *              stale/foreign id, or one slid out of the kept window -> snapshot.
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
import { statSync } from 'fs';
import {
  normalizeTranscript, normalizeTranscriptFile, buildRenderItems, dropWakeReplies, GRAMMAR_VERSION,
  mergeInterruptedTurns, readInterruptedTurns, interruptedTurnsSig, type InterruptedTurn,
} from '../routes/chat-transcript.js';
import { storeRules } from './conversation-store.js';
import { finalReplyUuids, turnScan } from './live-step.js';
import { RawTail } from './raw-tail.js';
import { stampRefs } from '../lib/messageRef.js';
import { stampReplyTo } from '../lib/reply-to-store.js';

/** Upper bound on items the SSE tailer will normalize per tick (see the call site). */
const TAIL_ITEM_CAP = 2000;
let tailItemCap = TAIL_ITEM_CAP;
/** Tests: a smaller cap, so a modest fixture shows the first read costing the cap's items, not the file. */
export function tailerCapForTests(n: number | null): void { tailItemCap = n ?? TAIL_ITEM_CAP; }
/** This process's mark in an epoch (see TranscriptTailer.epoch), and the numberings started so far. */
const BOOT = Math.random().toString(36).slice(2, 6);
let epochs = 0;
/** The version of the chat a stream's event ids count in (sidTag). Bumped when what a log builds to changes under a
 *  running client: the head cut (chat-transcript.ts liveFrom) left the hold phase out, so every count below the cap
 *  moved, and an id from a server without it would resume at a count that names another item. Such an id gets a
 *  snapshot, never a delta. */
const STREAM_CUT = 'h1';

/** Beats in a row with no transcript and no tmux window before a tailer stops (about 30 s at the route's 1 s beat). */
export const GONE_TICKS = 30;

export interface TailEvent {
  type: 'snapshot' | 'delta';
  /** SSE event id: `<tag>:<sequence number of the newest item after this event>` (see eventId) */
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

/** Items before the last kept one that must line up too: one key alone can match a copy of it among the new items. */
const ALIGN_RUN = 4;

/** How far `next` slid relative to `prev` (index in next = index in prev + shift), found by
 *  locating prev's last item in next, with the items before it; null when it cannot be found. 0 below the cap.
 *  The match nearest its old place wins: a beat moves it by the few items it brought. */
export function alignShift(prev: any[], next: any[]): number | null {
  if (!prev.length) return 0;
  const last = prev.length - 1;
  const run = prev.slice(Math.max(0, last - ALIGN_RUN + 1)).map(itemKey).reverse();
  const fits = (j: number): boolean => {
    for (let d = 0; d < run.length && j - d >= 0; d++) if (itemKey(next[j - d]) !== run[d]) return false;
    return true;
  };
  for (let d = 0; d <= Math.max(last, next.length - 1 - last); d++) {
    if (last - d >= 0 && last - d < next.length && fits(last - d)) return d ? -d : 0;
    if (d > 0 && last + d < next.length && fits(last + d)) return d;
  }
  return null;
}

const READ_ERROR_CODES = new Set(['ENOENT', 'EACCES', 'EPERM', 'EISDIR', 'ENOTDIR', 'ELOOP', 'ENAMETOOLONG']);
/** A failure to stat or read the log file (the file is gone, a directory, unreadable), as opposed to a failure to parse it.
 *  Transient errors (EMFILE, EBUSY) are not here on purpose: out of tick() they propagate to the beat's catch and the next
 *  beat retries with nothing sent, where a fallback would blank the chat with an empty reset snapshot for a beat. Out of
 *  subscribe() (its refresh() on connect) they propagate to the SSE route after its 200 head: the socket is destroyed and
 *  the client's EventSource reconnects after its retry delay. A failed connect, never an empty chat. */
function isReadError(err: unknown): boolean {
  const code = (err as { code?: unknown } | null)?.code;
  return typeof code === 'string' && READ_ERROR_CODES.has(code);
}

/** The turn marks a tailer reads from its log's raw lines (turn()). */
export interface TurnMarks { ended: string | null; finals: ReadonlySet<string>; known: boolean }

export class TranscriptTailer {
  private agentId: string;
  private resolve: Resolver;
  private limit: number;
  /** Each subscriber with ITS OWN window (items per snapshot, and the most it is ever sent at once). */
  private subs = new Map<Subscriber, number>();

  private sid: string | null = null;
  private path: string | null = null;
  private fileSize = -1;
  /** The log's newest raw lines (services/raw-tail.ts), for turn(): never the whole file. */
  private raw = new RawTail();
  private turnMemo: { version: number; turn: TurnMarks } | null = null;
  private items: any[] = [];
  private rev = 0;           // bumped when an already-sent item changes (see sidTag)
  /** The sequence number of the newest kept item: every item this tailer has read counts once (see eventId). */
  private seq = 0;
  /** '' when the numbering counts from the log's first item (the read that started it held all of it); otherwise this
   *  numbering's own nonce, so an id from another tailer (an API restart) is never read as one of these (see sidTag). */
  private epoch = '';
  private interrupted: Interrupted;
  private interruptedSig = '';
  private rulesSig = '';
  private windowGone: () => boolean;
  private goneBeats = 0;
  private stopped = false;
  /** A subagent's own log (the agent-tree drill-in): every row is sidechain, and the seat's cut turns and
   *  store rules are the seat's, not its. */
  private keepSidechain: boolean;

  constructor(opts: { agentId: string; resolve: Resolver; limit?: number; interrupted?: Interrupted;
    windowGone?: () => boolean; keepSidechain?: boolean }) {
    this.agentId = opts.agentId;
    this.resolve = opts.resolve;
    this.limit = opts.limit ?? 150;
    this.windowGone = opts.windowGone ?? (() => false);
    this.keepSidechain = opts.keepSidechain ?? false;
    this.interrupted = opts.interrupted ?? (this.keepSidechain ? { read: () => [], sig: () => '' } : {
      read: () => readInterruptedTurns(opts.agentId),
      sig: () => interruptedTurnsSig(opts.agentId),
    });
  }

  get subscriberCount(): number {
    return this.subs.size;
  }

  /** The newest items read so far (the live step: the tool call still running). Read only. */
  recentItems(n = 50): readonly any[] {
    return this.items.slice(-n);
  }

  /** The turn as the log's newest raw lines mark it (live-step): when it ended (else null), which lines are final
   *  replies, and whether those lines can say (`known` false: Claude Code lines cut short by their size, none of which
   *  decides the turn; the step is then none, never a guess. Another CLI's lines carry no turn marks: known). Kept for
   *  the same lines, so the quarter-second step beat parses nothing new while the log is still. */
  turn(): TurnMarks {
    const m = this.turnMemo;
    if (m && m.version === this.raw.version) return m.turn;
    const lines = this.raw.lines;
    const scan = turnScan(lines);
    const turn = { ended: scan.ended, finals: finalReplyUuids(lines), known: scan.decided || !scan.claude || !this.raw.cut };
    this.turnMemo = { version: this.raw.version, turn };
    return turn;
  }

  /** Whether the file this tailer follows has grown since its last read: a stat only, no re-resolve (the stream's
   *  quarter-second peek, routes/transcript-stream.ts). */
  grew(): boolean {
    if (!this.path || this.fileSize < 0) return false;
    try { return statSync(this.path).size !== this.fileSize; } catch { return false; }
  }

  /** Whether the items read so far hold this uuid (live text: its finished line has landed). */
  hasItemUuid(uuid: string): boolean {
    for (let i = this.items.length - 1; i >= 0; i--) if (this.items[i]?.uuid === uuid) return true;
    return false;
  }

  /** Re-resolve + re-read the transcript. Returns what changed. */
  private refresh(): { rotated: boolean; fresh: number; restated?: boolean } {
    const { path, sid } = this.resolve();
    const prevSid = this.sid;
    const prevCount = this.items.length;
    const rotated = this.sid !== null && (sid !== prevSid || path !== this.path);

    if (!path) {
      this.sid = sid;
      this.path = null;
      this.fileSize = -1;
      this.raw.reset();
      this.items = [];
      this.seq = 0;
      this.epoch = '';
      return { rotated, fresh: 0 };
    }

    // Cheap no-op check: same file, same size -> nothing to re-normalize.
    let size = -1;
    try { size = statSync(path).size; } catch { size = -1; }
    let isig = '';
    try { isig = this.interrupted.sig(); } catch { isig = ''; }
    // The store's rules for this session (seen-only entries, seed rows): marking an entry seen-only changes what is
    // shown without the log growing, so it re-reads and re-sends the window.
    let rsig = '';
    try { rsig = this.keepSidechain ? '' : storeRules(sid).sig; } catch { rsig = ''; }
    if (!rotated && path === this.path && size === this.fileSize && isig === this.interruptedSig && rsig === this.rulesSig) {
      return { rotated: false, fresh: 0 };
    }

    // The raw lines for turn(): the appended bytes only (a fresh tail read for a new or replaced file).
    try { this.raw.update(path); } catch { this.raw.reset(); }
    // Through the parsed-log cache (services/transcript-log-cache.ts): the file is parsed once, then on each tick
    // that found it grown only the appended lines are read, with the Claude grammar's carried state. Before this,
    // every tick that found a new line re-read and re-parsed the whole file (4 MB: seconds on a throttled box, on a
    // ~1 s poll per live seat). An unreadable file is an empty log, as before.
    // BOUNDED. Codex rollouts reach 13-25 MB: normalizing an unbounded item list every tick is a self-inflicted load
    // collapse (congruence DEC-1790239929422621, both peers). The chat view never renders more than a few hundred
    // items anyway, and the cap is well above the poll route's own 500 ceiling.
    let env: ReturnType<typeof normalizeTranscriptFile>;
    try {
      // Tail-first: the first read of a log is its last bytes, doubled until they hold the cap's items (chat-transcript.ts
      // normalizeTranscriptFile); later ticks ride the kept chunk's appends. Never a whole-file parse of a big log per seat.
      env = normalizeTranscriptFile(path, this.agentId, sid, tailItemCap, path.includes('antigravity-cli'), false, true, this.keepSidechain, true);
    } catch (err) {
      // Only the file being unreadable is an empty log (what readFileSync's catch gave before); a parser or store-rule
      // error is a bug and still propagates out of tick(), as it did.
      if (!isReadError(err)) throw err;
      env = normalizeTranscript([], this.agentId, sid, tailItemCap, false, false, null, true, this.keepSidechain);
    }
    // What the read itself held, before the merges below: fewer than the cap is the whole log.
    const readCount = env.items.length;
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
    // reply_to (reply threads P1): the same stamp the poll route's finishWindow gives its items. Fail-open.
    env.items = stampReplyTo(env.items, this.agentId);
    // Old and new windows of one log: at the item cap the window slides (one in, one out, its length stuck at the cap),
    // so they are aligned on the last kept item, never compared by bare index. null: they cannot be lined up.
    // A shorter window is a shrunk log, except one the cap cut: a window never starts inside a line, so it is one item
    // longer while a line of two items sits at its front and shorter again when that line slides out (unless the file
    // itself got smaller: that is a rewrite).
    const cut = readCount >= tailItemCap;
    const shorter = !rotated && env.items.length < prevCount;
    const same = !rotated && path === this.path && prevCount > 0 && (!shorter || (cut && size >= this.fileSize));
    const shift = same ? alignShift(this.items, env.items) : null;
    const truncated = shorter && shift === null;
    // The new items: those after the last kept one (at the cap, as many as slid out the front).
    const fresh = shift === null ? 0 : Math.max(0, env.items.length - (prevCount + shift));
    // A cut-turn line new this beat and placed before the old tail: every later item moved, which
    // a delta (append only) cannot say. Sent as a snapshot.
    const sentUuids = new Set(this.items.map((it) => it?.uuid));
    const insertedBeforeTail = shift !== null
      && env.items.some((it, i) => it?.interrupted_turn && !sentUuids.has(it.uuid) && i < prevCount + shift);

    // Codex reports a response's tokens after its items: `usage` can land on an item a delta
    // already carried. Deltas only append, so a usage change on a sent item is re-sent as a
    // (non-reset) snapshot. Only the last few sent items can gain one.
    let restated = false;
    if (prevCount > 0 && shift === null) restated = true;  // cannot line them up: re-send everything
    else if (shift !== null) {
      for (let i = prevCount - 1; i >= Math.max(0, prevCount - 20); i--) {
        if (JSON.stringify(this.items[i]?.usage) !== JSON.stringify(env.items[i + shift]?.usage)) { restated = true; break; }
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

    // Below the cap the read held the whole log: the number is its item count, so another tailer's numbering of the same
    // log agrees. A window the cap cut goes on counting across the windows it can line up (from the log's first item, when
    // it crossed the cap in this tailer) and starts again where it cannot (a new session, a shrunk log, a window that does
    // not line up, the first read), at the window's own count with a nonce of its own.
    if (!cut) {
      this.seq = env.items.length;
      this.epoch = '';
    } else if (shift !== null) this.seq += fresh;
    else {
      this.seq = env.items.length;
      this.epoch = `e${(++epochs).toString(36)}${BOOT}`;
    }

    if (rotated || truncated) return { rotated: true, fresh: 0, restated: false };
    if (restated) { this.rev++; return { rotated: false, fresh: 0, restated: true }; }
    // The first read of a log (or of one that was empty): every item is new.
    if (prevCount === 0) return { rotated: false, fresh: env.items.length, restated: false };
    return { rotated: false, fresh, restated: false };
  }

  /** The session tag in event ids: the sid, plus the numbering's epoch when it does not count from the log's first item,
   *  plus a revision once an already-sent item changed (late usage), so a client resuming from an older id is no longer
   *  "caught up" and gets a snapshot. Ids stay opaque to clients (they only echo Last-Event-ID). */
  private sidTag(): string {
    return `${this.sid ?? ''}~${STREAM_CUT}${this.epoch ? `~${this.epoch}` : ''}${this.rev ? `~${this.rev}` : ''}`;
  }

  /** `<tag>:<sequence number of the newest item>`. Below the cap that is the item count, as it always was; at the cap
   *  it keeps counting while the window slides (a window offset stops there, and a resume read as caught up). */
  private eventId(): string {
    return `${this.sidTag()}:${this.seq}`;
  }

  /** The newest `limit` items as a snapshot. Built once per (limit, reset) within one broadcast: `memo` is the broadcast's
   *  own map, so two subscribers with the same window share one render pass and never one from an earlier read. */
  private snapshot(limit: number, reset = false, memo?: Map<string, TailEvent>): TailEvent {
    const key = `${limit}:${reset ? 1 : 0}`;
    const kept = memo?.get(key);
    if (kept) return kept;
    const windowed = this.items.slice(-limit);
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
    memo?.set(key, ev);
    return ev;
  }

  /** The newest `count` items (the ones a subscriber has not had). */
  private delta(count: number): TailEvent {
    return {
      type: 'delta',
      id: this.eventId(),
      agent_id: this.agentId,
      session_id: this.sid,
      grammar_version: GRAMMAR_VERSION,
      items: count > 0 ? this.items.slice(-count) : [],
    };
  }

  /**
   * Subscribe with a window of `limit` items (the tailer's default when not given). With a valid `lastEventId`
   * (`<tag>:<sequence>`) the subscriber resumes: it immediately receives a delta of anything it missed (nothing if
   * caught up). Otherwise it receives a snapshot of its window. A subscriber is never sent more items at once than
   * its window: a resume that missed more than `limit` items (an API restart under a busy seat drew a 1500-row
   * delta) gets its snapshot instead, which holds the newest `limit` and says nothing older is missing. So does one
   * whose last item slid out of the kept window, or a sequence this numbering has not reached.
   */
  subscribe(cb: Subscriber, lastEventId?: string, limit?: number): () => void {
    const window = Number.isInteger(limit) && (limit as number) > 0 ? (limit as number) : this.limit;
    // What this read found new since the last beat goes to the subscribers already here, as the beat would have sent
    // it: the next beat's read finds nothing new (a second page opened between beats took two items from the first).
    this.broadcast(this.refresh());
    let resumed = false;
    if (lastEventId) {
      const i = lastEventId.lastIndexOf(':');
      const sid = i >= 0 ? lastEventId.slice(0, i) : '';
      const seen = i >= 0 ? Number(lastEventId.slice(i + 1)) : NaN;
      if (sid && sid === this.sidTag() && Number.isInteger(seen)) {
        const missed = this.seq - seen;
        if (missed >= 0 && missed <= Math.min(window, this.items.length)) {
          resumed = true;
          if (missed > 0) cb(this.delta(missed));
        }
      }
    }
    if (!resumed) cb(this.snapshot(window));
    this.subs.set(cb, window);
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
    const { rotated, fresh, restated } = this.refresh();
    let gone = false;
    try { gone = this.path === null && this.windowGone(); } catch { gone = false; }
    this.goneBeats = gone ? this.goneBeats + 1 : 0;
    if (this.goneBeats >= GONE_TICKS) {
      this.stopped = true;
      return 'gone';
    }
    this.broadcast({ rotated, fresh, restated });
  }

  /** What one read changed, to every subscriber. */
  private broadcast({ rotated, fresh, restated }: ReturnType<TranscriptTailer['refresh']>): void {
    if (this.subs.size === 0) return;
    if (rotated || restated) {
      // reset only for a new session or a shrunk log; a late usage update is a plain snapshot. Each subscriber's own
      // window; one render pass per distinct window.
      const memo = new Map<string, TailEvent>();
      for (const [cb, window] of this.subs) cb(this.snapshot(window, rotated, memo));
      return;
    }
    if (fresh > 0) {
      // A growth that fits a subscriber's window is its delta; one that does not (a burst after a stall, a resumed
      // seat) is its snapshot: the newest `window` items, never more than the window at once.
      const ev = this.delta(fresh);
      const memo = new Map<string, TailEvent>();
      for (const [cb, window] of this.subs) cb(ev.items.length <= window ? ev : this.snapshot(window, false, memo));
    }
  }
}
