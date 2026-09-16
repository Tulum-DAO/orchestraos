import { useQuery } from '@tanstack/react-query';
import { fetchAgents, fetchAnalyticsDashboard } from '../lib/api';

// ---- Section A: Agent Status Grid ----------------------------------------

function AgentStatusGrid() {
  const { data, isLoading } = useQuery({ queryKey: ['agents'], queryFn: fetchAgents, refetchInterval: 15_000 });

  if (isLoading) return <p className="text-neutral-600 text-sm">Loading agents...</p>;

  const agents: any[] = data?.agents ?? [];
  if (agents.length === 0) return <p className="text-neutral-600 text-sm">No agents found.</p>;

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
      {agents.map((a: any) => {
        const alive = a.alive === true || a.tmux_alive === true;
        return (
          <div
            key={a.id}
            className="flex items-center gap-3 rounded-lg border border-neutral-800 bg-neutral-900 px-3 py-2"
          >
            {/* Status dot */}
            <span
              className={`shrink-0 w-2.5 h-2.5 rounded-full ${alive ? 'bg-green-500' : 'bg-neutral-600'}`}
              title={alive ? 'Running' : 'Stopped'}
            />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium truncate">{a.name || a.id}</p>
              <p className="text-[11px] text-neutral-500 truncate">
                {[a.tier, a.machine].filter(Boolean).join(' · ')}
                {a.current_task ? ` · ${String(a.current_task).slice(0, 40)}` : ''}
              </p>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ---- Section B: Message Throughput (7-day bar chart) ---------------------

function ThroughputBars({ data }: { data: { date: string; label: string; count: number }[] }) {
  if (!data || data.length === 0) {
    return <p className="text-neutral-600 text-sm">No message data yet.</p>;
  }

  const maxCount = Math.max(...data.map(d => d.count), 1);

  return (
    <div className="flex items-end gap-3 h-36 pt-2">
      {data.map(d => (
        <div key={d.date} className="flex flex-col items-center gap-1 flex-1 min-w-0">
          <span className="text-xs text-neutral-400 tabular-nums">{d.count > 0 ? d.count.toLocaleString() : ''}</span>
          <div className="w-full bg-neutral-800 rounded-t flex flex-col justify-end" style={{ height: '96px' }}>
            <div
              className="bg-violet-500 rounded-t w-full transition-all duration-300"
              style={{ height: `${Math.max((d.count / maxCount) * 100, d.count > 0 ? 2 : 0)}%` }}
            />
          </div>
          <span className="text-[10px] text-neutral-500 truncate w-full text-center">{d.label}</span>
        </div>
      ))}
    </div>
  );
}

// ---- Section C: Task Funnel -----------------------------------------------

const FUNNEL_STAGES = [
  { key: 'pending',     label: 'Pending',     color: 'bg-neutral-600',  text: 'text-neutral-300' },
  { key: 'in_progress', label: 'In Progress', color: 'bg-blue-600',     text: 'text-blue-300' },
  { key: 'completed',   label: 'Completed',   color: 'bg-green-600',    text: 'text-green-300' },
  { key: 'blocked',     label: 'Blocked',     color: 'bg-red-600',      text: 'text-red-300' },
] as const;

function TaskFunnel({ funnel }: { funnel: Record<string, number> }) {
  const total = Object.values(funnel).reduce((s, n) => s + n, 0);

  if (total === 0) {
    return <p className="text-neutral-600 text-sm">No tasks found.</p>;
  }

  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
      {FUNNEL_STAGES.map(stage => {
        const count = funnel[stage.key] ?? 0;
        const pct = total > 0 ? Math.round((count / total) * 100) : 0;
        return (
          <div key={stage.key} className={`rounded-xl border border-neutral-800 bg-neutral-900 p-4 flex flex-col gap-1`}>
            <div className={`w-3 h-3 rounded-full ${stage.color}`} />
            <p className="text-2xl font-bold tabular-nums">{count}</p>
            <p className={`text-xs font-medium ${stage.text}`}>{stage.label}</p>
            <p className="text-[11px] text-neutral-600">{pct}% of total</p>
          </div>
        );
      })}
    </div>
  );
}

// ---- Section D: Project Health -------------------------------------------

function formatRelativeTime(isoStr: string | null): string {
  if (!isoStr) return 'Never';
  const diff = Date.now() - new Date(isoStr).getTime();
  const hours = Math.floor(diff / 3_600_000);
  if (hours < 1) return 'Just now';
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function isStale(isoStr: string | null): boolean {
  if (!isoStr) return true;
  return Date.now() - new Date(isoStr).getTime() > 3 * 86_400_000;
}

function ProjectHealthTable({ rows }: { rows: any[] }) {
  if (!rows || rows.length === 0) {
    return <p className="text-neutral-600 text-sm">No active projects.</p>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-neutral-500 text-xs border-b border-neutral-800">
            <th className="pb-2 pr-4 font-medium">Project</th>
            <th className="pb-2 pr-4 font-medium">Agents</th>
            <th className="pb-2 pr-4 font-medium">Open Tasks</th>
            <th className="pb-2 font-medium">Last Activity</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((p: any) => {
            const stale = isStale(p.last_activity);
            return (
              <tr
                key={p.slug}
                className={`border-b border-neutral-800 last:border-0 ${stale ? 'bg-yellow-950/30' : ''}`}
              >
                <td className="py-2 pr-4">
                  <span className="font-medium">{p.name}</span>
                  {stale && (
                    <span className="ml-2 text-[10px] text-yellow-500 font-medium uppercase tracking-wide">stale</span>
                  )}
                </td>
                <td className="py-2 pr-4 text-neutral-400 tabular-nums">
                  {p.active_agents > 0 ? (
                    <span className="text-green-400">{p.active_agents}</span>
                  ) : (
                    <span className="text-neutral-600">0</span>
                  )}
                  <span className="text-neutral-600">/{p.agent_count}</span>
                </td>
                <td className="py-2 pr-4 tabular-nums">
                  {p.open_tasks > 0 ? (
                    <span className="text-blue-400">{p.open_tasks}</span>
                  ) : (
                    <span className="text-neutral-600">0</span>
                  )}
                </td>
                <td className="py-2 text-neutral-500 text-xs">{formatRelativeTime(p.last_activity)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ---- Main Analytics Page --------------------------------------------------

export default function Analytics() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['analytics-dashboard'],
    queryFn: fetchAnalyticsDashboard,
    refetchInterval: 30_000,
  });

  const throughput: any[] = data?.message_throughput ?? [];
  const funnel: Record<string, number> = data?.task_funnel ?? {};
  const projectHealth: any[] = data?.project_health ?? [];

  return (
    <div className="space-y-8">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold">Analytics</h1>
        <p className="text-neutral-500 mt-1">Live fleet metrics — refreshes every 30s</p>
        {error && (
          <p className="mt-2 text-red-400 text-sm">Failed to load metrics: {(error as Error).message}</p>
        )}
      </div>

      {/* Section A: Agent Status Grid */}
      <section>
        <h2 className="text-lg font-semibold mb-3">Agent Status</h2>
        <AgentStatusGrid />
      </section>

      {/* Section B: Message Throughput */}
      <section>
        <h2 className="text-lg font-semibold mb-1">Message Throughput</h2>
        <p className="text-xs text-neutral-500 mb-3">Messages routed per day (last 7 days)</p>
        <div className="rounded-xl border border-neutral-800 bg-neutral-900 px-4 pt-4 pb-3">
          {isLoading ? (
            <p className="text-neutral-600 text-sm py-8 text-center">Loading...</p>
          ) : (
            <ThroughputBars data={throughput} />
          )}
        </div>
      </section>

      {/* Section C: Task Funnel */}
      <section>
        <h2 className="text-lg font-semibold mb-3">Task Funnel</h2>
        {isLoading ? (
          <p className="text-neutral-600 text-sm">Loading...</p>
        ) : (
          <TaskFunnel funnel={funnel} />
        )}
      </section>

      {/* Section D: Project Health */}
      <section>
        <h2 className="text-lg font-semibold mb-3">Project Health</h2>
        <div className="rounded-xl border border-neutral-800 bg-neutral-900 p-4">
          {isLoading ? (
            <p className="text-neutral-600 text-sm">Loading...</p>
          ) : (
            <ProjectHealthTable rows={projectHealth} />
          )}
        </div>
      </section>
    </div>
  );
}
