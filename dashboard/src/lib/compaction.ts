/**
 * compaction.ts — the chat's notice that the agent's conversation is being compacted ("Compacting the chat…")
 * and then has been ("The chat was compacted"). The server keeps each notice in its database and sends it two ways: in the
 * transcript's items (kind "compaction", placed by time, also in older pages) and live on the transcript stream
 * (`event: compaction`) when it starts and when it finishes. Both carry the same object; one notice keeps one id and
 * changes in place. A seat whose "Show compaction messages" is off gets none at all.
 *
 * The notice is the chat's only sign of the switch, and never part of any model's context (it is not in a
 * transcript file, only in the database).
 */
import type { ChatItem } from './transcript.gen';

/** The transcript item, as the shared contract (section 3) defines it. Local until the regenerated contract types
 *  carry the kind; then this becomes the generated type. */
export interface CompactionItem {
  kind: 'compaction';
  id: string;
  state: 'compacting' | 'compacted' | 'failed';
  ts?: string;
  finished_at?: string | null;
  /** What started it: the agent's own Auto-compact, or the person typing /compact. */
  trigger?: 'auto' | 'manual';
  before_tokens?: number | null;
  after_tokens?: number | null;
}

/** A transcript item, including the kind the generated types do not name yet. */
export type TranscriptItem = ChatItem | CompactionItem;

const STATES = new Set(['compacting', 'compacted', 'failed']);

export function isCompaction(it: unknown): it is CompactionItem {
  const o = it as Partial<CompactionItem> | null;
  return !!o && o.kind === 'compaction' && typeof o.id === 'string' && !!o.id && STATES.has(o.state as string);
}

/** What the line says; null draws nothing. A /compact the person typed that could not be done says so in one short
 *  line; an automatic attempt that failed draws nothing (the old conversation simply goes on, and the server never
 *  sends one; this holds if it ever does). */
export function compactionText(state: CompactionItem['state'], trigger?: CompactionItem['trigger']): string | null {
  if (state === 'compacting') return 'Compacting the chat…';
  if (state === 'compacted') return 'The chat was compacted';
  return trigger === 'manual' ? "Couldn't compact the chat right now." : null;
}

// A notice only moves forward: a frame that arrives late (the stream's "compacting" after the page already read
// "compacted") never takes it back.
const RANK: Record<CompactionItem['state'], number> = { compacting: 0, compacted: 1, failed: 1 };

/** The newer of two copies of one notice. "Compacted" arrives when the new conversation takes over, and the same id
 *  again once its first turn has counted its tokens: a copy without a number never wipes one already known. */
export function newerNotice(a: CompactionItem, b: CompactionItem): CompactionItem {
  if (RANK[b.state] < RANK[a.state]) return a;
  return { ...a, ...b, before_tokens: b.before_tokens ?? a.before_tokens, after_tokens: b.after_tokens ?? a.after_tokens,
           finished_at: b.finished_at ?? a.finished_at, ...(b.trigger ?? a.trigger ? { trigger: b.trigger ?? a.trigger } : {}) };
}

/** The notices the stream has sent so far, by id, with this one: kept apart from the items, because the stream's
 *  snapshots and deltas carry the transcript's lines only and would otherwise drop a notice the moment the next one
 *  arrives. */
export function addNotice(notices: Record<string, CompactionItem>, n: unknown): Record<string, CompactionItem> {
  if (!isCompaction(n)) return notices;
  const had = notices[n.id];
  return { ...notices, [n.id]: had ? newerNotice(had, n) : n };
}

const timeOf = (it: { ts?: string }) => { const t = it.ts ? Date.parse(it.ts) : NaN; return Number.isNaN(t) ? null : t; };

/** The transcript's items with the stream's notices laid in: a notice the items already hold is updated where it
 *  stands (the newer copy); one they do not is placed by its time, after the last item at or before it (at the end
 *  when it has none, or is newer than everything). A notice older than the items' first line is left out: it belongs
 *  to an older page, which brings it in its place (laid in at the top instead, it would sit above lines older than
 *  itself, and the window would no longer meet the pages joined above it). The items' own order is never changed. */
export function withNotices<T extends { kind: string; ts?: string }>(items: T[], notices: Record<string, CompactionItem>): (T | CompactionItem)[] {
  const ids = Object.keys(notices);
  if (!ids.length) return items;
  const placed = new Set<string>();
  const out: (T | CompactionItem)[] = items.map((it) => {
    if (!isCompaction(it) || !notices[it.id]) return it;
    placed.add(it.id);
    return newerNotice(it, notices[it.id]);
  });
  const first = items.map(timeOf).find((t) => t !== null) ?? null;
  const rest = ids.filter((id) => !placed.has(id)).map((id) => notices[id])
    .filter((n) => { const t = timeOf(n); return first === null || t === null || t >= first; })
    .sort((a, b) => (timeOf(a) ?? Infinity) - (timeOf(b) ?? Infinity));
  for (const n of rest) {
    const t = timeOf(n);
    let at = out.length;
    if (t !== null) {
      // After the last item at or before the notice's time; items with no time stay with what precedes them.
      at = 0;
      for (let i = 0; i < out.length; i++) { const ti = timeOf(out[i]); if (ti === null ? i === at : ti <= t) at = i + 1; }
    }
    out.splice(at, 0, n);
  }
  return out;
}

/** The newest notice in a list of items (by time, else by place), or null: what "Compact now" in the slideout reads
 *  to say how the compaction it asked for went. */
export function latestNotice(items: readonly unknown[]): CompactionItem | null {
  let best: CompactionItem | null = null;
  for (const it of items) {
    if (!isCompaction(it)) continue;
    if (!best) { best = it; continue; }
    const a = timeOf(best), b = timeOf(it);
    if (a === null || b === null || b >= a) best = it;
  }
  return best;
}
