import { useMemo } from 'react';
import { clsx } from 'clsx';
import { StatusDot } from './StatusDot';
import { TierBadge } from './TierBadge';
import type { PairCountRow } from '../lib/api';
import { lineWidthPx, pairKey, shortAgo } from '../lib/topologyLines';
import { dotColor, type TrafficMessage } from '../lib/fleetTraffic';

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
  /** Clicking empty space clears the selection (spec §4). */
  onClearSelection?: () => void;
  /** Explicit set of agent ids to keep bright, overriding the selection-derived set. Used by
   *  search: picking a repo or prompt highlights the agents that use it (spec §8) without
   *  opening any panel. */
  brightIds?: string[] | null;
  /** Messages that JUST arrived, one dot each, colour by type and direction by sender
   *  (spec §4). The page owns the poll-diff and the expiry; this component only draws what
   *  it is handed, so a dot can never appear without a real message behind it (spec §2). */
  dots?: TrafficMessage[];
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

/** Spec §4 selection: the selected agent and everyone it talks to stay bright, everything
 *  else fades. Returns null when nothing is selected, which the callers read as "no fading" —
 *  distinct from "an empty neighbour set", where everything else SHOULD fade. */
function useBrightSet(
  selectedAgentId: string | null | undefined, pairs: PairCountRow[] | undefined,
): Set<string> | null {
  return useMemo(() => {
    if (!selectedAgentId) return null;
    const bright = new Set<string>([selectedAgentId]);
    for (const p of pairs ?? []) {
      if (p.a === selectedAgentId) bright.add(p.b);
      else if (p.b === selectedAgentId) bright.add(p.a);
    }
    return bright;
  }, [selectedAgentId, pairs]);
}

function useDotsByPair(dots: TrafficMessage[] | undefined): Map<string, TrafficMessage[]> {
  return useMemo(() => {
    const m = new Map<string, TrafficMessage[]>();
    for (const d of dots ?? []) {
      const k = pairKey(d.from_agent, d.to_agent);
      const list = m.get(k);
      if (list) list.push(d); else m.set(k, [d]);
    }
    return m;
  }, [dots]);
}

/**
 * `busiest` is the denominator every line's thickness is scaled against, and it MUST be the
 * busiest line actually DRAWN — not the busiest pair in the data.
 *
 * review's browser pass (2026-09-30) caught this: the global busiest pair was gm<->telegram
 * at 150, but telegram is not a node in this tree — it is invisible and unclickable. So every
 * visible line was scaled against a number the operator cannot see and drawn systematically
 * too thin relative to the busiest line on screen (gm<->build, 131). The thicknesses were
 * internally consistent and quietly wrong, which is why only looking at it found this.
 *
 * `drawnKeys` is the set of pairKeys the tree renders. Empty (nothing drawn yet) falls back to
 * the global max rather than to 0, so the first render cannot divide by zero.
 */
function usePairLookup(pairs: PairCountRow[] | undefined, drawnKeys: Set<string>): PairLookup {
  return useMemo(() => {
    const map = new Map<string, PairCountRow>();
    let globalMax = 0;
    let drawnMax = 0;
    for (const p of pairs ?? []) {
      const k = pairKey(p.a, p.b);
      map.set(k, p);
      if (p.count > globalMax) globalMax = p.count;
      if (drawnKeys.has(k) && p.count > drawnMax) drawnMax = p.count;
    }
    return {
      get: (a: string, b: string) => map.get(pairKey(a, b)),
      busiest: drawnKeys.size > 0 ? drawnMax : globalMax,
    };
  }, [pairs, drawnKeys]);
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
  agent, onSelect, selected, dimmed,
}: { agent: Agent; onSelect?: (id: string) => void; selected?: boolean; dimmed?: boolean }) {
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
        // Fade, not hide: a down agent that is also unrelated to the selection still has to
        // stay on screen and still has to be red (spec §2, §7 — nothing important is hidden).
        dimmed && 'opacity-30',
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
  a, b, lookup, windowHours, onSelect, stubPx, dimmed, dots,
}: {
  a: string; b: string; lookup: PairLookup; windowHours: number;
  onSelect?: (a: string, b: string) => void; stubPx: number;
  dimmed?: boolean; dots?: TrafficMessage[];
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
    // `relative` so a dot animates across the WHOLE connection, label included, rather than
    // across one half of it — over a 30px segment, half the travel reads as a flicker.
    <div className={clsx('relative flex flex-col items-center gap-0', dimmed && 'opacity-30')}>
      {line}
      {label}
      {line}
      {/* `a` is always the upper node in the tree, so a message sent BY `a` travels down and
          one sent TO it travels up. Direction comes off the message, never from the line. */}
      {(dots ?? []).map((d) => (
        <span
          key={d.id}
          aria-hidden
          className={clsx('fleet-dot', d.from_agent === a ? 'fleet-dot-down' : 'fleet-dot-up',
            dotColor(d.type))}
        />
      ))}
    </div>
  );
}

