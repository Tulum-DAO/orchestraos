/**
 * Bounding the historical batch rows a transcript window may carry.
 *
 * ITS OWN MODULE, with no imports, so the test can load the REAL functions: queued-merge.ts
 * reaches the sqlite msg_store through conn() and importing it throws ConfigError without an
 * orchestra.toml. The alternative — a test that re-implements the bound — passes with the bound
 * deleted, which is the class that left a reports_to gate green earlier today.
 */
/**
 * How many historical batches a transcript window may carry when there is no turn to anchor to.
 * Not a tuning knob: it is the backstop for the case where `floorTs` cannot be derived.
 */
export const QUEUED_BATCH_MAX = 25;

/**
 * Bound the HISTORICAL batch rows to the window the caller is actually rendering.
 *
 * THE BUG THIS FIXES (quest-orchestra, measured 2026-10-07): `limit` was applied to the JSONL
 * turns and the synthetics were merged in AFTERWARDS, so it capped the conversation and not the
 * transcript. Measured on orchestra-builder at limit=80: 371 render_items = 329 queued_batch rows
 * reaching back three weeks, across every generation of the seat, against 42 turns from the current session. A window scrolled up showed weeks of agent messages and none of the
 * conversation they belonged to.
 *
 * TIMESTAMPS ARE PARSED, NEVER COMPARED AS STRINGS. The batch rows carry msg_store's
 * '2025-11-03T14:35:04.343218+00:00' and the JSONL turns carry '2026-01-02T03:04:05.678Z'.  // operator-id-ok: synthetic clock (01-02T03:04:05), shape required to pin the ms-vs-us compare
 * Those are the same instant format with different offset spellings, so `<` on the raw strings
 * compares '+' against 'Z' and silently mis-filters. An UNPARSEABLE ts is KEPT: this is a
 * display bound, and hiding a message because its clock looked odd is the worse failure.
 */
export function boundQueuedBatches(batches: any[], floorMs: number | null, max = QUEUED_BATCH_MAX): any[] {
  if (!Array.isArray(batches) || !batches.length) return [];
  if (floorMs === null) {
    // No turn to anchor to (a fresh session renders none). Keep the most RECENT few rather
    // than all of history; unparseable rows sort last and are kept by the slice, not dropped.
    const scored = batches.map((b) => ({ b, t: Date.parse(String(b?.ts ?? '')) }));
    scored.sort((x, y) => (Number.isNaN(y.t) ? -1 : Number.isNaN(x.t) ? 1 : y.t - x.t));
    return scored.slice(0, max).map((x) => x.b);
  }
  return batches.filter((b) => {
    const t = Date.parse(String(b?.ts ?? ''));
    return Number.isNaN(t) ? true : t >= floorMs;
  });
}

/** Epoch ms of the OLDEST real turn in the window, or null when the window carries none. */
export function earliestTurnMs(items: any[]): number | null {
  let best: number | null = null;
  for (const it of items || []) {
    // Only real JSONL turns anchor the window. A synthetic would anchor to itself.
    if (it?.kind === 'queued_batch' || it?.queued) continue;
    const t = Date.parse(String(it?.ts ?? ''));
    if (Number.isNaN(t)) continue;
    if (best === null || t < best) best = t;
  }
  return best;
}

