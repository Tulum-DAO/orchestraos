/**
 * The command palette's decision logic, kept out of the component so the parts that are
 * easy to get wrong are testable: WHEN it may open, and WHAT it offers.
 *
 * It NAVIGATES ONLY. No palette entry may spawn, kill, inject or message, because a palette
 * is a place people type fast without reading, and the one thing it must never contain is a
 * destructive verb.
 */

export type PaletteKind = 'agent' | 'page' | 'recent';

export interface PaletteEntry {
  kind: PaletteKind;
  id: string;
  label: string;
  /** Where selecting it goes. The only effect an entry is allowed to have. */
  to: string;
  sublabel?: string;
}

/* ─── when the hotkey may fire ─────────────────────────────────────────── */

export interface HotkeyContext {
  key: string;
  metaKey?: boolean;
  ctrlKey?: boolean;
  /** The element the keystroke landed on. */
  target?: { tagName?: string; isContentEditable?: boolean; closest?: (sel: string) => unknown };
  /** A modal dialog is open. Two focus traps fighting is worse than no palette. */
  modalOpen?: boolean;
}

/**
 * Should this keystroke open the palette?
 *
 * Refusals, each for a reason rather than for tidiness:
 *  - NOT the Cmd/Ctrl-K combination. No bare-letter shortcut exists, so nothing can fire
 *    while somebody types a message.
 *  - A TERMINAL has focus. WebTerminal already preventDefaults and stopPropagations
 *    keystrokes: it is a TEXT SINK, and a terminal that silently loses a keystroke to a UI
 *    overlay is a worse bug than having no palette. The terminal keeps every key it is given.
 *  - A MODAL is open. It owns the focus trap; a palette over it is two traps fighting.
 *
 * A text input does NOT refuse it: Cmd-K with focus in the composer should still open the
 * palette. Only a BARE key in a text sink is ignored, and no bare key is bound anyway.
 */
export function shouldOpenPalette(ev: HotkeyContext): boolean {
  if (ev.modalOpen) return false;
  const isK = (ev.key || '').toLowerCase() === 'k';
  const mod = !!ev.metaKey || !!ev.ctrlKey;
  if (!isK || !mod) return false;
  // A terminal refuses even the modified combination: its keys are its own.
  if (ev.target?.closest && ev.target.closest('[data-terminal]')) return false;
  // NOTE there is deliberately NO text-sink check here. An earlier version had one and it was
  // UNREACHABLE — the `!mod` return above already covers every case it claimed to — so it
  // documented a protection it did not provide. Nothing needs it: no bare-letter shortcut is
  // bound, so a keystroke in a text input can only reach this far WITH the modifier held, and
  // Cmd-K from inside the composer is a case we deliberately want to work.
  return true;
}

/* ─── what it offers ───────────────────────────────────────────────────── */

const norm = (s: string) => s.toLowerCase().trim();

/** Rank: a prefix beats a word-start, which beats a substring. Ties keep input order. */
function score(label: string, id: string, q: string): number {
  const l = norm(label), i = norm(id), n = norm(q);
  if (!n) return 0;
  if (l.startsWith(n) || i.startsWith(n)) return 3;
  if (l.split(/[\s\-_/]+/).some((w) => w.startsWith(n))) return 2;
  if (l.includes(n) || i.includes(n)) return 1;
  return -1;
}

export function filterEntries(entries: PaletteEntry[], query: string, limit = 20): PaletteEntry[] {
  const q = norm(query);
  if (!q) return entries.slice(0, limit);
  return entries
    .map((e, idx) => ({ e, idx, s: score(e.label, e.id, q) }))
    .filter((r) => r.s > 0)
    .sort((a, b) => b.s - a.s || a.idx - b.idx)
    .slice(0, limit)
    .map((r) => r.e);
}

/* ─── recents ──────────────────────────────────────────────────────────── */

export const RECENTS_KEY = 'orchestra.palette.recents';
export const RECENTS_MAX = 8;

/**
 * Agent ids only, newest first, capped.
 *
 * NO QUERY LOG. What somebody typed into a search box is a record of what they were worried
 * about, and this is a convenience feature — it does not get to create that.
 */
export function pushRecent(ids: string[], id: string, max = RECENTS_MAX): string[] {
  if (!id) return ids;
  return [id, ...ids.filter((x) => x !== id)].slice(0, max);
}

/**
 * Drop recents that are no longer real agents.
 *
 * A retired seat must not haunt the list: offering a dead name is offering a dead end. When
 * the live list is UNKNOWN (undefined — the feed failed), the recents are returned UNCHANGED
 * rather than emptied, because "we could not ask" is not "they are all gone".
 */
export function pruneRecents(ids: string[], liveAgentIds: string[] | undefined): string[] {
  if (!liveAgentIds) return ids;
  const live = new Set(liveAgentIds);
  return ids.filter((id) => live.has(id));
}
