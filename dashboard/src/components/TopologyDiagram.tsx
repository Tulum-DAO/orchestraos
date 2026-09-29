import { clsx } from 'clsx';
import { StatusDot } from './StatusDot';
import { TierBadge } from './TierBadge';

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

function AgentNode({ agent }: { agent: Agent }) {
  const working = isWorking(agent);
  // Agent-level only. The operator explicitly did NOT want skill/prompt execution detail,
  // so this shows the task a seat is on, never which tool or skill is running.
  const subtitle = working ? (agent.current_task || 'working').trim() : '';
  return (
    <div
      className={clsx(
        'relative flex flex-col items-center gap-1 rounded-lg border-2 bg-neutral-900 px-3 py-2.5 min-w-[110px] transition hover:bg-neutral-800',
        working ? 'border-sky-400 shadow-[0_0_12px_-2px_rgba(56,189,248,0.7)]' : getNodeBorder(agent),
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
    </div>
  );
}

function ConnectionLabel({ label }: { label: string }) {
  return (
    <span className="text-[10px] text-neutral-600 bg-neutral-950 px-1.5 py-0.5 rounded z-10 relative">
      {label}
    </span>
  );
}

export function TopologyDiagram({ agents }: TopologyDiagramProps) {
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
          <AgentNode agent={gm} />
          <div className="flex flex-col items-center gap-0">
            <div className="w-px h-6 bg-neutral-700" />
            <ConnectionLabel label="hub-spoke" />
            <div className="w-px h-6 bg-neutral-700" />
          </div>
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
                  {/* Vertical stub from horizontal line */}
                  {gm && <div className="w-px h-3 bg-neutral-700" />}
                  <AgentNode agent={pm} />

                  {/* Workers under this PM */}
                  {pmWorkers.length > 0 && (
                    <>
                      <div className="flex flex-col items-center gap-0">
                        <div className="w-px h-4 bg-neutral-700" />
                        <ConnectionLabel label="mesh" />
                        <div className="w-px h-4 bg-neutral-700" />
                      </div>
                      <div className="flex items-start gap-3 flex-wrap justify-center max-w-xs">
                        {pmWorkers.map((w) => (
                          <AgentNode key={w.id} agent={w} />
                        ))}
                      </div>
                    </>
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
              <AgentNode key={w.id} agent={w} />
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
