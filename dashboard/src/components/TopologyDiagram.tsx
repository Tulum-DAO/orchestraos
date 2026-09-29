import { useMemo } from 'react';
import { clsx } from 'clsx';
import { StatusDot } from './StatusDot';
import { TierBadge } from './TierBadge';
import type { PairCountRow } from '../lib/api';
import { lineWidthPx, pairKey, shortAgo } from '../lib/topologyLines';

interface Agent {
  id: string;
  name: string;
  tier: string;
  machine?: string;
  machine_status?: string;
  parent?: string;
  alive?: boolean;
  tmux_alive?: boolean;
  can_spawn?: string[];
  // The API already returned these; this component simply declared a narrower type and
  // dropped them, which is why the org chart could only ever show reachable-vs-dead while
  // the individual agent panes showed real activity. Item 5 of the operator's ask.
  status?: string;
  activity?: string;
  current_task?: string;
}

interface TopologyDiagramProps {
  agents: Agent[];
  /** Canonical per-pair counts from GET /api/messages/pair-counts (spec §16). Omit and the
   *  lines render as they always did: no counts, no thickness, nothing pretending to be data. */
  pairs?: PairCountRow[];
  /** The window `pairs` was counted over. Only used to label it honestly in tooltips. */
  windowHours?: number;
  /** Click a line. Passed the two ends; the page decides what opens. */
  onSelectConnection?: (a: string, b: string) => void;
  /** Click an agent box. The page decides what opens. */
  onSelectAgent?: (id: string) => void;
  /** Agent whose panel is currently open — gets a bright border (spec §4 selection). */
  selectedAgentId?: string | null;
}

/** A box or a line is a real control when it has a handler and plain markup when it does not.
 *  One element, two shells: without this the node markup would exist in two copies and they
 *  would drift. `button` over `div onClick` so the graph is usable from a keyboard (spec §2
 *  says everything is clickable — that has to include people not using a mouse). */
function Clickable({
  onClick, title, className, children,
}: {
  onClick?: () => void; title?: string; className: string; children: React.ReactNode;
}) {
  if (!onClick) return <div title={title} className={className}>{children}</div>;
  return (
    <button type="button" onClick={onClick} title={title}
      className={clsx(className, 'text-left focus:outline-none focus:ring-2 focus:ring-sky-500')}>
      {children}
    </button>
  );
}

// 2D Agents View spec §4. Lines carry the message count for the window and get thicker with
// traffic; a line with no traffic goes thin and grey rather than disappearing (spec §12 —
// nothing important is hidden). This replaces the literal "hub-spoke"/"mesh" strings, which
// described the topology's shape and told you nothing about whether anything was moving.
// The maths lives in lib/topologyLines.ts so it can be tested and so this file keeps
// exporting nothing but components (react-refresh/only-export-components).

type PairLookup = { get: (a: string, b: string) => PairCountRow | undefined; busiest: number };

function usePairLookup(pairs: PairCountRow[] | undefined): PairLookup {
  return useMemo(() => {
    const map = new Map<string, PairCountRow>();
    let busiest = 0;
    for (const p of pairs ?? []) {
      map.set(pairKey(p.a, p.b), p);
      if (p.count > busiest) busiest = p.count;
    }
    return { get: (a: string, b: string) => map.get(pairKey(a, b)), busiest };
  }, [pairs]);
}

function getNodeBorder(agent: Agent): string {
  if (agent.alive || agent.tmux_alive) {
    if (agent.machine === 'mac' && (agent.machine_status === 'sleeping' || agent.machine_status === 'offline')) {
      return 'border-amber-500';
    }
    return 'border-green-500';
  }
  return 'border-red-500/60';
}

function getNodeOpacity(agent: Agent): string {
  if (!agent.alive && !agent.tmux_alive) return 'opacity-50';
  return '';
}

// A seat that is merely reachable and a seat that is mid-turn are different facts, and
// the org chart previously showed only the first. `working` is the live detector state
// (corroborated by transcript activity server-side); anything else falls back to the
// reachable/dead distinction the tree already made.
function isWorking(agent: Agent): boolean {
  return agent.status === 'working' && (agent.alive || agent.tmux_alive) === true;
}

