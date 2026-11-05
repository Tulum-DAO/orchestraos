/**
 * Parsed conversation logs, kept between requests.
 *
 * The stitched chat reads every generation of a lineage, and a page further back must not parse
 * every file again: a retired generation's log never changes, and the current one only grows. An
 * entry is reused while the file's size and mtime are the ones it was parsed at; any change (an
 * append above all) parses it again. Bounded by total source bytes and by count, least recently
 * used out first; a file bigger than the whole budget is parsed every time and never kept.
 *
 * Callers must not mutate the items they get back: they are shared with the next request.
 */
import { readFileSync, statSync } from 'fs';

export const LOG_CACHE_MAX_BYTES = 64 * 1024 * 1024;   // source bytes; parsed items take several times that
export const LOG_CACHE_MAX_FILES = 32;

export interface CachedLog {
  readonly items: readonly any[];
  /** Derived views of `items` (a filtered list, an index), dropped with the entry. */
  readonly memo: Map<string, unknown>;
}

interface Entry extends CachedLog { size: number; mtimeMs: number }

const entries = new Map<string, Entry>();   // insertion order = least recently used first
let bytes = 0;
let parses = 0;

/** How many files were parsed (tests: a page that hits the cache parses nothing). */
export function logCacheParses(): number { return parses; }

export function clearLogCache(): void { entries.clear(); bytes = 0; }

function drop(path: string): void {
  const e = entries.get(path);
  if (!e) return;
  entries.delete(path);
  bytes -= e.size;
}

/** The parsed items of `path`, from the cache while the file is unchanged. Throws when the file
 *  cannot be read (the caller decides what a missing log means). */
export function cachedLog(path: string, parse: (lines: string[]) => any[]): CachedLog {
  // Stat BEFORE reading: if the file grows in between, the entry records the older size and the
  // next request parses again. The other order could keep a short parse under the longer size.
  const st = statSync(path);
  const hit = entries.get(path);
  if (hit && hit.size === st.size && hit.mtimeMs === st.mtimeMs) {
    entries.delete(path);
    entries.set(path, hit);
    return hit;
  }
  drop(path);
  const items = parse(readFileSync(path, 'utf-8').split('\n'));
  parses += 1;
  const entry: Entry = { items, memo: new Map(), size: st.size, mtimeMs: st.mtimeMs };
  if (st.size > LOG_CACHE_MAX_BYTES) return entry;
  entries.set(path, entry);
  bytes += st.size;
  for (const [p] of entries) {
    if (bytes <= LOG_CACHE_MAX_BYTES && entries.size <= LOG_CACHE_MAX_FILES) break;
    if (p !== path) drop(p);
  }
  return entry;
}
