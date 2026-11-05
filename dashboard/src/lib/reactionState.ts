/**
 * reactionState.ts — message reactions on the client (spec docs/superpowers/specs/2026-11-02-message-reactions-design.md,
 * build step 2): one conversation's reactions by message ref, as GET /api/reactions returns them, and the pure rules the
 * UI applies to them. No DOM, no fetching (lib/reactionsApi.ts fetches, lib/useReactions.ts holds the state).
 *
 *   - One emoji per reactor per message: a change replaces the reactor's emoji, or removes it (emoji null).
 *   - A tap on the emoji the viewer already has removes it; any other emoji replaces it (the server's PUT toggles the
 *     same way, api/src/routes/reactions.ts).
 *   - A badge groups one emoji: its count, whether the viewer is among its reactors, who they are.
 *   - A user message carried into a rebuilt session keeps its uuid but changes session: when nothing is stored under
 *     the row's own ref, a reaction stored under the same `~<uuid>#<part>` of another session (a Claude uuid only, and
 *     only when that ref's own row is not on screen) is the row's (spec "carriedOver").
 */
export type Surface = 'agent-chat' | 'arturo' | 'call';
export interface Reactor { kind: 'user' | 'agent'; id: string }
export interface StoredReaction { emoji: string; reactor: Reactor; at: string }
export type ReactionMap = Readonly<Record<string, readonly StoredReaction[]>>;
/** One change, as the PUT answers it and the stream sends it. */
export interface ReactionChange { surface?: Surface; conv?: string; ref: string; reactor: Reactor; emoji: string | null; at: string }
export interface BadgeGroup { emoji: string; count: number; mine: boolean; reactors: Reactor[] }

/** The quick row (as on common chat apps' desktop). */
export const QUICK_EMOJI = ['👍', '👎', '❤️', '😂', '🎉', '😮'] as const;

export const EMPTY_REACTIONS: ReactionMap = Object.freeze({});

export function sameReactor(a: Reactor | null | undefined, b: Reactor | null | undefined): boolean {
  return !!a && !!b && a.kind === b.kind && a.id === b.id;
}

/** The map with one change applied (idempotent: the PUT's answer and the stream's echo of it land as one). */
export function applyChange(map: ReactionMap, c: ReactionChange): ReactionMap {
  const list = map[c.ref] ?? [];
  const rest = list.filter((r) => !sameReactor(r.reactor, c.reactor));
  const next = c.emoji ? [...rest, { emoji: c.emoji, reactor: c.reactor, at: c.at }] : rest;
  const out: Record<string, readonly StoredReaction[]> = { ...map };
  if (next.length) out[c.ref] = next; else delete out[c.ref];
  return out;
}

/** The map with one message's reactions put back as they were (a PUT the server refused). */
export function restoreRef(map: ReactionMap, ref: string, before: readonly StoredReaction[]): ReactionMap {
  const out: Record<string, readonly StoredReaction[]> = { ...map };
  if (before.length) out[ref] = before; else delete out[ref];
  return out;
}

/** What a tap of `emoji` leaves the viewer with: null when it was already theirs (removed), else that emoji. */
export function nextEmoji(list: readonly StoredReaction[], me: Reactor, emoji: string): string | null {
  return list.find((r) => sameReactor(r.reactor, me))?.emoji === emoji ? null : emoji;
}

/** The badges under one message: one per emoji, in the order each emoji was first given. */
export function badgeGroups(list: readonly StoredReaction[], me: Reactor | null): BadgeGroup[] {
  const ordered = list.map((r, i) => ({ r, i })).sort((a, b) => (a.r.at || '').localeCompare(b.r.at || '') || a.i - b.i);
  const groups: BadgeGroup[] = [];
  for (const { r } of ordered) {
    let g = groups.find((x) => x.emoji === r.emoji);
    if (!g) { g = { emoji: r.emoji, count: 0, mine: false, reactors: [] }; groups.push(g); }
    g.count++;
    g.reactors.push(r.reactor);
    if (sameReactor(r.reactor, me)) g.mine = true;
  }
  return groups;
}

/** A reactor as the badge names them: "You", the agent's display name, or the id. */
export function reactorName(r: Reactor, me: Reactor | null, nameOf: (r: Reactor) => string = (x) => x.id): string {
  return sameReactor(r, me) ? 'You' : nameOf(r);
}

/** "You and pm-x" / "sam, pm-x and 2 others": who gave one badge's emoji. */
export function whoReacted(g: BadgeGroup, me: Reactor | null, nameOf?: (r: Reactor) => string): string {
  const names = [...g.reactors].sort((a, b) => Number(sameReactor(b, me)) - Number(sameReactor(a, me)))
    .map((r) => reactorName(r, me, nameOf));
  if (names.length <= 1) return names[0] ?? '';
  if (names.length <= 3) return `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;
  return `${names.slice(0, 2).join(', ')} and ${names.length - 2} others`;
}

const CLAUDE_UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** `~<uuid>#<part>` of a ref (`<session>~<uuid>#<part>`), or null when it is not one. */
export function refTail(ref: string): { tail: string; uuid: string } | null {
  const t = ref.indexOf('~');
  const h = ref.lastIndexOf('#');
  if (t <= 0 || h <= t + 1) return null;
  return { tail: ref.slice(t), uuid: ref.slice(t + 1, h) };
}

/** Every ref in a map by its `~<uuid>#<part>` tail, built once per map (a thread asks once per row). */
const tailIndexes = new WeakMap<object, Map<string, string[]>>();
function tailIndex(map: ReactionMap): Map<string, string[]> {
  let idx = tailIndexes.get(map);
  if (!idx) {
    idx = new Map();
    for (const k of Object.keys(map)) {
      if (!map[k].length) continue;
      const t = refTail(k);
      if (t) idx.set(t.tail, [...(idx.get(t.tail) ?? []), k]);
    }
    tailIndexes.set(map, idx);
  }
  return idx;
}

/** The reactions a row shows and the ref a tap on it writes to: its own, else a carried-over copy's (see the header).
 *  `shown`: every ref drawn in the thread now. */
export function resolveRef(ref: string, map: ReactionMap, shown: ReadonlySet<string>): { ref: string; list: readonly StoredReaction[] } {
  const own = map[ref];
  if (own?.length) return { ref, list: own };
  const t = refTail(ref);
  if (t && CLAUDE_UUID.test(t.uuid)) {
    for (const k of tailIndex(map).get(t.tail) ?? []) {
      if (k !== ref && !shown.has(k)) return { ref: k, list: map[k] };
    }
  }
  return { ref, list: [] };
}
