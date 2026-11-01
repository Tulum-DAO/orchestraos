/**
 * paging.ts — a thread read in pages going back (scrolling up loads what came before), joined with its live window
 * (the newest items, which keep arriving and replacing themselves) into one list with no item twice and no gap.
 * Pure; the hook that drives it is lib/threadKit/useOlderPages.ts.
 *
 *   joinTail  the list so far, with the live window laid over its end: the live window overlaps the end of what is
 *             already known (it moves down a few items at a time), and from that overlap on, the live copy wins (an
 *             item there may have changed: a reply grew, a result arrived). No overlap at all means a different
 *             conversation: the live window alone.
 *   joinHead  an older page added above: the page ends where the list begins, or just before; any item the list
 *             already begins with (the top of the live window can start partway through one log line's items, which
 *             the older page then holds whole) is the page's copy, once.
 *
 * Items are matched by an identity that does not change as they do (an agent transcript item: its uuid, its kind,
 * its tool call id).
 *
 *   joinBySeq a thread whose stored turns are numbered (Arturo's: each turn's seq) and whose newest turns may be the
 *             page's own, not stored yet (a reply being streamed, a just-sent message): the older pages, then the
 *             live window from its first numbered turn on. Seqs need not run on by one (a turn the client never
 *             draws, the page's hidden opener, leaves a hole), so meeting is not checked by number: the window is
 *             re-read at least as far back as it reached before (the newest page of a thread outgrows it only after
 *             a hundred turns without a reload).
 */

export type KeyOf<T> = (item: T) => string;

/** Where the live window starts within the list: the last position from which the list's items run on as the live
 *  window's do (as far as both go). -1 when they do not meet. */
export function findOverlap<T>(list: T[], live: T[], keyOf: KeyOf<T>): number {
  if (!live.length) return -1;
  const first = keyOf(live[0]);
  for (let j = list.length - 1; j >= 0; j--) {
    if (keyOf(list[j]) !== first) continue;
    const n = Math.min(list.length - j, live.length);
    let ok = true;
    for (let t = 1; t < n && ok; t++) ok = keyOf(list[j + t]) === keyOf(live[t]);
    if (ok) return j;
  }
  return -1;
}

/** The list with the live window laid over its end; `reset` when they did not meet (the live window alone). */
export function joinTail<T>(list: T[], live: T[], keyOf: KeyOf<T>): { items: T[]; reset: boolean } {
  if (!list.length) return { items: live, reset: false };
  if (!live.length) return { items: list, reset: false };
  const j = findOverlap(list, live, keyOf);
  if (j >= 0) return { items: [...list.slice(0, j), ...live], reset: false };
  // The live window may also run on from the list's very end (nothing shared, nothing missing): one step past it.
  return { items: live, reset: true };
}

/** An older page added above the list, every item once. */
export function joinHead<T>(page: T[], list: T[], keyOf: KeyOf<T>): T[] {
  if (!page.length) return list;
  const inPage = new Set(page.map(keyOf));
  let i = 0;
  while (i < list.length && inPage.has(keyOf(list[i]))) i++;
  return [...page, ...list.slice(i)];
}

type ItemFields = { uuid?: string; kind?: string; id?: string; tool_use_id?: string; key?: string };

/** An agent transcript item's identity: its log line (uuid), its kind, and its tool call id. */
export function transcriptKey(item: object): string {
  const it = item as ItemFields;
  return `${it.uuid ?? it.key ?? ''}|${it.kind ?? ''}|${it.id ?? it.tool_use_id ?? ''}`;
}

/** The cursor for the first page older than a live window: the first item that starts a log line of its own. The
 *  window's top can start partway through one line's items, and a page read from an item's uuid holds everything
 *  older than that item's line, so it brings the cut line back whole (joinHead keeps its copy once). Null when the
 *  window has no item a cursor can name. */
export function firstCursor(items: readonly object[]): string | null {
  const uuids = items.map((it) => (it as ItemFields).uuid);
  const firstUuid = uuids.find(Boolean);
  if (!firstUuid) return null;
  return uuids.find((u) => u && u !== firstUuid) ?? firstUuid;
}

/** A numbered turn's seq; undefined for one not stored yet. */
export type SeqOf<T> = (item: T) => number | undefined;

/** The older pages (`list`, which may still hold an earlier copy of the window) with the live window laid over its
 *  end, by seq: everything numbered below the window's first seq, then the window. `reset` when the window has no
 *  stored turn at all (a new thread): the window alone. */
export function joinBySeq<T>(list: T[], live: T[], seqOf: SeqOf<T>): { items: T[]; reset: boolean } {
  if (!list.length) return { items: live, reset: false };
  const first = live.map(seqOf).find((n) => n !== undefined);
  // A window with nothing stored (a new thread, or one emptied): there is nothing older to join.
  if (first === undefined) return { items: live, reset: true };
  return { items: [...list.filter((t) => { const n = seqOf(t); return n !== undefined && n < first; }), ...live], reset: false };
}

/** The cursor for the first page older than a numbered thread's live window: its first numbered turn. */
export function firstSeqCursor<T>(seqOf: SeqOf<T>): (items: T[]) => string | null {
  return (items) => { const n = items.map(seqOf).find((x) => x !== undefined); return n === undefined ? null : String(n); };
}
