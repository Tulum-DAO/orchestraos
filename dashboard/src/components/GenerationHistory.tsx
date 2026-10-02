import { useEffect, useRef, useState } from 'react';
import { History } from 'lucide-react';

interface Generation {
  generation: number;
  model: string | null;
  spawned_at: string | null;
  promoted_at: string | null;
  retired_at: string | null;
  note: string | null;
  current?: boolean;
}

/** A date for display, or "—". Some rows hold a non-date marker here, so never trust Date(). */
function when(v: string | null): string {
  if (!v) return '—';
  const t = Date.parse(v);
  return Number.isNaN(t) ? '—' : new Date(t).toLocaleString();
}

/**
 * "N gens" beside an agent's name. Click it to list the agent's past generations — every
 * restart it has been through — current first, then newest to oldest. Shown only when there is a history to show, and
 * fetched only when opened, so the page's poll carries a count and nothing more.
 */
export function GenerationHistory({ agentId, total }: { agentId: string; total?: number }) {
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState<Generation[] | null>(null);
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

  if (!total || total < 2) return null;

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
        <div className="absolute left-0 top-6 z-40 w-72 max-h-72 overflow-y-auto bg-neutral-900 border border-neutral-700 rounded-lg shadow-xl p-2 text-xs">
          {!rows && <div className="text-neutral-500 p-1">Loading…</div>}
          {rows && rows.length === 0 && <div className="text-neutral-500 p-1">No history recorded.</div>}
          {rows?.map((g) => (
            <div key={`${g.generation}-${g.promoted_at ?? g.spawned_at ?? ''}`} className="py-1.5 px-1 border-b border-neutral-800 last:border-0">
              <div className="flex items-center justify-between">
                <span className="text-neutral-200 font-medium">gen {g.generation}{g.current ? ' · current' : ''}</span>
                <span className="text-neutral-500">{g.model ?? ''}</span>
              </div>
              <div className="text-neutral-500">
                {g.retired_at ? `retired ${when(g.retired_at)}` : `promoted ${when(g.promoted_at ?? g.spawned_at)}`}
              </div>
              {g.note && <div className="text-neutral-600 truncate" title={g.note}>{g.note}</div>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
