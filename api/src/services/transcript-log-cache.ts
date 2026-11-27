/**
 * Parsed conversation logs, kept between requests, read TAIL-FIRST.
 *
 * The stitched chat reads every generation of a lineage, and a page further back must not parse
 * every file again: a retired generation's log never changes, and the current one only grows. An
 * entry is reused while the file's inode, size and mtime are the ones it was read at. When the
 * file GREW and is still the same file (same inode, the bytes just under the old end unchanged),
 * only the new bytes are read: the complete new lines are parsed with the grammar's carried state
 * (parser.append) and the whole-array passes run again (parser.finish). Anything else (shorter,
 * rewritten under the old end, replaced by another inode, a grammar that cannot resume) is a full
 * parse. Bounded by total source bytes and by count, least recently used out first; a file bigger
 * than the whole budget is parsed every time and never kept.
 *
 * Why: a busy seat appends to a multi-megabyte log many times a minute, so on a ~1 s poll nearly
 * every request found the cache stale and parsed the whole file again (4 MB: 0.8 s of read + JSON
 * on an idle box, 10+ s on a throttled one, past the client's timeout). An append is a few lines.
 *
 * TAIL READS (step 2 part B): `cachedLogTail(path, parser, wantBytes)` parses the file from a line start at or under
 * size - wantBytes, so a page costs the bytes of the page: the entry carries `start` (0 = the whole file). One entry per
 * (grammar, file): a wider request parses only the lines above the entry and puts them above its items (prefixed: the
 * grammar must say its state at the join carries nothing, `clean`; else the range is re-parsed), REPLACING the entry
 * (ranges only widen, never shrink), the whole file is the widest, and an append extends a tail entry like any other.
 * A cursor page's window (`logRange`) is kept once per (grammar, file) and grown the same way, back by a prefix or
 * forward through the grammar's state at its end. A grammar that cannot be
 * read from a chunk (codex/grok/antigravity rollouts carry a header line; `full` hands back no state) falls back to the
 * whole file once. Every item of a resumable grammar carries `line_off` (the byte offset of its log line) and
 * `line_part` (its index within that line): what a byte cursor names.
 *
 * Callers must not mutate the items they get back: they are shared with the next request.
 */
import { closeSync, openSync, readSync, statSync } from 'fs';

export const LOG_CACHE_MAX_BYTES = 64 * 1024 * 1024;   // source bytes; parsed items take several times that
export const LOG_CACHE_MAX_FILES = 32;
/** Bytes just under the parsed end that an append must reproduce: a rewrite in place that keeps the
 *  size growing is still caught, because the old end's bytes changed. */
export const APPEND_PROBE_BYTES = 64;

/** A log grammar the cache can drive: `full` parses whole lines and returns the state an `append`
 *  continues from (null when the grammar cannot resume: the cache then re-parses in full on every
 *  change); `finish` runs the whole-array passes over the raw items, every time. */
export interface LogParser<S = unknown> {
  /** What of this grammar changes the items it yields (its flags): two parsers with different keys keep separate
   *  entries for one file. Without it, a subagent log read once WITH its sidechain rows served them to a plain reader. */
  readonly key: string;
  /** Items may carry `li`, the index in `lines` of the line they came from: the cache turns it into the line's byte
   *  offset (`line_off`) and the item's index within the line (`line_part`). A grammar without it yields no offsets. */
  full(lines: string[]): { items: any[]; state: S | null };
  append(lines: string[], state: S): { items: any[]; state: S };
  /** `atStart`: the raw items begin at the file's first line (a tail read's do not: its first user text is not the
   *  spawn brief, and the items before its first turn opener belong to a turn whose opener is above the chunk). */
  finish(raw: any[], state: S | null, atStart: boolean): any[];
  /** True when `state`, at the end of some lines, carries nothing into the line after them: a chunk parsed fresh from
   *  that line equals the rest of a parse that ran through it, so the two can be STITCHED (a widened read parses only
   *  the lines it did not have). Absent: never stitch (every widening re-parses). */
  clean?(state: S): boolean;
}

