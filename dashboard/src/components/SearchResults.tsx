import { clsx } from 'clsx';
import type { Hit, SearchFleetResult } from '../lib/agentSearch';

export interface SearchResultsProps {
  results: SearchFleetResult;
  /** Index into the flattened, rendered order (agents, then repos, then prompts —
   *  matches agentSearch.ts's flattenVisibleHits) for keyboard nav. */
  highlightedIndex?: number;
  onPick: (hit: Hit) => void;
}

const GROUPS: { key: 'agents' | 'repos' | 'prompts'; heading: string; emptyLabel: string }[] = [
  { key: 'agents', heading: 'Agents', emptyLabel: 'No matches' },
  { key: 'repos', heading: 'Repos', emptyLabel: 'No repos registered' },
  { key: 'prompts', heading: 'Prompts', emptyLabel: 'No matches' },
];

export function SearchResults({ results, highlightedIndex, onPick }: SearchResultsProps) {
  // Offsets computed up front (no mutation during render) so each group's hits know their
  // position in the flattened keyboard-nav order without a running counter.
  const agentsCount = results.agents.length;
  const reposCount = results.repos.length;
  const startIndexByGroup: Record<'agents' | 'repos' | 'prompts', number> = {
    agents: 0,
    repos: agentsCount,
    prompts: agentsCount + reposCount,
  };

  // Files is always empty (see lib/agentSearch.ts) — nothing reachable exposes a general
  // file listing, so rendering an always-disabled section on every keystroke would just be
  // clutter (spec §2: calm by default), not a category anyone can ever use.
  return (
    <div className="rounded-xl border border-neutral-800 bg-neutral-900 overflow-hidden">
      {GROUPS.map(({ key, heading, emptyLabel }) => {
        const hits = results[key];
        const startIndex = startIndexByGroup[key];
        return (
          <div key={key} className="border-b border-neutral-800 last:border-b-0">
            <div className="px-3 py-1.5 text-[11px] uppercase tracking-wider text-neutral-500 bg-neutral-950/50">
              {heading}
            </div>
            {hits.length === 0 ? (
              <p className="px-3 py-2 text-sm text-neutral-600">{emptyLabel}</p>
            ) : (
              <ul>
                {hits.map((hit, i) => {
                  const isHighlighted = highlightedIndex === startIndex + i;
                  return (
                    <li key={`${hit.kind}:${hit.id}`}>
                      <button
                        type="button"
                        onClick={() => onPick(hit)}
                        className={clsx(
                          'w-full text-left px-3 py-2 flex items-center justify-between gap-2 transition-colors',
                          isHighlighted ? 'bg-neutral-700/60' : 'hover:bg-neutral-800/60'
                        )}
                      >
                        <span className="text-sm text-neutral-200 break-words">{hit.label}</span>
                        {hit.sublabel && (
                          <span className="text-[11px] text-neutral-500 shrink-0">{hit.sublabel}</span>
                        )}
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        );
      })}
    </div>
  );
}
