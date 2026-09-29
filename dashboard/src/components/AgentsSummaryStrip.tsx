import { clsx } from 'clsx';
import { Search } from 'lucide-react';

export interface AgentsSummaryStripProps {
  machineName: string;
  machineLive: boolean;
  agentsUp: number;
  agentsTotal: number;
  messages24h: number;
  activeConnections: number;
  busiest: { a: string; b: string; count: number } | null;
  windowHours: number;
  search: string;
  onSearchChange: (next: string) => void;
}

function formatAgentsUp(agentsUp: number, agentsTotal: number): string {
  return `${agentsUp} of ${agentsTotal} up`;
}

function formatBusiest(busiest: { a: string; b: string; count: number } | null): string {
  return busiest ? `${busiest.a} to ${busiest.b}, ${busiest.count}` : '—';
}

export function AgentsSummaryStrip({
  machineName,
  machineLive,
  agentsUp,
  agentsTotal,
  messages24h,
  activeConnections,
  busiest,
  windowHours,
  search,
  onSearchChange,
}: AgentsSummaryStripProps) {
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <h1 className="text-2xl font-bold text-neutral-100">Agents</h1>
        <span
          className={clsx('inline-block w-2 h-2 rounded-full shrink-0', machineLive ? 'bg-green-500 motion-safe:animate-pulse' : 'bg-red-500')}
          title={machineLive ? 'live' : 'down'}
        />
        <span className="text-sm text-neutral-500">{machineName}</span>
      </div>
      <div className="flex items-center gap-4 overflow-x-auto pb-2 -mx-6 px-6 scrollbar-hide" style={{ WebkitOverflowScrolling: 'touch' }}>
        <div className="flex items-center gap-4 text-sm text-neutral-400 shrink-0">
          <span>{formatAgentsUp(agentsUp, agentsTotal)}</span>
          <span>{messages24h} messages ({windowHours}h)</span>
          <span>{activeConnections} active connections</span>
          <span>busiest: {formatBusiest(busiest)}</span>
        </div>
        <div className="relative shrink-0 ml-auto">
          <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-neutral-500" />
          <input
            type="text"
            value={search}
            onChange={(e) => onSearchChange(e.target.value)}
            placeholder="Search agents, repos, prompts, files"
            className="w-64 pl-8 pr-3 py-1.5 text-sm rounded-lg bg-neutral-900 border border-neutral-800 text-neutral-100 placeholder:text-neutral-500 focus:outline-none focus:border-neutral-600"
          />
        </div>
      </div>
    </div>
  );
}