export interface CachedLog {
  readonly items: readonly any[];
  /** Derived views of `items` (a filtered list, an index), dropped with the entry. */
  readonly memo: Map<string, unknown>;
  /** The byte offset of the first parsed line: 0 for the whole file, a line start for a tail read. */
  readonly start: number;
}

interface Entry extends CachedLog {
  ino: number; size: number; mtimeMs: number;
  /** Bytes parsed: the end of the last complete line read. A half-written last line is re-read whole later. */
  offset: number;
  /** The last APPEND_PROBE_BYTES of the parsed bytes. */
  probe: Buffer;
  raw: any[];
  state: unknown | null;
}

const entries = new Map<string, Entry>();   // by `${parser.key}\n${path}`; insertion order = least recently used first

/** The last middle window read of a file (logRange): a cursor page widens it either way, so the next read parses only
 *  the lines it did not have. One per slot, never counted toward the entries' budget, capped at RANGE_MEMO_MAX_BYTES. */
interface RangeEntry extends CachedLog {
  ino: number; size: number; mtimeMs: number;
  end: number;
  raw: any[];
  /** The grammar's state at `end` (an append continues from it), or null for a grammar read whole. */
  state: unknown | null;
}
const ranges = new Map<string, RangeEntry>();   // insertion order = least recently used first
export const RANGE_MEMO_MAX_BYTES = 16 * 1024 * 1024;          // one window
export const RANGE_MEMO_MAX_TOTAL_BYTES = 64 * 1024 * 1024;    // every kept window together
export const RANGE_MEMO_MAX_FILES = 16;
let rangeBytes = 0;
/** Tests: how many cursor windows are kept, and their source bytes together. */
export function logRangeMemoStats(): { files: number; bytes: number } { return { files: ranges.size, bytes: rangeBytes }; }
let bytes = 0;
let parses = 0;        // every read that produced items (full or append)
let fullParses = 0;
let appends = 0;
let tailParses = 0;    // range parses from a line start > 0
let bytesRead = 0;     // file bytes read by every parse (tests: a tail read costs the tail)

/** How many reads produced items (tests: a page that hits the cache parses nothing). */
export function logCacheParses(): number { return parses; }
/** How many of those were a whole-file parse. */
export function logCacheFullParses(): number { return fullParses; }
/** How many were an append read (new bytes only). */
export function logCacheAppends(): number { return appends; }

/** How many reads parsed a tail range (start > 0) rather than the whole file. */
export function logCacheTailParses(): number { return tailParses; }
/** How many file bytes the parses read in all. */
export function logCacheBytesRead(): number { return bytesRead; }

export function clearLogCache(): void { entries.clear(); ranges.clear(); bytes = 0; rangeBytes = 0; }

/** Tests: the entries' byte budget (LOG_CACHE_MAX_BYTES in production); null restores it. */
export function logCacheBudgetForTests(n: number | null): void { budget = n ?? LOG_CACHE_MAX_BYTES; }
/** Tests: what the entries hold. */
export function logCacheStats(): { files: number; bytes: number } { return { files: entries.size, bytes }; }
let budget = LOG_CACHE_MAX_BYTES;

/** What an entry costs the budget: the bytes it parsed. */
const cost = (e: Entry): number => e.size - e.start;

function drop(slot: string): void {
  const e = entries.get(slot);
  if (!e) return;
  entries.delete(slot);
  bytes -= cost(e);
}

/** Keep an entry that fits the budget (one over it is handed back but never kept), evicting the least recently used. */
function keep(slot: string, entry: Entry): void {
  if (cost(entry) > budget) return;
  entries.set(slot, entry);
  bytes += cost(entry);
  for (const [k] of entries) {
    if (bytes <= budget && entries.size <= LOG_CACHE_MAX_FILES) break;
    if (k !== slot) drop(k);
  }
}

/** [from, to) of the file. */
function readRange(path: string, from: number, to: number): Buffer {
  const fd = openSync(path, 'r');
  try {
    const buf = Buffer.allocUnsafe(Math.max(0, to - from));
    let got = 0;
    while (got < buf.length) {
      const n = readSync(fd, buf, got, buf.length - got, from + got);
      if (n <= 0) break;
      got += n;
    }
    bytesRead += got;
    return got === buf.length ? buf : buf.subarray(0, got);
  } finally {
    closeSync(fd);
  }
}

