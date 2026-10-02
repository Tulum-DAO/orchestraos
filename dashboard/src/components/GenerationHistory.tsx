import { useEffect, useRef, useState } from 'react';
import { History } from 'lucide-react';
import { GenerationList } from './GenerationList';
import { hasGenerationHistory, type GenerationRow } from '../lib/generations';

/**
 * "N gens" beside an agent's name on its card. Click it to list the agent's past generations
 * — every restart it has been through — current first, then newest to oldest. Shown only when
 * there is a history to show, and fetched only when opened, so the page's poll carries a
 * count and nothing more.
 *
 * The rows themselves are GenerationList's job, shared with the agent drawer that topology
 * opens. This component owns only the chip and the popover around them.
 */
export function GenerationHistory({ agentId, total }: { agentId: string; total?: number }) {
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState<GenerationRow[] | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open || rows) return;
    fetch(`/api/agents/${encodeURIComponent(agentId)}/generations`)
      .then((r) => (r.ok ? r.json() : { generations: [] }))
      .then((d) => setRows(d.generations ?? []))
      .catch(() => setRows([]));
  }, [open, rows, agentId]);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [open]);

  if (!hasGenerationHistory(total)) return null;

  return (
    <div className="relative shrink-0" ref={ref}>
      <button
        type="button"
        onClick={(e) => { e.stopPropagation(); setOpen(!open); }}
        className="flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] text-neutral-400 border border-neutral-700 hover:text-neutral-200 hover:border-neutral-500"
        title="Past generations of this agent"
      >
        <History size={10} /> {total} gens
      </button>
      {open && (
        <div className="absolute left-0 top-6 z-40 w-72 max-h-72 overflow-y-auto bg-neutral-900 border border-neutral-700 rounded-lg shadow-xl p-2">
          <GenerationList rows={rows} />
        </div>
      )}
    </div>
  );
}
