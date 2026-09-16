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

function AgentNode({ agent }: { agent: Agent }) {
  return (
    <div
      className={clsx(
        'relative flex flex-col items-center gap-1 rounded-lg border-2 bg-neutral-900 px-3 py-2.5 min-w-[110px] transition hover:bg-neutral-800',
        getNodeBorder(agent),
        getNodeOpacity(agent)
      )}
    >
      <div className="flex items-center gap-1.5">
        <StatusDot status={agent.alive ? 'running' : 'stopped'} />
        <span className="text-sm font-semibold text-neutral-100 truncate max-w-[90px]">{agent.name}</span>
      </div>
      <TierBadge tier={agent.tier} />
      {agent.machine && (
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