const SCAN_BLOCK = 4096;

/** The start of the line that holds byte `pos` (0 <= pos <= size): one past the last newline before `pos`, or 0. */
function lineStartAt(path: string, pos: number): number {
  let hi = pos;
  while (hi > 0) {
    const lo = Math.max(0, hi - SCAN_BLOCK);
    const block = readRange(path, lo, hi);
    const nl = block.lastIndexOf(0x0a);
    if (nl >= 0) return lo + nl + 1;
    hi = lo;
  }
  return 0;
}

/** The uuid and message id of the JSON line starting at `off`, or null when that line is not one JSON value with a uuid. */
function keysOfLineAt(path: string, off: number, size: number):
    { uuid: string; mid: string | null; assistant: boolean; sidechain: boolean; next: number } | null {
  let end = off;
  let text = '';
  let next = size;
  while (end < size) {
    const block = readRange(path, end, Math.min(size, end + SCAN_BLOCK));
    const nl = block.indexOf(0x0a);
    if (nl >= 0) { text += block.subarray(0, nl).toString('utf-8'); next = end + nl + 1; break; }
    text += block.toString('utf-8');
    end += block.length;
    if (!block.length) break;
  }
  try {
    const o = JSON.parse(text);
    const u = o?.uuid;
    if (typeof u !== 'string' || !u) return null;
    const m = o?.message?.id;
    // `next`: where the line after it starts (read on the way: a long line is not read again going forward).
    return { uuid: u, mid: typeof m === 'string' && m ? m : null, assistant: o?.type === 'assistant',
      sidechain: o?.isSidechain === true, next };
  } catch {
    return null;
  }
}

/** Two lines of ONE record group: the same uuid (one entry's items), or the same message id (a streamed reply's
 *  content blocks, one line each with its own uuid, whose usage is counted once across them). */
const sameRecord = (a: { uuid: string; mid: string | null }, b: { uuid: string; mid: string | null } | null): boolean =>
  !!b && (a.uuid === b.uuid || (!!a.mid && a.mid === b.mid));

/** Items that came with a line index (`li`): stamp the line's byte offset from `base` and the item's index within
 *  the line, then drop `li`. Items without one are left as they are (a whole-file grammar). */
function stampLines(items: any[], lines: string[], base: number): void {
  let offs: number[] | null = null;
  let lastLi = -1;
  let part = 0;
  for (const it of items) {
    if (!it || typeof it.li !== 'number') continue;
    if (!offs) {
      offs = new Array(lines.length);
      let o = base;
      for (let i = 0; i < lines.length; i++) { offs[i] = o; o += Buffer.byteLength(lines[i], 'utf8') + 1; }
    }
    if (it.li !== lastLi) { lastLi = it.li; part = 0; }
    it.line_off = offs[it.li];
    it.line_part = part++;
    delete it.li;
  }
}

/** The bytes just under the parsed end are still what was parsed: this is the same file, grown. Logs are taken as
 *  APPEND-ONLY: an in-place edit of an earlier line that keeps the file's length and the last APPEND_PROBE_BYTES under
 *  the parsed end (a scrub tool rewriting one value) is not seen until the next full parse (restart, eviction, a
 *  rewrite under the end, or another inode). Every runtime this reads writes its session log by appending. */
function probeMatches(path: string, hit: Entry): boolean {
  if (!hit.probe.length) return hit.offset === 0;
  try {
    return readRange(path, hit.offset - hit.probe.length, hit.offset).equals(hit.probe);
  } catch {
    return false;
  }
}

/** A complete JSONL line (one JSON value): a line cut mid-way is never valid JSON. */
function isWholeJsonLine(text: string): boolean {
  if (!text.trim()) return false;
  try { JSON.parse(text); return true; } catch { return false; }
}

/** The complete lines of `buf` (up to and including its last newline) and where they end. */
function completeLines(buf: Buffer): { lines: string[]; end: number } {
  const cut = buf.lastIndexOf(0x0a);
  if (cut < 0) return { lines: [], end: 0 };
  return { lines: buf.subarray(0, cut + 1).toString('utf-8').split('\n'), end: cut + 1 };
}

