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
 * Callers must not mutate the items they get back: they are shared with the next request.
 */
import { closeSync, openSync, readFileSync, readSync, statSync } from 'fs';

export const LOG_CACHE_MAX_BYTES = 64 * 1024 * 1024;   // source bytes; parsed items take several times that
export const LOG_CACHE_MAX_FILES = 32;
/** Bytes just under the parsed end that an append must reproduce: a rewrite in place that keeps the
 *  size growing is still caught, because the old end's bytes changed. */
export const APPEND_PROBE_BYTES = 64;

/** A log grammar the cache can drive: `full` parses whole lines and returns the state an `append`
 *  continues from (null when the grammar cannot resume: the cache then re-parses in full on every
 *  change); `finish` runs the whole-array passes over the raw items, every time. */
export interface LogParser<S = unknown> {
  full(lines: string[]): { items: any[]; state: S | null };
  append(lines: string[], state: S): { items: any[]; state: S };
  finish(raw: any[], state: S | null): any[];
}

export interface CachedLog {
  readonly items: readonly any[];
  /** Derived views of `items` (a filtered list, an index), dropped with the entry. */
  readonly memo: Map<string, unknown>;
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

const entries = new Map<string, Entry>();   // insertion order = least recently used first
let bytes = 0;
let parses = 0;        // every read that produced items (full or append)
let fullParses = 0;
let appends = 0;

/** How many reads produced items (tests: a page that hits the cache parses nothing). */
export function logCacheParses(): number { return parses; }
/** How many of those were a whole-file parse. */
export function logCacheFullParses(): number { return fullParses; }
/** How many were an append read (new bytes only). */
export function logCacheAppends(): number { return appends; }

export function clearLogCache(): void { entries.clear(); bytes = 0; }

function drop(path: string): void {
  const e = entries.get(path);
  if (!e) return;
  entries.delete(path);
  bytes -= e.size;
}

function keep(path: string, entry: Entry): void {
  entries.set(path, entry);
  bytes += entry.size;
  for (const [p] of entries) {
    if (bytes <= LOG_CACHE_MAX_BYTES && entries.size <= LOG_CACHE_MAX_FILES) break;
    if (p !== path) drop(p);
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
    return got === buf.length ? buf : buf.subarray(0, got);
  } finally {
    closeSync(fd);
  }
}

/** The bytes just under the parsed end are still what was parsed: this is the same file, grown. */
function probeMatches(path: string, hit: Entry): boolean {
  if (!hit.probe.length) return hit.offset === 0;
  try {
    return readRange(path, hit.offset - hit.probe.length, hit.offset).equals(hit.probe);
  } catch {
    return false;
  }
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
  // Stat BEFORE reading: if the file grows in between, the entry records the older size and the
  // next request reads again. The other order could keep a short read under the longer size.
  const st = statSync(path);
  const hit = entries.get(path);
  if (hit && hit.ino === st.ino && hit.size === st.size && hit.mtimeMs === st.mtimeMs) {   // ino: an atomic replace keeps size and mtime
    entries.delete(path);
    entries.set(path, hit);
    return hit;
  }
  if (hit && hit.state !== null && hit.ino === st.ino && st.size > hit.offset && probeMatches(path, hit)) {
    const added = readRange(path, hit.offset, st.size);
    const { lines, end } = completeLines(added);
    drop(path);
    let entry: Entry;
    if (!end) {
      // Grew by part of a line only: nothing new to parse yet. Remember the size so the next request
      // is a hit until the line's newline lands (then the probe still matches and this reads it whole).
      entry = { ...hit, size: st.size, mtimeMs: st.mtimeMs };
    } else {
      const got = parser.append(lines, hit.state as S);
      const raw = hit.raw.concat(got.items);
      const items = parser.finish(raw, got.state);
      parses += 1;
      appends += 1;
      entry = {
        items, memo: new Map(), raw, state: got.state,
        ino: st.ino, size: st.size, mtimeMs: st.mtimeMs,
        offset: hit.offset + end, probe: probeOf(hit.probe, added.subarray(0, end)),
      };
    }
    if (st.size <= LOG_CACHE_MAX_BYTES) keep(path, entry);
    return entry;
  }
  drop(path);
  const buf = readFileSync(path);
  const { lines, end } = completeLines(buf);
  const got = parser.full(lines);
  const items = parser.finish(got.items, got.state);
  parses += 1;
  fullParses += 1;
  const entry: Entry = {
    items, memo: new Map(), raw: got.items, state: got.state,
    ino: st.ino, size: st.size, mtimeMs: st.mtimeMs,
    offset: end, probe: probeOf(Buffer.alloc(0), buf.subarray(0, end)),
  };
  if (st.size > LOG_CACHE_MAX_BYTES) return entry;
  keep(path, entry);
  return entry;
}
