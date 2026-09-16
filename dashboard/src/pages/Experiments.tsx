import { useState } from 'react';
import { FlaskConical } from 'lucide-react';
import { clsx } from 'clsx';
import { useExperiments } from '../hooks/useExperiments';
import { ExperimentCard } from '../components/ExperimentCard';
import { StatCard } from '../components/StatCard';

type Tab = 'agent' | 'timeline' | 'learnings';

export default function Experiments() {
  const { data, isLoading } = useExperiments();
  const [tab, setTab] = useState<Tab>('timeline');

  if (isLoading) return <p className="text-neutral-500 p-8">Loading...</p>;

  const experiments: any[] = data?.experiments ?? [];
  const activeCycles = data?.active_cycles ?? 0;
  const running = data?.running ?? 0;
  const completed = data?.completed ?? 0;
  const keepRate = data?.keep_rate ?? 0;

  // Group by agent
  const byAgent: Record<string, any[]> = {};
  for (const exp of experiments) {
    const agent = exp.agent_id || 'unassigned';
    if (!byAgent[agent]) byAgent[agent] = [];
    byAgent[agent].push(exp);
  }

  // Learnings = only kept experiments
  const learnings = experiments.filter((e: any) => e.kept);

  function renderCards(list: any[]) {
    if (list.length === 0) return renderEmpty();
    return (
      <div className="space-y-3">
        {list.map((exp: any) => (
          <ExperimentCard key={exp.id} experiment={exp} />
        ))}
      </div>
    );
  }

  function renderEmpty() {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-neutral-500">
        <FlaskConical className="w-12 h-12 mb-3 text-neutral-600" />
        <p className="text-lg font-medium text-neutral-400">No experiments yet</p>
        <p className="text-sm">Autoresearch cycles will appear here when agents propose experiments</p>
      </div>
    );
  }

  function renderByAgent() {
    const agents = Object.keys(byAgent).sort();
    if (agents.length === 0) return renderEmpty();
    return (
      <div className="space-y-6">
        {agents.map(agent => (
          <div key={agent}>
            <h3 className="text-sm font-medium text-neutral-400 mb-3 uppercase tracking-wider">{agent}</h3>
            <div className="space-y-3">
              {byAgent[agent].map((exp: any) => (
                <ExperimentCard key={exp.id} experiment={exp} />
              ))}
            </div>
          </div>
        ))}
      </div>
    );
  }

  const TABS: { key: Tab; label: string }[] = [
    { key: 'agent', label: 'By Agent' },
    { key: 'timeline', label: 'Timeline' },
    { key: 'learnings', label: 'Learnings' },
  ];

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-white">Experiments</h1>
        <p className="text-neutral-500 mt-1 text-sm">Autoresearch cycles across your agent fleet</p>
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard label="Active Cycles" value={activeCycles} />
        <StatCard label="Running" value={running} />
        <StatCard label="Completed" value={completed} />
        <StatCard label="Keep Rate" value={`${keepRate}%`} />
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-neutral-800">
        {TABS.map(t => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={clsx(
              'px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px',
              tab === t.key
                ? 'text-white border-white'
                : 'text-neutral-500 border-transparent hover:text-neutral-300'
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      {tab === 'timeline' && renderCards(experiments)}
      {tab === 'agent' && renderByAgent()}
      {tab === 'learnings' && renderCards(learnings)}
    </div>
  );
}