function probeOf(prev: Buffer, added: Buffer): Buffer {
  const joined = prev.length ? Buffer.concat([prev, added]) : added;
  return Buffer.from(joined.subarray(Math.max(0, joined.length - APPEND_PROBE_BYTES)));
}

/** The parsed items of `path`, from the cache while the file is unchanged; the new lines only when it
 *  grew. Throws when the file cannot be read (the caller decides what a missing log means). */
export function cachedLog<S>(path: string, parser: LogParser<S>): CachedLog {
  return read(path, parser, Number.POSITIVE_INFINITY);
}

/** The items of the last `wantBytes` of `path` (rounded out to a line start; the whole file when it is that small,
 *  or when the grammar cannot be read from a chunk). The entry's `start` says where the items begin. */
export function cachedLogTail<S>(path: string, parser: LogParser<S>, wantBytes: number): CachedLog {
  return read(path, parser, Math.max(0, wantBytes));
}

/** An entry covers a want when it is the whole file or reaches back `want` bytes from the end. */
const covers = (e: Entry, want: number): boolean => e.start === 0 || e.size - e.start >= want;

function read<S>(path: string, parser: LogParser<S>, want: number): Entry {
  // Stat BEFORE reading: if the file grows in between, the entry records the older size and the
  // next request reads again. The other order could keep a short read under the longer size.
  const st = statSync(path);
  const slot = `${parser.key}\n${path}`;
  const hit = entries.get(slot);
  if (hit && hit.ino === st.ino && hit.size === st.size && hit.mtimeMs === st.mtimeMs && covers(hit, want)) {   // ino: an atomic replace keeps size and mtime
    entries.delete(slot);
    entries.set(slot, hit);
    return hit;
  }
  if (hit && hit.state !== null && hit.ino === st.ino && st.size > hit.offset && probeMatches(path, hit)) {
    const added = readRange(path, hit.offset, st.size);
    // Complete lines only: the append path has no remainder rule, on purpose. A whole-file read parses a complete last
    // line that has no newline yet (parseRange: a one-line file is not empty); an append waits for the newline, because a
    // line is appended in pieces by every runtime and a piece that happens to parse (a short value) would be parsed twice.
    // The cost is one beat's delay on the last line of a growing log, never a missed or doubled item.
    const { lines, end } = completeLines(added);
    drop(slot);
    let entry: Entry;
    if (!end) {
      // Grew by part of a line only: nothing new to parse yet. Remember the size so the next request
      // is a hit until the line's newline lands (then the probe still matches and this reads it whole).
      entry = { ...hit, size: st.size, mtimeMs: st.mtimeMs };
    } else {
      const got = parser.append(lines, hit.state as S);
      stampLines(got.items, lines, hit.offset);
      const raw = hit.raw.concat(got.items);
      const items = parser.finish(raw, got.state, hit.start === 0);
      parses += 1;
      appends += 1;
      entry = {
        items, memo: new Map(), raw, state: got.state, start: hit.start,
        ino: st.ino, size: st.size, mtimeMs: st.mtimeMs,
        offset: hit.offset + end, probe: probeOf(hit.probe, added.subarray(0, end)),
      };
    }
    if (covers(entry, want)) {
      if (cost(entry) <= LOG_CACHE_MAX_BYTES) keep(slot, entry);
      return entry;
    }
    // Grown, but the caller wants further back than this entry reaches: widen below.
  }
  // A tail read: from the line start at or under size - want, moved back while the line before shares the first
  // line's uuid (one Claude reply's lines never straddle a chunk edge, so parts and refs equal the whole-file build's).
  const start = Number.isFinite(want) && want < st.size ? chunkStartAt(path, st.size - want, st.size, sidechainOf(parser)) : 0;
  // A widening of the entry this file has (same bytes, a start above the entry's): the lines between are parsed on
  // their own and put above the entry's raw items, when the grammar says the join is exact (clean). Otherwise, below:
  // the range is read whole again.
  const had = entries.get(slot);
  if (had && had.ino === st.ino && had.size === st.size && had.mtimeMs === st.mtimeMs && start < had.start) {
    const stitched = prefixed(path, parser, had, start);
    if (stitched) {
      drop(slot);
      keep(slot, stitched);
      return stitched;
    }
  }
  drop(slot);
  if (start > 0) {
    const entry = parseRange(path, st, start, parser);
    if (entry) {
      keep(slot, entry);
      return entry;
    }
    // the grammar cannot be read from a chunk: the whole file, once
  }
  const entry = parseRange(path, st, 0, parser)!;
  keep(slot, entry);
  return entry;
}