function AgentNode({
  agent, onSelect, selected,
}: { agent: Agent; onSelect?: (id: string) => void; selected?: boolean }) {
  const working = isWorking(agent);
  // Agent-level only. The operator explicitly did NOT want skill/prompt execution detail,
  // so this shows the task a seat is on, never which tool or skill is running.
  const subtitle = working ? (agent.current_task || 'working').trim() : '';
  return (
    <Clickable
      onClick={onSelect ? () => onSelect(agent.id) : undefined}
      className={clsx(
        'relative flex flex-col items-center gap-1 rounded-lg border-2 bg-neutral-900 px-3 py-2.5 min-w-[110px] transition hover:bg-neutral-800',
        working ? 'border-sky-400 shadow-[0_0_12px_-2px_rgba(56,189,248,0.7)]' : getNodeBorder(agent),
        // Selection reads as a ring, not a border swap: overwriting the border would throw
        // away the status colour, which is the one thing that must never be hidden (spec §2).
        selected && 'ring-2 ring-sky-300 ring-offset-2 ring-offset-neutral-900',
        getNodeOpacity(agent)
      )}
      title={working ? (agent.activity || 'working') : (agent.status || (agent.alive ? 'idle' : 'stopped'))}
    >
      {working && (
        <span
          aria-hidden
          className="absolute -top-1 -right-1 flex h-2.5 w-2.5"
        >
          {/* motion-safe: the expanding ring is decoration, so it is the only part gated.
              Every bit of INFORMATION survives prefers-reduced-motion — the solid dot
              below, the sky border and the task subtitle all stay — so a reader who
              suppresses motion loses the animation, never the meaning. A continuously
              looping pulse is the shape that actually troubles vestibular sensitivity,
              which is why this one is worth gating and a one-shot spinner is not. */}
          <span className="absolute inline-flex h-full w-full motion-safe:animate-ping rounded-full bg-sky-400 opacity-75" />
          <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-sky-400" />
        </span>
      )}
      <div className="flex items-center gap-1.5">
        <StatusDot status={agent.alive ? 'running' : 'stopped'} />
        <span className="text-sm font-semibold text-neutral-100 truncate max-w-[90px]">{agent.name}</span>
      </div>
      <TierBadge tier={agent.tier} />
      {subtitle && (
        <span className="text-[10px] text-sky-300/90 truncate max-w-[100px]" title={subtitle}>
          {subtitle}
        </span>
      )}
      {!working && agent.machine && (
        <span className="text-[10px] text-neutral-500">{agent.machine}</span>
      )}
    </Clickable>
  );
}

/** One clickable segment between two named agents, thickness and count from `lookup`.
 *  `stubPx` is the length of the line above and below the label, so the caller keeps the
 *  spacing the tree already had instead of this component guessing at layout. */
function Connection({
  a, b, lookup, windowHours, onSelect, stubPx,
}: {
  a: string; b: string; lookup: PairLookup; windowHours: number;
  onSelect?: (a: string, b: string) => void; stubPx: number;
}) {
  const row = lookup.get(a, b);
  const count = row?.count ?? 0;
  const width = lineWidthPx(count, lookup.busiest);
  const tip = count
    ? `${a} ⇄ ${b} · ${count} message${count === 1 ? '' : 's'} · last ${shortAgo(row?.last_at ?? null)}`
    : `${a} ⇄ ${b} · no messages in the last ${windowHours}h`;
  const line = (
    <div
      className={clsx('shrink-0', count ? 'bg-neutral-500' : 'bg-neutral-800')}
      style={{ width: `${width}px`, height: `${stubPx}px` }}
    />
  );
  // A button, not a div with onClick: the lines are real controls, so they have to be
  // reachable and announced without a mouse (spec §2, "everything is clickable" — for
  // everyone). No onSelect and it degrades to plain text rather than a dead button.
  const label = onSelect ? (
    <button
      type="button"
      onClick={() => onSelect(a, b)}
      title={tip}
      aria-label={tip}
      className={clsx(
        'text-[10px] bg-neutral-950 px-1.5 py-0.5 rounded z-10 relative transition-colors',
        'hover:text-neutral-100 hover:bg-neutral-800 focus:outline-none focus:ring-1 focus:ring-sky-500',
        count ? 'text-neutral-400' : 'text-neutral-600',
      )}
    >
      {count || '—'}
    </button>
  ) : (
    <span
      title={tip}
      className={clsx('text-[10px] bg-neutral-950 px-1.5 py-0.5 rounded z-10 relative',
        count ? 'text-neutral-400' : 'text-neutral-600')}
    >
      {count || '—'}
    </span>
  );
  return (
    <div className="flex flex-col items-center gap-0">
      {line}
      {label}
      {line}
    </div>
  );
}

