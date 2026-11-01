/**
 * Live text for a headless seat: the words of the reply still being written, before the
 * transcript has them.
 *
 * Source: the supervisor's event log (<data dir>/state/headless/<seat>/events.jsonl,
 * scripts/seat_supervisor.py), which records every stdout event of the seat's `claude -p` process.
 * With --include-partial-messages the CLI streams `stream_event`s (message_start,
 * content_block_start/delta/stop) and, once a block is finished, one `assistant` event for that
 * block whose `uuid` is the uuid of the line the transcript gets. Only the main chain's TEXT blocks
 * count (parent_tool_use_id null): thinking is never shown, a subagent's words are not the seat's,
 * and a tool call shows as its own row when the transcript lands.
 *
 * A block leaves the live text when the transcript holds its uuid (the finished message replaces
 * it: no double text, no gap). When the turn ends (a result, or the process exits: an interrupt, a
 * crash) a block that never got a uuid will never land and goes at once; one that has its uuid
 * waits for its line up to LAND_GRACE_MS.
 */
import { closeSync, fstatSync, openSync, readSync } from 'fs';

export const LAND_GRACE_MS = 5000;
/** How much of the log's end is read when a seat is first watched (a turn already under way). */
export const BACKLOG_BYTES = 512 * 1024;

export interface LiveBlock { message_id: string; index: number; uuid: string | null; text: string; closed: boolean }

export interface LiveTextView {
  /** The message the newest block belongs to; null when nothing is live. */
  message_id: string | null;
  /** The words so far, blocks joined by a blank line; '' clears. */
  text: string;
  /** The same words per block, with the transcript uuid each will land as (null until known). */
  blocks: { uuid: string | null; text: string }[];
  /** The turn has ended: no more words are coming, the rest is waiting for its transcript line. */
  done: boolean;
}

const EMPTY: LiveTextView = { message_id: null, text: '', blocks: [], done: false };

/** The state machine over the log's records. Pure: no files, the clock passed in. */
export class LiveTextState {
  blocks: LiveBlock[] = [];
  private message: string | null = null;
  private doneAt: number | null = null;

  apply(rec: any): void {
    if (!rec || typeof rec !== 'object') return;
    if (rec.kind === 'sup' && rec.what === 'exit') { this.end(rec); return; }
    if (rec.kind !== 'out') return;
    const ev = rec.ev;
    if (!ev || typeof ev !== 'object' || ev.parent_tool_use_id != null) return;
    if (ev.type === 'result') { this.end(rec); return; }
    if (ev.type === 'assistant') {
      const msg = ev.message || {};
      const hasText = Array.isArray(msg.content) && msg.content.some((b: any) => b?.type === 'text');
      if (!hasText || typeof ev.uuid !== 'string') return;
      // One assistant event per finished block, in order: it names the first of its message's
      // text blocks still without a uuid.
      const b = this.blocks.find((x) => x.message_id === msg.id && x.uuid === null);
      if (b) { b.uuid = ev.uuid; b.closed = true; }
      return;
    }
    if (ev.type !== 'stream_event') return;
    const e = ev.event || {};
    const mid = typeof ev.api_message_id === 'string' ? ev.api_message_id : this.message;
    switch (e.type) {
      case 'message_start':
        this.message = typeof e.message?.id === 'string' ? e.message.id : mid;
        this.doneAt = null;
        break;
      case 'content_block_start':
        if (e.content_block?.type === 'text' && mid) {
          this.doneAt = null;
          this.blocks.push({ message_id: mid, index: Number(e.index), uuid: null, text: String(e.content_block.text || ''), closed: false });
        }
        break;
      case 'content_block_delta': {
        if (e.delta?.type !== 'text_delta') break;
        const b = this.find(mid, e.index);
        if (b && !b.closed) b.text += String(e.delta.text || '');
        break;
      }
      case 'content_block_stop': {
        const b = this.find(mid, e.index);
        if (b) b.closed = true;
        break;
      }
    }
  }