export function TopologyDiagram({
  agents, pairs, windowHours = 24, onSelectConnection, onSelectAgent, selectedAgentId,
  onClearSelection, dots, brightIds,
}: TopologyDiagramProps) {
  // Tier split first: the set of lines the tree will draw is what the thickness denominator
  // has to be computed from, so it cannot wait until render.
  const gm = agents.find((a) => a.tier === 'T0');
  const pms = agents.filter((a) => a.tier === 'T1');
  const workers = agents.filter((a) => a.tier === 'T2' || a.tier === 'T3');

  const workersByParent: Record<string, Agent[]> = {};
  for (const w of workers) {
    const parent = w.parent || '_unassigned';
    if (!workersByParent[parent]) workersByParent[parent] = [];
    workersByParent[parent].push(w);
  }

  // Derived entirely from `agents` inside the memo rather than from the locals above, so the
  // dependency list is honest — the locals are rebuilt every render and listing them would
  // defeat the memo while satisfying the linter, which is the wrong trade.
  const drawnKeys = useMemo(() => {
    const keys = new Set<string>();
    const top = agents.find((a) => a.tier === 'T0');
    const leads = agents.filter((a) => a.tier === 'T1');
    const leadIds = new Set(leads.map((l) => l.id));
    for (const lead of leads) {
      if (top) keys.add(pairKey(top.id, lead.id));
    }
    for (const w of agents) {
      if (w.tier !== 'T2' && w.tier !== 'T3') continue;
      if (w.parent && leadIds.has(w.parent)) keys.add(pairKey(w.parent, w.id));
    }
    return keys;
  }, [agents]);

  const lookup = usePairLookup(pairs, drawnKeys);
  const selectionBright = useBrightSet(selectedAgentId, pairs);
  // An explicit highlight wins over the selection-derived set: the two are different questions
  // ("who does this agent talk to" vs "who uses this repo") and answering both at once would
  // brighten a union that means neither.
  const bright = useMemo(
    () => (brightIds ? new Set(brightIds) : selectionBright),
    [brightIds, selectionBright],
  );
  const dotsByPair = useDotsByPair(dots);
  const isDim = (id: string) => bright !== null && !bright.has(id);
  // A line stays bright only if the selected agent is one of its two ends.
  const lineDim = (a: string, b: string) =>
    bright !== null && a !== selectedAgentId && b !== selectedAgentId;
  // PMs that have workers or exist
  const pmIds = new Set(pms.map((p) => p.id));
  // Workers with no matching PM parent
  const orphanWorkers = workers.filter((w) => !w.parent || !pmIds.has(w.parent));

  return (
    // Spec §4: clicking empty space clears. Guarded on e.target === e.currentTarget so a
    // click that bubbled up from a node or a line does not immediately undo the selection
    // it just made — without that check, selecting anything is impossible.
    <div
      className="flex flex-col items-center gap-0 py-6 overflow-x-auto"
      onClick={(e) => { if (onClearSelection && e.target === e.currentTarget) onClearSelection(); }}
    >
      {/* T0: GM */}
      {gm && (
        <>
          <AgentNode agent={gm} onSelect={onSelectAgent} selected={selectedAgentId === gm.id} dimmed={isDim(gm.id)} />
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
                      onSelect={onSelectConnection} stubPx={18}
                      dimmed={lineDim(gm.id, pm.id)}
                      dots={dotsByPair.get(pairKey(gm.id, pm.id))}
                    />
                  )}
                  <AgentNode agent={pm} onSelect={onSelectAgent} selected={selectedAgentId === pm.id} dimmed={isDim(pm.id)} />

                  {/* Workers under this PM. Each worker gets its OWN line to the PM rather
                      than sharing one "mesh" label for the group — a per-pair count is the
                      whole point, and one label for six workers can't carry six numbers. */}
                  {pmWorkers.length > 0 && (
                    <div className="flex items-start gap-3 flex-wrap justify-center max-w-xs">
                      {pmWorkers.map((w) => (
                        <div key={w.id} className="flex flex-col items-center gap-0">
                          <Connection
                            a={pm.id} b={w.id} lookup={lookup} windowHours={windowHours}
                            onSelect={onSelectConnection} stubPx={14}
                            dimmed={lineDim(pm.id, w.id)}
                            dots={dotsByPair.get(pairKey(pm.id, w.id))}
                          />
                          <AgentNode agent={w} onSelect={onSelectAgent} selected={selectedAgentId === w.id} dimmed={isDim(w.id)} />
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
              <AgentNode key={w.id} agent={w} onSelect={onSelectAgent} selected={selectedAgentId === w.id} dimmed={isDim(w.id)} />
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
