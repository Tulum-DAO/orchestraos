import { useEffect, useState } from 'react';
import { ChevronRight, ChevronDown } from 'lucide-react';
import { GenerationList } from './GenerationList';
import { hasGenerationHistory, type GenerationRow } from '../lib/generations';

/**
 * The Generations section of the agent drawer — the route to an agent's lineage from the
 * TOPOLOGY view, which is the view the fleet is watched in. #147 put this history on the
 * agent card and nowhere else, so from the graph there was no way to reach it at all.
 *
 * It lives in the drawer rather than on the node deliberately (operator, 2026-10-02): a node
 * is `min-w-[110px]` and already carries a status dot, name, tier badge, task subtitle and
 * machine; its onClick is taken by selection; and a popover anchored inside the diagram's
 * `overflow-x-auto` container clips at the edge. The drawer is already `fixed` precisely so
 * that opening it never reflows the graph, so this placement costs the graph nothing.
 *
 * The caller MUST pass `key={agent.id}`. The drawer is reused as you click from node to
 * node, so without a key this component keeps the previous agent's rows and expanded state
 * and shows one lineage under another agent's name. A key remounts it instead, which is why
 * there is no reset-on-prop-change effect here (setting state in an effect to undo a prop
 * change is the anti-pattern react-hooks/set-state-in-effect exists to catch).
 *
 * Collapsed by default, and the rows are fetched only on expand — the count in the header
 * comes from `generations_total`, which the agents poll already carries. So opening a node's
 * drawer costs ZERO extra requests, which is what keeps clicking around the graph cheap.
 */
export function AgentGenerations({ agentId, total }: { agentId: string; total?: number }) {
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState<GenerationRow[] | null>(null);

  useEffect(() => {
    if (!open || rows) return;
    fetch(`/api/agents/${encodeURIComponent(agentId)}/generations`)
      .then((r) => (r.ok ? r.json() : { generations: [] }))
      .then((d) => setRows(d.generations ?? []))
      // A scoped-out agent answers 403 and a missing DB answers an empty list. Both are
      // "nothing to show", never an error in the panel.
      .catch(() => setRows([]));
  }, [open, rows, agentId]);

  if (!hasGenerationHistory(total)) return null;

  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex w-full items-center gap-1.5 text-left text-[11px] uppercase tracking-wider text-neutral-500 hover:text-neutral-300 transition-colors"
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        Generations
        <span className="ml-auto normal-case tracking-normal text-neutral-400">{total}</span>
      </button>
      {open && (
        // Capped and scrollable: a deep lineage (gm has 55) must not push the live feed and
        // the message composer off the bottom of the drawer.
        <div className="mt-1.5 max-h-60 overflow-y-auto overscroll-contain rounded-lg border border-neutral-800 bg-neutral-950/60 px-2">
          <GenerationList rows={rows} />
        </div>
      )}
    </div>
  );
}