  private find(mid: string | null, index: unknown): LiveBlock | undefined {
    for (let i = this.blocks.length - 1; i >= 0; i--) {
      const b = this.blocks[i];
      if (b.message_id === mid && b.index === Number(index)) return b;
    }
    return undefined;
  }

  private end(rec: any): void {
    const t = Date.parse(String(rec?.wall ?? ''));
    this.doneAt = Number.isNaN(t) ? Date.now() : t;
  }

  /** Drop what the transcript has, and what a finished turn will never deliver. */
  prune(landed: (uuid: string) => boolean, now: number = Date.now()): void {
    this.blocks = this.blocks.filter((b) => {
      if (b.uuid && landed(b.uuid)) return false;
      if (this.doneAt === null) return true;
      if (!b.uuid) return false;                                   // cut mid-block: never lands
      return now - this.doneAt <= LAND_GRACE_MS;
    });
  }

  /** Everything off (the seat left headless, or went to Dev). */
  clear(): void { this.blocks = []; }

  view(): LiveTextView {
    const shown = this.blocks.filter((b) => b.text.trim());
    if (!shown.length) return EMPTY;
    return {
      message_id: shown[shown.length - 1].message_id,
      text: shown.map((b) => b.text).join('\n\n'),
      blocks: shown.map((b) => ({ uuid: b.uuid, text: b.text })),
      done: this.doneAt !== null,
    };
  }
}

/** Reads a growing log from where it left off; a shorter file starts over. */
export class LogFollower {
  private offset = -1;
  private rest = '';
  constructor(private path: string, private backlog = BACKLOG_BYTES) {}

  /** The complete new lines since the last call ([] when the file is missing). `reset` is true
   *  when the file was replaced or truncated (state built from it is stale). */
  read(): { lines: string[]; reset: boolean } {
    let fd: number;
    try { fd = openSync(this.path, 'r'); } catch { return { lines: [], reset: false }; }
    try {
      const size = fstatSync(fd).size;
      let reset = false;
      let skipFirst = false;
      if (this.offset < 0 || size < this.offset) {
        reset = this.offset >= 0;
        this.offset = Math.max(0, size - this.backlog);
        skipFirst = this.offset > 0;                              // mid-line: the first piece is not a line
        this.rest = '';
      }
      if (size === this.offset) return { lines: [], reset };
      const buf = Buffer.alloc(size - this.offset);
      const n = readSync(fd, buf, 0, buf.length, this.offset);
      this.offset += n;
      const text = this.rest + buf.subarray(0, n).toString('utf-8');
      const parts = text.split('\n');
      this.rest = parts.pop() ?? '';
      if (skipFirst) parts.shift();
      return { lines: parts.filter((l) => l.trim()), reset };
    } finally {
      try { closeSync(fd); } catch { /* best-effort */ }
    }
  }
}

// The event lines that can matter; everything else (system, rate limits, stdin echoes) is not parsed.
const INTERESTING = /"(stream_event|assistant|result|exit)"/;

/** One seat's live text: follow its log, keep the state, tell when the view changes. */
export class LiveText {
  readonly state = new LiveTextState();
  private follower: LogFollower;
  private last = JSON.stringify(EMPTY);

  constructor(path: string, backlog = BACKLOG_BYTES) {
    this.follower = new LogFollower(path, backlog);
  }

  /** Read what is new, drop what landed; the new view when it changed, else null. */
  step(landed: (uuid: string) => boolean, enabled = true, now: number = Date.now()): LiveTextView | null {
    if (!enabled) {
      this.state.clear();
    } else {
      const { lines, reset } = this.follower.read();
      if (reset) this.state.clear();
      for (const l of lines) {
        if (!INTERESTING.test(l)) continue;
        try { this.state.apply(JSON.parse(l)); } catch { /* a torn or bad line */ }
      }
      this.state.prune(landed, now);
    }
    const v = this.state.view();
    const sig = JSON.stringify(v);
    if (sig === this.last) return null;
    this.last = sig;
    return v;
  }

  current(): LiveTextView { return this.state.view(); }
}
