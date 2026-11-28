/**
 * The newest raw lines of a session log, kept small (services/transcript-tail.ts turn(), for live-step's turn marks).
 *
 * The parsed items carry neither of the turn's own markers (a reply's stop_reason, the CLI's turn_duration line), so the
 * step reads them from the raw lines. Never the whole file: the first read takes the log's last lines, reading back from
 * its end until it holds `lines` complete lines, the file's start, or `maxBytes`; later reads take only what was appended,
 * and the oldest lines fall off. A log whose newest `lines` lines are longer than `maxBytes` together is `cut`: the lines
 * kept are fewer, and the caller says so (live-step: a turn the kept lines do not decide is no step, never a guessed one).
 * An append larger than `maxBytes` is a fresh tail read. A shrunk, replaced or rewritten file is read again from its end.
 */
import { closeSync, openSync, readSync, statSync } from 'fs';

/** Lines kept: what live-step's turn marks look at (TURN_SCAN_LINES). */
export const RAW_TAIL_LINES = 200;
/** The most bytes of a log one read takes. */
export const RAW_TAIL_MAX_BYTES = 4 * 1024 * 1024;
const BLOCK = 64 * 1024;
/** Bytes under the kept end compared on each update: a rewrite in place shows there. */
const PROBE_BYTES = 64;

let bytesRead = 0;
let bytesScanned = 0;
/** Tests: the bytes every raw tail has read (never the whole of a big log). */
export function rawTailBytesRead(): number { return bytesRead; }
/** Tests: the bytes a first read has counted newlines in (each byte once: never the growing buffer again per block). */
export function rawTailBytesScanned(): number { return bytesScanned; }

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
  private mtimeMs = -1;
  private end = 0;           // the byte after the last complete line kept
  private partialBytes = 0;  // bytes after it: a line still being written (bytes, not decoded text: a held byte of a
                             // UTF-8 character decodes to three)
  private probe: Buffer = Buffer.alloc(0);   // the bytes just under `end`, as read: still there = the same file, grown
  private midLine = false;   // `end` is inside a line longer than maxBytes: the next newline ends a piece, not a line
  /** Bumped whenever `lines` changes, so a reader can keep what it worked out from the same lines. */
  version = 0;
  /** The last read failed (fail()): the caller reads again on its next tick, whether or not the log grew. */
  failed = false;

  constructor(private readonly max = RAW_TAIL_LINES, private readonly maxBytes = RAW_TAIL_MAX_BYTES) {}

  /** The log could not be read: keep nothing and say so (`cut`, no lines: live-step's turn is not known, so no step),
   *  and read it fresh next time. A read that fails again changes nothing (same lines, same version). */
  fail(): void {
    if (this.failed) return;
    this.path = null; this.ino = -1; this.mtimeMs = -1; this.end = 0; this.partialBytes = 0; this.probe = Buffer.alloc(0);
    this.midLine = false; this.lines = []; this.cut = true; this.failed = true;
    this.version++;
  }

  /** Forget the log (no session, or a new one). */
  reset(): void {
    if (this.path === null && !this.lines.length && !this.failed) return;
    this.path = null; this.ino = -1; this.mtimeMs = -1; this.end = 0; this.partialBytes = 0; this.probe = Buffer.alloc(0);
    this.midLine = false; this.lines = []; this.cut = false; this.failed = false;
    this.version++;
  }

  /** Bring the kept lines up to the file at `path` now. Throws when the file cannot be read (the caller's catch).
   *  Logs are appended to; a file rewritten in place (same inode) is caught by the bytes under `end` no longer being
   *  the ones read (as services/transcript-log-cache.ts probeMatches), and read again from its end. */
  update(path: string): void {
    const st = statSync(path);
    const size = st.size;
    const same = this.path === path && this.ino === st.ino;
    const seen = this.end + this.partialBytes;
    if (same && size === seen && st.mtimeMs === this.mtimeMs) return;
    if (same && size >= seen && size - this.end <= this.maxBytes && this.probeMatches(path)) {
      this.mtimeMs = st.mtimeMs;
      if (size > seen) this.append(path, size);
      return;
    }
    this.fresh(path, st.ino, st.mtimeMs, size);
  }

  private probeMatches(path: string): boolean {
    if (!this.probe.length) return this.end === 0;
    const fd = openSync(path, 'r');
    try { return readRange(fd, this.end - this.probe.length, this.end).equals(this.probe); } finally { closeSync(fd); }
  }

  private keepProbe(fd: number): void {
    this.probe = Buffer.from(readRange(fd, Math.max(0, this.end - PROBE_BYTES), this.end));
  }

  private append(path: string, size: number): void {
    const fd = openSync(path, 'r');
    try {
      const buf = readRange(fd, this.end, size);
      const nl = buf.lastIndexOf(0x0a);
      if (nl < 0) { this.partialBytes = buf.length; return; }
      let added = buf.subarray(0, nl).toString('utf8').split('\n');
      if (this.midLine) { added = added.slice(1); this.midLine = false; }
      this.partialBytes = buf.length - (nl + 1);
      this.end += nl + 1;
      this.keepProbe(fd);
      this.lines = this.lines.concat(added).slice(-this.max);
      // Appended lines only ever fill a cut tail toward `max`; it stays cut while the file holds more above it.
      if (this.lines.length >= this.max) this.cut = false;
      this.version++;
    } finally { closeSync(fd); }
  }

  private fresh(path: string, ino: number, mtimeMs: number, size: number): void {
    const fd = openSync(path, 'r');
    try {
      let lo = size;
      const blocks: Buffer[] = [];
      // Back from the end a block at a time, until the bytes hold max + 1 newlines (max whole lines after the first
      // one, which may be cut), the file's start, or maxBytes. Each block's newlines are counted once, and the blocks
      // joined once at the end.
      let n = 0;
      for (;;) {
        const from = Math.max(0, lo - BLOCK, size - this.maxBytes);
        if (from >= lo) break;
        const block = readRange(fd, from, lo);
        blocks.unshift(block);
        lo = from;
        for (let i = 0; i < block.length; i++) if (block[i] === 0x0a) n++;
        bytesScanned += block.length;
        if (n > this.max || lo === 0) break;
      }
      const buf = blocks.length === 1 ? blocks[0] : Buffer.concat(blocks);
      const nl = buf.lastIndexOf(0x0a);
      let lines = nl < 0 ? [] : buf.subarray(0, nl).toString('utf8').split('\n');
      // A read that did not start at the file's start begins inside a line: that piece is not a line.
      if (lo > 0) lines = lines.slice(1);
      this.path = path; this.ino = ino; this.mtimeMs = mtimeMs;
      this.midLine = nl < 0 && lo > 0;
      if (this.midLine) {
        // one line longer than maxBytes, still unfinished: nothing to keep of it
        this.end = size; this.partialBytes = 0;
      } else {
        this.end = nl < 0 ? 0 : lo + nl + 1;
        this.partialBytes = size - this.end;
      }
      this.keepProbe(fd);
      this.lines = lines.slice(-this.max);
      this.cut = lo > 0 && this.lines.length < this.max;
      this.failed = false;
      this.version++;
    } finally { closeSync(fd); }
  }
}
