/**
 * One agent's generation history — the shape `GET /api/agents/:id/generations` returns, and
 * the small decisions about how a row reads. Logic lives here, not in the component, because
 * `npm test` covers src/lib only (no DOM runner); the component is a thin render over this.
 */

/** One past or current generation of a lineage. Mirrors the API's GenerationRow. */
export interface GenerationRow {
  generation: number;
  model: string | null;
  spawned_at: string | null;
  promoted_at: string | null;
  retired_at: string | null;
  note: string | null;
  /** The lineage's canonical head. Comes from the `canonical` table, NOT from the highest
   *  generation number — numbers reset, so gm's live head is generation 2 while its history
   *  runs to 87. Never re-derive this client-side. */
  current?: boolean;
  /** A pre-allocated slot: minted for an incoming green and never promoted, so it names a
   *  seat that never ran. Server-stamped; not counted in `generations_total`. */
  pending?: boolean;
}

/**
 * Whether an agent has a history worth showing. Keyed on the `generations_total` the agents
 * list already carries, so asking costs no request.
 *
 * `undefined` (the field is absent) is the "no identity DB, or no history" signal — the API
 * omits it rather than sending 0 — and it means show nothing, never an error.
 */
export function hasGenerationHistory(total: number | undefined): boolean {
  return typeof total === 'number' && total >= 2;
}

/**
 * A timestamp for display, or an em dash. Some rows hold a non-date marker in these columns,
 * so `new Date(v).toLocaleString()` on the raw value prints "Invalid Date" into the UI.
 */
export function formatGenerationWhen(v: string | null | undefined): string {
  if (!v) return '—';
  const t = Date.parse(v);
  return Number.isNaN(t) ? '—' : new Date(t).toLocaleString();
}

/**
 * How a row describes itself in time, saying only what actually happened to it: retired rows
 * by when they ENDED, promoted rows by when they started, and a spawned-but-never-promoted
 * row as `spawned` — never as `promoted`. Many rows carry no `promoted_at` at all, so the old
 * fallback silently promoted them in the UI.
 */
export function describeGenerationTiming(g: GenerationRow): string {
  if (g.retired_at) return `retired ${formatGenerationWhen(g.retired_at)}`;
  if (g.promoted_at) return `promoted ${formatGenerationWhen(g.promoted_at)}`;
  // NOT "promoted <spawned_at>". A row that was spawned and never promoted is a seat that
  // never ran, and calling it promoted is how gm's never-used slot read as a live gen 3.
  if (g.spawned_at) return `spawned ${formatGenerationWhen(g.spawned_at)}`;
  return 'never ran';
}

/** A stable React key. `generation` alone is not unique: numbers reset within a lineage. */
export function generationKey(g: GenerationRow): string {
  return `${g.generation}-${g.promoted_at ?? g.spawned_at ?? g.retired_at ?? ''}`;
}