/** The items of [lo, hi) of the file, rounded out to whole lines (lo down to a line start, then back while the line
 *  before shares the first line's uuid; hi up to the end of the line holding hi - 1, capped at the file's size). Not
 *  kept: a cursor page is read once, scrolling back. `end` is where the read stopped. A grammar that cannot be read
 *  from a chunk is the whole file (cached), as for a tail read. Throws when the file cannot be read. */
export function logRange<S>(path: string, parser: LogParser<S>, lo: number, hi: number): CachedLog & { end: number; eof: boolean } {
  const st = statSync(path);
  const slot = `${parser.key}\n${path}`;
  const start = lo <= 0 ? 0 : chunkStartAt(path, Math.min(lo, st.size), st.size, sidechainOf(parser));
  const end = hi >= st.size ? st.size : lineEndAt(path, Math.max(hi, start), st.size);
  if (start === 0 && end === st.size) { forget(slot); return { ...cachedLog(path, parser), end: st.size, eof: true }; }
  // The window read before, widened: only the lines outside it are parsed (above it on their own, when the join is
  // exact; below it through the grammar's state at its end).
  const had = ranges.get(slot);
  if (had && had.ino === st.ino && had.size === st.size && had.mtimeMs === st.mtimeMs && had.state !== null
      && start <= had.end && end >= had.start) {
    const grown = extended(path, parser, had, Math.min(start, had.start), Math.max(end, had.end), st.size);
    if (grown) return remember(slot, grown);
  }
  const lines = rangeLines(path, start, end);
  const got = parser.full(lines);
  if (got.state === null) { forget(slot); return { ...cachedLog(path, parser), end: st.size, eof: true }; }
  stampLines(got.items, lines, start);
  const items = parser.finish(got.items, got.state, start === 0);
  parses += 1;
  tailParses += 1;
  return remember(slot, { items, memo: new Map(), start, end, eof: end >= st.size, raw: got.items, state: got.state,
    ino: st.ino, size: st.size, mtimeMs: st.mtimeMs });
}

/** The complete lines of [start, end), plus a complete last line of the file that has no newline yet. */
function rangeLines(path: string, start: number, end: number): string[] {
  const buf = readRange(path, start, end);
  const { lines, end: lineEnd } = completeLines(buf);
  if (lineEnd < buf.length) {   // the last line of the file, complete but without its newline yet
    const rest = buf.subarray(lineEnd).toString('utf-8');
    if (isWholeJsonLine(rest)) lines.push(rest);
  }
  return lines;
}

/** `had` grown to [start, end]: the lines above it parsed on their own (prefixItems), the lines below it appended
 *  through its state. Null when the join above is not exact (the caller reads the range whole). */
function extended<S>(path: string, parser: LogParser<S>, had: RangeEntry, start: number, end: number, size: number):
    (RangeEntry & { eof: boolean }) | null {
  let raw = had.raw;
  let state = had.state as S;
  if (start < had.start) {
    const above = prefixItems(path, parser, start, had.start);
    if (!above) return null;
    raw = above.concat(raw);
  }
  if (end > had.end) {
    const lines = rangeLines(path, had.end, end);
    const got = parser.append(lines, state);
    stampLines(got.items, lines, had.end);
    raw = raw.concat(got.items);
    state = got.state;
  }
  if (raw === had.raw) return { ...had, eof: had.end >= size };
  const items = parser.finish(raw, state, start === 0);
  parses += 1;
  tailParses += 1;
  return { ...had, items, memo: new Map(), start, end, raw, state, eof: end >= size };
}

