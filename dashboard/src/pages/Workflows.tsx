import { useWorkflows } from '../hooks/useWorkflows';
import { StatCard } from '../components/StatCard';
import { CronCard } from '../components/CronCard';

export default function Workflows() {
  const { data, isLoading } = useWorkflows();

  if (isLoading || !data) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold">Workflows</h1>
        <p className="text-neutral-500 mt-2">Loading...</p>
      </div>
    );
  }

  const { by_agent, agents, total, enabled } = data;
  const agentKeys = Object.keys(by_agent).sort();

  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Workflows</h1>
        <p className="text-neutral-500 mt-2">Manage scheduled crons across all agents</p>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
        <StatCard label="Total Crons" value={total} />
        <StatCard label="Agents" value={agents.length} sub={`of ${agents.length} total`} />
        <StatCard label="Enabled" value={enabled} sub={`of ${total} crons`} />
      </div>

      <div className="space-y-4">
        {agentKeys.map((agent) => {
          const crons = by_agent[agent];
          return (
            <div key={agent} className="rounded-xl border border-neutral-800 bg-neutral-900 p-4">
              <div className="flex items-center gap-2 mb-3">
                <h2 className="text-sm font-semibold uppercase tracking-wider text-neutral-300">{agent}</h2>
                <span className="text-xs px-1.5 py-0.5 rounded bg-neutral-800 text-neutral-500">{crons.length}</span>
              </div>
              <div className="space-y-2">
                {crons.map((cron: any) => (
                  <CronCard
                    key={cron.id}
                    name={cron.name}
                    frequencyLabel={cron.frequency_label}
                    description={cron.description}
                  />
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
