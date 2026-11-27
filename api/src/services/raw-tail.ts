/**
 * The newest raw lines of a session log, kept small (services/transcript-tail.ts turn(), for live-step's turn marks).
 *
 * The parsed items carry neither of the turn's own markers (a reply's stop_reason, the CLI's turn_duration line), so the
 * step reads them from the raw lines. Never the whole file: the first read takes the log's last lines, reading back from
 * its end until it holds `lines` complete lines, the file's start, or `maxBytes`; later reads take only what was appended,
 * and the oldest lines fall off. A log whose newest `lines` lines are longer than `maxBytes` together is `cut`: the lines
 * kept are fewer, and the caller says so (live-step: a turn the kept lines do not decide is no step, never a guessed one).
 * An append larger than `maxBytes` is a fresh tail read. A shrunk or replaced file is read again from its end.
 */
import { closeSync, openSync, readSync, statSync } from 'fs';

/** Lines kept: what live-step's turn marks look at (TURN_SCAN_LINES). */
export const RAW_TAIL_LINES = 200;
/** The most bytes of a log one read takes. */
export const RAW_TAIL_MAX_BYTES = 4 * 1024 * 1024;
const BLOCK = 64 * 1024;

let bytesRead = 0;
/** Tests: the bytes every raw tail has read (never the whole of a big log). */
export function rawTailBytesRead(): number { return bytesRead; }

function readRange(fd: number, from: number, to: number): Buffer {
  const buf = Buffer.allocUnsafe(Math.max(0, to - from));
  let got = 0;
  while (got < buf.length) {
    const n = readSync(fd, buf, got, buf.length - got, from + got);
    if (n <= 0) break;
    got += n;
  }
  bytesRead += got;
  return got === buf.length ? buf : buf.subarray(0, got);
}

export class RawTail {
  /** The newest complete lines, oldest first. */
  lines: string[] = [];
  /** The kept lines are fewer than `max` and the file holds more above them (cut by maxBytes). */
  cut = false;
  private path: string | null = null;
  private ino = -1;
  private end = 0;           // the byte after the last complete line kept
  private partial = '';      // bytes after it: a line still being written
  private midLine = false;   // `end` is inside a line longer than maxBytes: the next newline ends a piece, not a line
  /** Bumped whenever `lines` changes, so a reader can keep what it worked out from the same lines. */
  version = 0;

  constructor(private readonly max = RAW_TAIL_LINES, private readonly maxBytes = RAW_TAIL_MAX_BYTES) {}

  /** Forget the log (no session, or a new one). */
  reset(): void {
    if (this.path === null && !this.lines.length) return;
    this.path = null; this.ino = -1; this.end = 0; this.partial = ''; this.midLine = false; this.lines = []; this.cut = false;
    this.version++;
  }

  /** Bring the kept lines up to the file at `path` now. Throws when the file cannot be read (the caller's catch). */
  update(path: string): void {
    const st = statSync(path);
    const size = st.size;
    const known = this.path === path && this.ino === st.ino;
    const seen = this.end + Buffer.byteLength(this.partial, 'utf8');
    if (known && size === seen) return;
    if (known && size > seen && size - this.end <= this.maxBytes) { this.append(path, size); return; }
    this.fresh(path, st.ino, size);
  }

  private append(path: string, size: number): void {
    const fd = openSync(path, 'r');
    let buf: Buffer;
    try { buf = readRange(fd, this.end, size); } finally { closeSync(fd); }
    const nl = buf.lastIndexOf(0x0a);
    if (nl < 0) { this.partial = buf.toString('utf8'); return; }
    let added = buf.subarray(0, nl).toString('utf8').split('\n');
    if (this.midLine) { added = added.slice(1); this.midLine = false; }
    this.partial = buf.subarray(nl + 1).toString('utf8');
    this.end += nl + 1;
    this.lines = this.lines.concat(added).slice(-this.max);
    // Appended lines only ever fill a cut tail toward `max`; it stays cut while the file holds more above it.
    if (this.lines.length >= this.max) this.cut = false;
    this.version++;
  }

  private fresh(path: string, ino: number, size: number): void {
    const fd = openSync(path, 'r');
    let lo = size;
    let buf: Buffer = Buffer.alloc(0);
    try {
      // Back from the end a block at a time, until the bytes hold max + 1 newlines (max whole lines after the first
      // one, which may be cut), the file's start, or maxBytes.
      for (;;) {
        const from = Math.max(0, lo - BLOCK, size - this.maxBytes);
        if (from >= lo) break;
        buf = Buffer.concat([readRange(fd, from, lo), buf]);
        lo = from;
        let n = 0;
        for (let i = 0; i < buf.length && n <= this.max; i++) if (buf[i] === 0x0a) n++;
        if (n > this.max || lo === 0) break;
      }
    } finally { closeSync(fd); }
    const nl = buf.lastIndexOf(0x0a);
    let lines = nl < 0 ? [] : buf.subarray(0, nl).toString('utf8').split('\n');
    // A read that did not start at the file's start begins inside a line: that piece is not a line.
    if (lo > 0) lines = lines.slice(1);
    this.path = path; this.ino = ino;
    this.end = nl < 0 ? lo : lo + nl + 1;
    this.partial = nl < 0 ? (lo === 0 ? buf.toString('utf8') : '') : buf.subarray(nl + 1).toString('utf8');
    this.midLine = nl < 0 && lo > 0;
    if (this.midLine) this.end = size;   // one line longer than maxBytes, still unfinished: nothing to keep of it
    this.lines = lines.slice(-this.max);
    this.cut = lo > 0 && this.lines.length < this.max;
    this.version++;
  }
}
