import {
  describeGenerationTiming,
  generationKey,
  type GenerationRow,
} from '../lib/generations';

/**
 * The rows of one agent's generation history. Rendered in two places — the `N gens` popover
 * on a card, and the Generations section of the agent drawer that topology opens — so the row
 * shape lives here once. Two copies of one display rule drifting apart is what produced the
 * count-keying defect #148 had to fix.
 *
 * Order is the API's and is NOT re-sorted here: current first, then by time rather than by
 * generation number. Numbers reset (gm's live head is generation 2 while its history runs to
 * 87), so sorting by number puts a retired generation at the top — a bug #147 shipped in its
 * first cut and caught on staging.
 *
 * `rows === null` means the fetch is still in flight; an empty array means there is genuinely
 * nothing recorded. Both are ordinary states, never errors.
 */
export function GenerationList({ rows }: { rows: GenerationRow[] | null }) {
  if (!rows) return <div className="text-neutral-500 p-1 text-xs">Loading…</div>;
  if (rows.length === 0) return <div className="text-neutral-500 p-1 text-xs">No history recorded.</div>;

  return (
    <div className="text-xs">
      {rows.map((g) => (
        <div key={generationKey(g)} className="py-1.5 px-1 border-b border-neutral-800 last:border-0">
          <div className="flex items-center justify-between gap-2">
            <span className="text-neutral-200 font-medium">
              gen {g.generation}
              {g.current ? <span className="text-sky-300"> · current</span> : ''}
            </span>
            {g.model && <span className="text-neutral-500 truncate max-w-[45%]" title={g.model}>{g.model}</span>}
          </div>
          <div className="text-neutral-500">{describeGenerationTiming(g)}</div>
          {g.note && <div className="text-neutral-600 truncate" title={g.note}>{g.note}</div>}
        </div>
      ))}
    </div>
  );
}
