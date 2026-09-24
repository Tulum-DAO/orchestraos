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
 * The live-STATE lane (working/idle/waiting/stranded v2 detector) is NOT here
 * by design — state stays its own lane per the locked congruence guardrail.
 *
 * Testable with a manual tick() + injectable resolver (no timers inside);
 * the route (transcript-stream.ts) owns the interval + per-agent registry.
 */
import { readFileSync, statSync } from 'fs';
import { normalizeTranscript, buildRenderItems, GRAMMAR_VERSION } from '../routes/chat-transcript.js';

/** Upper bound on items the SSE tailer will normalize per tick (see the call site). */
const TAIL_ITEM_CAP = 2000;

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
type Subscriber = (ev: TailEvent) => void;

export class TranscriptTailer {
  private agentId: string;
  private resolve: Resolver;
  private limit: number;
  private subs = new Set<Subscriber>();

  private sid: string | null = null;
  private path: string | null = null;
  private fileSize = -1;
  private items: any[] = [];

  constructor(opts: { agentId: string; resolve: Resolver; limit?: number }) {
    this.agentId = opts.agentId;
    this.resolve = opts.resolve;
    this.limit = opts.limit ?? 150;
  }

  get subscriberCount(): number {
    return this.subs.size;
  }

  /** Re-resolve + re-read the transcript. Returns what changed. */
  private refresh(): { rotated: boolean; grewFrom: number | null } {
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
    if (!rotated && path === this.path && size === this.fileSize) {
      return { rotated: false, grewFrom: null };
    }

    let lines: string[] = [];
    try { lines = readFileSync(path, 'utf-8').split('\n'); } catch { lines = []; }
    // BOUNDED. This runs on a ~1 s poll, and codex rollouts reach 13-25 MB: normalizing an
    // unbounded item list every tick is a self-inflicted load collapse (congruence
    // DEC-1790239929422621, both peers). The chat view never renders more than a few hundred
    // items anyway, and the cap is well above the poll route's own 500 ceiling.
    const env = normalizeTranscript(
      lines, this.agentId, sid, TAIL_ITEM_CAP, path.includes('antigravity-cli'));

    const truncated = !rotated && env.items.length < prevCount;
    this.sid = sid;
    this.path = path;
    this.fileSize = size;
    this.items = env.items;

    if (rotated || truncated) return { rotated: true, grewFrom: null };
    if (env.items.length > prevCount) return { rotated: false, grewFrom: prevCount };
    return { rotated: false, grewFrom: null };
  }

  private eventId(): string {
    return `${this.sid ?? ''}:${this.items.length}`;
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
      if (sid && sid === this.sid && Number.isInteger(offset) && offset >= 0 && offset <= this.items.length) {
        resumed = true;
        if (offset < this.items.length) cb(this.delta(offset));
      }
    }
    if (!resumed) cb(this.snapshot());
    this.subs.add(cb);
    return () => { this.subs.delete(cb); };
  }

  /** One poll-tail beat: re-read, broadcast delta / reset-snapshot as needed. */
  tick(): void {
    if (this.subs.size === 0) return;
    const { rotated, grewFrom } = this.refresh();
    if (rotated) {
      const ev = this.snapshot(true);
      for (const cb of this.subs) cb(ev);
      return;
    }
    if (grewFrom !== null) {
      const ev = this.delta(grewFrom);
      for (const cb of this.subs) cb(ev);
    }
  }
}
