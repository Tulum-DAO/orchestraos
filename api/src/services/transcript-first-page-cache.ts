/**
 * The finished first page of a seat's chat, kept between requests.
 *
 * A seat switch loads the newest page of every visible seat at once. Building one page is the
 * stitched window, the queued/notice/call merges, the image stamp, JSON.stringify of up to 1 MB
 * and then gzip of that: 25-60 ms of CPU per seat on a warm server, repeated for every click
 * though nothing had changed. This table keeps the serialized body (and its gzipped form, and its
 * ETag) per (seat, limit) under a validity string the route derives from everything the page is
 * made of: the log file's identity and size and mtime, the registry's signature, the seat's queued
 * rows (services/queued-merge.ts queuedStateSig), its interrupted-turns and warm-up marks, plus
 * the size and mtime of every earlier generation's file the window read. A request whose validity
 * matches is answered from here without parsing, merging or serializing anything; any change
 * builds the page again. Bounded by count, least recently used out first.
 */
import { createHash } from 'node:crypto';
import { gzipSync } from 'node:zlib';
import { statSync } from 'node:fs';
import { COMPRESS_THRESHOLD } from '../lib/compress.js';

export const FIRST_PAGE_CACHE_MAX = 64;

export interface FirstPageEntry {
  readonly json: string;
  /** A weak validator over `json`, the shape express gives a JSON body. */
  readonly etag: string;
  /** `json` gzipped, or null when it is too small to be worth it. */
  readonly gzip: Buffer | null;
}

/** What a build hands back: the page, and the files it read on the way (an earlier generation's log
 *  the window reached into). Their size and mtime are part of the entry's validity: one of them
 *  rewritten, removed or appearing again builds the page anew. */
export interface FirstPageBuild {
  body: unknown;
  deps?: readonly string[];
  /** Questions asked again on every hit (where is the predecessor's file that was missing?): the
   *  page is kept only while every answer is what it was at build time. */
  probes?: readonly (() => string)[];
}

interface Entry extends FirstPageEntry { validity: string; deps: readonly string[]; probes: readonly (() => string)[]; depsSig: string }

/** inode too: an atomic replace (write beside, rename over) keeps size and mtime and moves the inode. */
function depsSig(paths: readonly string[], probes: readonly (() => string)[]): string {
  const files = paths.map((p) => { try { const st = statSync(p); return `${st.ino}:${st.size}:${st.mtimeMs}`; } catch { return ''; } });
  const asked = probes.map((q) => { try { return q(); } catch { return ''; } });
  return [...files, ...asked].join('|');
}

const entries = new Map<string, Entry>();   // insertion order = least recently used first
let hits = 0;
let misses = 0;

export function firstPageCacheStats(): { size: number; hits: number; misses: number } {
  return { size: entries.size, hits, misses };
}

export function clearFirstPageCache(): void { entries.clear(); hits = 0; misses = 0; }

function weakEtag(json: string): string {
  const hash = createHash('sha1').update(json, 'utf8').digest('base64url').slice(0, 27);
  return `W/"${Buffer.byteLength(json).toString(16)}-${hash}"`;
}

/** The page for (agentId, limit): the kept one while `validity` is the string it was built under
 *  and the files it read are unchanged, else `build()` serialized and kept. */
export function firstPage(agentId: string, limit: number, validity: string, build: () => FirstPageBuild): FirstPageEntry {
  const key = `${agentId}|${limit}`;
  const hit = entries.get(key);
  if (hit && hit.validity === validity && depsSig(hit.deps, hit.probes) === hit.depsSig) {
    entries.delete(key);
    entries.set(key, hit);
    hits += 1;
    return hit;
  }
  misses += 1;
  const built = build();
  const deps = [...(built.deps ?? [])];
  const probes = [...(built.probes ?? [])];
  const json = JSON.stringify(built.body);
  const entry: Entry = {
    json,
    etag: weakEtag(json),
    gzip: Buffer.byteLength(json) >= COMPRESS_THRESHOLD ? gzipSync(json) : null,
    validity, deps, probes, depsSig: depsSig(deps, probes),
  };
  entries.delete(key);
  entries.set(key, entry);
  while (entries.size > FIRST_PAGE_CACHE_MAX) {
    const oldest = entries.keys().next().value as string;
    entries.delete(oldest);
  }
  return entry;
}