export function TopologyDiagram({
  agents, pairs, windowHours = 24, onSelectConnection, onSelectAgent, selectedAgentId,
}: TopologyDiagramProps) {
  const lookup = usePairLookup(pairs);
  // Separate by tier
  const gm = agents.find((a) => a.tier === 'T0');
  const pms = agents.filter((a) => a.tier === 'T1');
  const workers = agents.filter((a) => a.tier === 'T2' || a.tier === 'T3');

  // Group workers by parent
  const workersByParent: Record<string, Agent[]> = {};
  for (const w of workers) {
    const parent = w.parent || '_unassigned';
    if (!workersByParent[parent]) workersByParent[parent] = [];
    workersByParent[parent].push(w);
  }

  // PMs that have workers or exist
  const pmIds = new Set(pms.map((p) => p.id));
  // Workers with no matching PM parent
  const orphanWorkers = workers.filter((w) => !w.parent || !pmIds.has(w.parent));

  return (
    <div className="flex flex-col items-center gap-0 py-6 overflow-x-auto">
      {/* T0: GM */}
      {gm && (
        <>
          <AgentNode agent={gm} onSelect={onSelectAgent} selected={selectedAgentId === gm.id} />
          {/* Trunk down to the PM bar. Deliberately unlabelled: this is a bus, not a pair,
              and the "hub-spoke" string it replaces described the shape rather than the
              traffic. The real gm<->pm counts sit on each PM's own segment below. */}
          <div className="w-px h-12 bg-neutral-700" />
        </>
      )}

      {/* T1: PMs row */}
      {pms.length > 0 && (
        <>
          {/* Horizontal connector */}
          <div className="relative flex items-start justify-center">
            {/* Horizontal line spanning all PMs */}
            {pms.length > 1 && (
              <div
                className="absolute top-0 h-px bg-neutral-700"
                style={{
                  left: `calc(50% - ${(pms.length - 1) * 80}px)`,
                  width: `${(pms.length - 1) * 160}px`,
                }}
              />
            )}
          </div>
          <div className="flex items-start gap-8 flex-wrap justify-center">
            {pms.map((pm) => {
              const pmWorkers = workersByParent[pm.id] || [];
              return (
                <div key={pm.id} className="flex flex-col items-center gap-0">
                  {/* The gm<->pm line itself: count, thickness and click all live here. */}
                  {gm && (
                    <Connection
                      a={gm.id} b={pm.id} lookup={lookup} windowHours={windowHours}
                      onSelect={onSelectConnection} stubPx={8}
                    />
                  )}
                  <AgentNode agent={pm} onSelect={onSelectAgent} selected={selectedAgentId === pm.id} />

                  {/* Workers under this PM. Each worker gets its OWN line to the PM rather
                      than sharing one "mesh" label for the group — a per-pair count is the
                      whole point, and one label for six workers can't carry six numbers. */}
                  {pmWorkers.length > 0 && (
                    <div className="flex items-start gap-3 flex-wrap justify-center max-w-xs">
                      {pmWorkers.map((w) => (
                        <div key={w.id} className="flex flex-col items-center gap-0">
                          <Connection
                            a={pm.id} b={w.id} lookup={lookup} windowHours={windowHours}
                            onSelect={onSelectConnection} stubPx={6}
                          />
                          <AgentNode agent={w} onSelect={onSelectAgent} selected={selectedAgentId === w.id} />
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </>
      )}

      {/* Orphan workers (no parent or parent not a PM) */}
      {orphanWorkers.length > 0 && (
        <div className="mt-6 pt-4 border-t border-neutral-800 w-full">
          <p className="text-xs text-neutral-600 text-center mb-3">Unassigned agents</p>
          <div className="flex items-start gap-3 flex-wrap justify-center">
            {orphanWorkers.map((w) => (
              <AgentNode key={w.id} agent={w} onSelect={onSelectAgent} selected={selectedAgentId === w.id} />
            ))}
          </div>
        </div>
      )}

      {/* Empty state */}
      {agents.length === 0 && (
        <p className="text-neutral-600 text-sm py-12">No agents to display</p>
      )}
    </div>
  );
}
