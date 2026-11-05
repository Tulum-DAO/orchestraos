/**
 * AgentTree: the strip above the chat that shows, at a glance, what a seat's subagents and their
 * helpers are doing (one line per node), and lets you click one to read its transcript.
 * All logic lives in lib/agentTree (tested); this only renders rows. Theme tokens (tone-*, text-px*) for both themes.
 */
import { useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { fmtAge, hasSubtree, rowsOf, workingCount, type AgentTreeNode, type AgentTreeRow } from '../../lib/agentTree';

interface Props {
  tree: AgentTreeNode | null;
  /** ms since epoch; ticks every second from useAgentTree so ages keep counting. */
  now: number;
  /** The selected node (?sub=), highlighted when it is in the tree. */
  selected: string | null;
  onSelect: (id: string) => void;
  /** Shown only when the API honours ?all=1. */
  supportsAll?: boolean;
  showFinished?: boolean;
  onShowFinished?: (v: boolean) => void;
  /** Collapsed on first render (web: none; phone/watch use defaultCollapsed(maxDepth 2)). */
  initialCollapsed?: Set<string>;
}

const ROW_PX = 26;
const MAX_ROWS = 8;

function Row({ row, selected, onSelect, onToggle }: {
  row: AgentTreeRow; selected: boolean; onSelect: () => void; onToggle: () => void;
}) {
  const muted = row.state === 'finished' || row.state === 'stale';
  return (
    <div
      role="treeitem"
      aria-selected={selected}
      aria-expanded={row.hasChildren ? !row.collapsed : undefined}
      data-testid="agent-tree-row"
      onClick={onSelect}
      className={`flex items-center gap-2 h-[26px] pr-3 text-px12 cursor-pointer select-none rounded
        ${selected ? 'bg-tone-800' : 'hover:bg-tone-800/50'} ${muted ? 'text-tone-500' : 'text-tone-200'}`}
      style={{ paddingLeft: 8 + row.depth * 16 }}
    >
      <button
        type="button"
        aria-label={row.collapsed ? 'Expand' : 'Collapse'}
        onClick={(e) => { e.stopPropagation(); onToggle(); }}
        className={`w-4 h-4 flex items-center justify-center text-tone-500 ${row.hasChildren ? '' : 'invisible'}`}
      >
        {row.collapsed ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
      </button>
      <span className={`w-2 h-2 shrink-0 rounded-full ${row.dot}`} title={row.state} />
      {row.state === 'finished' && <span className="text-px10 text-tone-500 -ml-1">✓</span>}
      <span className="truncate min-w-0 max-w-[40%]" title={row.label}>{row.label}</span>
      {row.badge && <span className="shrink-0 text-px10 px-1.5 rounded bg-emerald-500/15 text-emerald-600 dark:text-emerald-300">{row.badge}</span>}
      <span className={`truncate min-w-0 flex-1 ${row.waitingOn ? 'text-amber-600 dark:text-amber-300/90' : 'text-tone-500'}`} title={row.tool}>{row.tool}</span>
      <span className="shrink-0 tabular-nums text-tone-500 w-14 text-right">{fmtAge(row.ageS)}</span>
      <span className="shrink-0 tabular-nums text-tone-500 w-24 text-right" title={`own ${row.tokensOwn} / with helpers ${row.tokensRollup} tokens`}>{row.tokens}</span>
    </div>
  );
}

export function AgentTree({ tree, now, selected, onSelect, supportsAll, showFinished = false, onShowFinished, initialCollapsed }: Props) {
  const [collapsed, setCollapsed] = useState<Set<string>>(() => initialCollapsed ?? new Set());
  // null = automatic: open while anything below the seat is working.
  const [openPref, setOpenPref] = useState<boolean | null>(null);
  if (!tree || !hasSubtree(tree)) return null;

  const working = workingCount(tree);
  const open = openPref ?? working > 0;
  const rows = rowsOf(tree, collapsed, now);
  const toggle = (id: string) => setCollapsed((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  return (
    <div className="shrink-0 border-b border-border bg-tone-950/60" data-testid="agent-tree">
      <div className="w-full max-w-[860px] mx-auto px-4 py-1.5 flex items-center gap-3 text-px12">
        <button
          type="button"
          onClick={() => setOpenPref(!open)}
          aria-expanded={open}
          className="flex items-center gap-1.5 text-tone-300 hover:text-foreground"
        >
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          <span>{working === 0 ? 'No agents working' : working === 1 ? '1 agent working' : `${working} agents working`}</span>
        </button>
        {supportsAll && onShowFinished && (
          <label className="ml-auto flex items-center gap-1.5 text-tone-500 cursor-pointer">
            <input type="checkbox" checked={showFinished} onChange={(e) => onShowFinished(e.target.checked)} />
            show finished
          </label>
        )}
      </div>
      {open && (
        <div role="tree" className="w-full max-w-[860px] mx-auto px-4 pb-1.5 overflow-y-auto" style={{ maxHeight: ROW_PX * MAX_ROWS }}>
          {rows.map((r) => (
            <Row key={r.id} row={r} selected={r.id === selected} onSelect={() => onSelect(r.id)} onToggle={() => toggle(r.id)} />
          ))}
        </div>
      )}
    </div>
  );
}

export default AgentTree;
