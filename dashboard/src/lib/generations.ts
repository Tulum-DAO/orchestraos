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
 * How a row describes itself in time. A retired generation is defined by when it ENDED; a
 * live one by when it started. Falls back to `spawned_at` because 360 of 950 generation rows
 * carry no `promoted_at`.
 */
export function describeGenerationTiming(g: GenerationRow): string {
  if (g.retired_at) return `retired ${formatGenerationWhen(g.retired_at)}`;
  return `promoted ${formatGenerationWhen(g.promoted_at ?? g.spawned_at)}`;
}

/** A stable React key. `generation` alone is not unique: numbers reset within a lineage. */
export function generationKey(g: GenerationRow): string {
  return `${g.generation}-${g.promoted_at ?? g.spawned_at ?? g.retired_at ?? ''}`;
}

/**
 * How many "sheets" a topology node stacks behind itself to show it is a lineage.
 *
 * Coarse on purpose. The graph answers "which lineages are deep"; the exact number is one
 * click away in the drawer, so the deepest lineage on the fleet (gm, 55) still caps at two.
 * Today that is 25 nodes at one sheet, 10 at two, and 71 drawn exactly as they are now.
 *
 * The threshold matches `hasGenerationHistory`, so a node can never stack for a lineage whose
 * drawer section would then decline to render.
 */
export function lineageDepthTier(total: number | undefined): 0 | 1 | 2 {
  if (!hasGenerationHistory(total)) return 0;
  return (total as number) >= 5 ? 2 : 1;
}

/**
 * The lineage half of a node's tooltip, or '' when there is no lineage. The stack is a shape,
 * and a shape alone must not be the only carrier of the meaning.
 */
export function describeLineage(total: number | undefined): string {
  return hasGenerationHistory(total) ? `${total} generations` : '';
}