/** Keep the window as the slot's one memo, most recently used last; drop it if it is over the per-window cap, and
 *  the least recently used memos while the count or the bytes together are over their caps. */
function remember(slot: string, r: RangeEntry & { eof: boolean }): RangeEntry & { eof: boolean } {
  forget(slot);
  if (r.end - r.start <= RANGE_MEMO_MAX_BYTES) {
    ranges.set(slot, r);
    rangeBytes += r.end - r.start;
    for (const [k] of ranges) {
      if (rangeBytes <= RANGE_MEMO_MAX_TOTAL_BYTES && ranges.size <= RANGE_MEMO_MAX_FILES) break;
      if (k !== slot) forget(k);
    }
  }
  return r;
}

function forget(slot: string): void {
  const had = ranges.get(slot);
  if (!had) return;
  ranges.delete(slot);
  rangeBytes -= had.end - had.start;
}

/** The start of a chunk that holds byte `pos`: the start of its line, moved back while the line before belongs to
 *  the same record group (sameRecord: one Claude reply's lines never straddle a chunk edge, by uuid or message id). */
/** Whether a parser reads a subagent's own log (its sidechain rows kept: chat-transcript.ts logParser's key). */
const sidechainOf = (parser: { key: string }): boolean => parser.key.endsWith(':sidechain');

function chunkStartAt(path: string, pos: number, size: number, sidechain = false): number {
  let start = lineStartAt(path, pos);
  let k: ReturnType<typeof keysOfLineAt> = null;
  if (start > 0) {
    k = keysOfLineAt(path, start, size);
    for (let guard = 0; k && start > 0; guard++) {
      if (guard >= BACK_SCAN_MAX) return 0;   // a record group longer than the scan: the whole file, never a start inside it
      const prev = lineStartAt(path, start - 1);
      const pk = keysOfLineAt(path, prev, size);
      if (!sameRecord(k, pk)) break;
      start = prev;
      k = pk;
    }
  }
  return start > 0 ? replyStart(path, start, size, k, sidechain) : start;
}

/** A chunk start never falls between two blocks of one reply that other lines split: parallel tool calls are one reply
 *  (one message.id) written as tool_use, its result, an attachment, the next tool_use. The reply's usage goes on its
 *  first drawn item (chat-transcript.ts parseClaudeLines), so a chunk that started after that block would put it on a
 *  later one. `start` (a record group's first line) moves back to the reply's first block when the nearest reply above
 *  it goes on below it. Measured on real logs: 3 to 5 lines split a reply's blocks. Past a scan's guard the start stays
 *  (see below). `sidechain`: the log is a subagent's own, read with its sidechain rows kept. */
function replyStart(path: string, start: number, size: number,
                    atStart: { mid: string | null; assistant: boolean; sidechain: boolean; next: number } | null,
                    sidechain: boolean): number {
  // The nearest reply line above, over the lines that are not one (results, attachments, user turns, and the sidechain
  // rows of a seat's own log; in a subagent's log, read with them kept, they are its replies). None within the scan: the
  // start stays. A tradeoff, not a proof: a reply's blocks measured 3 to 5 lines apart, so a run longer than the scan is
  // taken as no reply's middle (a reply split wider than that still gets a start inside it, never a whole-file read).
  const replyMid = (k: { mid: string | null; assistant: boolean; sidechain: boolean } | null): string | null =>
    (k?.assistant && k.sidechain === sidechain ? k.mid : null);
  let at = start;
  let mid: string | null = null;
  for (let guard = 0; !mid; guard++) {
    if (at <= 0 || guard >= BACK_SCAN_MAX) return start;
    at = lineStartAt(path, at - 1);
    mid = replyMid(keysOfLineAt(path, at, size));
  }
  // Does that reply go on at or below the start? Its next assistant line says (usually the start line or the next).
  let off = start;
  let goesOn = false;
  for (let guard = 0; off < size; guard++) {
    if (guard >= BACK_SCAN_MAX) break;        // no reply line within the scan: that reply has ended
    const k = guard === 0 && atStart ? atStart : keysOfLineAt(path, off, size);
    const m = replyMid(k);
    if (m) { goesOn = m === mid; break; }
    off = k ? k.next : lineEndAt(path, off, size);
  }
  if (!goesOn) return start;
  // It straddles the start: back to its first block, over its own blocks and the lines between them (the guard counts
  // the lines since the last block found: a reply of many parallel calls spans more than the guard; past it, the same
  // tradeoff as above).
  let first = at;
  for (let guard = 0; at > 0 && guard < BACK_SCAN_MAX; guard++) {
    at = lineStartAt(path, at - 1);
    const m = replyMid(keysOfLineAt(path, at, size));
    if (m === mid) { first = at; guard = -1; }
    else if (m) break;
  }
  return first;
}
/** How many lines back a chunk start is moved over one record group before the whole file is read instead. */
const BACK_SCAN_MAX = 64;

