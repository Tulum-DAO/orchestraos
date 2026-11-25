/**
 * Parked rotation leftovers (DEC-1790826247484632).
 *
 * Every lineage rotation leaves a per-generation row behind. PR #137 taught the fleet view to
 * exclude two kinds of ghost: a row the registry marks `retired`/`archived`, and a `-genN`
 * predecessor whose base seat is currently alive. A third kind was still counted — a leftover
 * whose status is `parked`, which neither predicate sees, so it landed in the header
 * denominator and the Down bucket.
 *
 * This module answers one question for one row and nothing else, so the rule is testable
 * rather than hoped for. GET /api/agents leaves such a row out of the list (and its counts), so one
 * seat is one row; the row stays addressable by id for its own routes and revive. See
 * .workspace/proposals/parked-rotation-leftovers-spec.md for the measurements.
 */

/** The canonical facts the rule needs, read from the `canonical` table itself. */
export interface CanonicalIndex {
  /** Every `canonical.root`. */
  roots: Set<string>;
  /** Every `canonical.tmux_session` — a root's live head, which may differ from its root name. */
  sessions: Set<string>;
  /** root -> its canonical live head session, for the roots that have one. */
  heads: Map<string, string>;
}

/**
 * Build the index from `getCanonicalAgents()`.
 *
 * Returns `null` when there is no canonical map — outside cutover, or on an absent/unreadable
 * DB. A null index means the rule DOES NOT FIRE: with no canonical table there is no condition
 * 3 and no condition 5, and degrading to "name shape plus status" would hide a row on no
 * registry evidence at all. Fail-safe direction is to show the extra rows.
 */
export function buildCanonicalIndex(
  canon: Record<string, { root: string; tmux_session: string }> | null | undefined,
): CanonicalIndex | null {
  if (!canon) return null;
  const roots = new Set<string>();
  const sessions = new Set<string>();
  const heads = new Map<string, string>();
  for (const [root, c] of Object.entries(canon)) {
    roots.add(c?.root || root);
    if (c?.tmux_session) { sessions.add(c.tmux_session); heads.set(c?.root || root, c.tmux_session); }
  }
  return { roots, sessions, heads };
}

/** `seat-g7` / `seat-gen7` -> `seat`. Null when the id carries no generation suffix. */
function baseName(id: string): string | null {
  const m = /^(.+)-g(?:en)?\d+$/.exec(id);
  return m ? m[1] : null;
}

/**
 * Does the root this row descends from have a live canonical head that is NOT this row? Then an
 * alive leftover is not "the only agent answering for its root": its successor is. Measured
 * 2026-11-25: gm-gen139, avatar-dev-gen8 and orchestra-builder-gen118 (parked, retired at a swap,
 * their panes still running for minutes after) were listed as live ids with their own context
 * percentages while gm, avatar-dev and orchestra-builder answered on their canonical heads.
 */
export function rootHasAnotherHead(id: string, base: string | null, index: CanonicalIndex): boolean {
  if (!base) return false;
  const head = index.heads.get(base);
  return !!head && head !== id;
}

/**
 * The canonical root that supersedes this row, or `null` if it is not a parked rotation
 * leftover. All five conditions must hold:
 *
 *  1. the id carries a generation suffix;
 *  2. the REGISTRY DEFINITION's status is exactly `parked` — not the post-spread, post-detector
 *     `agent.status`, which a frozen state file can overwrite and which the detector pass
 *     reclassifies to `offline`. This matches the `retired` branch of `applyIdentityPrecedence`;
 *  3. the base name is itself a canonical root — the registry fact that makes this a leftover
 *     rather than a seat that merely looks like one;
 *  4. the row is not alive, by its ALREADY-RESOLVED liveness, OR it is alive but its root has a
 *     DIFFERENT canonical live head (rootHasAnotherHead): a predecessor pane still running after the
 *     swap is not the only agent answering for its root. A fresh local tmux probe sees
 *     only VPS sessions, so it would report every Mac-hosted or heartbeat-only seat not-live
 *     and void this escape for all of them;
 *  5. the row is not itself a canonical root, and not any root's canonical live head.
 *
 * (5) is why this function exists in its current form. Without it the rule hid `gm-g3`,
 * `ios-watch-dev-g19`, `orchestra-builder-g68` and `release-readiness-g3` — canonical lineage
 * roots of their own, one carrying two generations of its own lineage. Conditions 2 and 4 gave
 * them no protection, since they are themselves parked and not live.
 *
 * (4) is the safety escape. It costs nothing today (0 of the 154 matches are live) and is what
 * keeps a resumed-but-still-`parked` seat from going invisible while it is the only thing
 * answering for its root — the af199c1 "never hide the only agent" principle.
 */
export function supersededBy(args: {
  id: string;
  defStatus: string | undefined | null;
  alive: boolean;
  index: CanonicalIndex | null;
}): string | null {
  const { id, defStatus, alive, index } = args;
  if (!index) return null;                                   // rule does not fire (see above)
  if (String(defStatus || '').toLowerCase() !== 'parked') return null;
  if (index.roots.has(id) || index.sessions.has(id)) return null;
  const base = baseName(id);
  if (!base || !index.roots.has(base)) return null;
  // (4) an alive row is protected only while nothing else answers for its root: a retired
  // generation whose pane is still winding down, with the successor already canonical, is a ghost.
  if (alive && !rootHasAnotherHead(id, base, index)) return null;
  return base;
}