/** The lines of [from, to) parsed on their own, to go above an entry's raw items that start at `to` (a line start):
 *  their items (offsets stamped from `from`), or null when the grammar cannot be read from them or carries something
 *  across `to` (then the join is not exact and the caller re-parses the range whole). */
function prefixItems<S>(path: string, parser: LogParser<S>, from: number, to: number): any[] | null {
  if (!parser.clean) return null;
  const buf = readRange(path, from, to);
  const { lines, end } = completeLines(buf);
  if (end !== buf.length) return null;         // `to` is a line start: the prefix is whole lines
  const got = parser.full(lines);
  if (got.state === null || !parser.clean(got.state)) return null;
  stampLines(got.items, lines, from);
  return got.items;
}

/** A tail entry widened to `start` by parsing only [start, had.start): the entry over the wider range, or null. */
function prefixed<S>(path: string, parser: LogParser<S>, had: Entry, start: number): Entry | null {
  if (had.state === null) return null;
  const above = prefixItems(path, parser, start, had.start);
  if (!above) return null;
  const raw = above.concat(had.raw);
  const items = parser.finish(raw, had.state as S, start === 0);
  parses += 1;
  tailParses += 1;
  return { ...had, items, memo: new Map(), raw, start };
}

/** One past the newline that ends the line holding byte `pos`, or `size` when that line has none yet. */
function lineEndAt(path: string, pos: number, size: number): number {
  let lo = pos;
  while (lo < size) {
    const block = readRange(path, lo, Math.min(size, lo + SCAN_BLOCK));
    if (!block.length) break;
    const nl = block.indexOf(0x0a);
    if (nl >= 0) return lo + nl + 1;
    lo += block.length;
  }
  return size;
}

/** The entry for [start, size) of the file. For start > 0 (a tail read) a grammar that hands back no resumable state is
 *  not readable from a chunk: null, so the caller reads the whole file. */
function parseRange<S>(path: string, st: { ino: number; size: number; mtimeMs: number }, start: number, parser: LogParser<S>): Entry | null {
  const buf = readRange(path, start, st.size);
  const { lines, end: lineEnd } = completeLines(buf);
  // The remainder after the last newline: a line still being written (fails JSON.parse: left for the append that lands
  // its newline, as before) or a COMPLETE line whose newline is not written yet (parses: a JSONL line is one JSON value,
  // and a truncated one is never valid JSON). The old split-on-newline read parsed a complete last line; so does this,
  // and the parsed end then moves past it so the append never reads it again (a one-line codex file is not empty; a
  // one-line Claude file is not parsed twice).
  let end = lineEnd;
  if (lineEnd < buf.length) {
    const rest = buf.subarray(lineEnd).toString('utf-8');
    if (isWholeJsonLine(rest)) { lines.push(rest); end = buf.length; }
  }
  const got = parser.full(lines);
  if (start > 0 && got.state === null) return null;
  stampLines(got.items, lines, start);
  const items = parser.finish(got.items, got.state, start === 0);
  parses += 1;
  if (start === 0) fullParses += 1; else tailParses += 1;
  return {
    items, memo: new Map(), raw: got.items, state: got.state, start,
    ino: st.ino, size: st.size, mtimeMs: st.mtimeMs,
    offset: start + end, probe: probeOf(Buffer.alloc(0), buf.subarray(0, end)),
  };
}
