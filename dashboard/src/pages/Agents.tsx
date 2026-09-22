import { useState, useMemo, useEffect } from 'react';
import { clsx } from 'clsx';
import { LayoutGrid, List, GitBranch, Plus } from 'lucide-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useAgents } from '../hooks/useAgents';
import { useUser, canSeeAgent } from '../hooks/useUser';
import { spawnAgent, killAgent, getAdaptiveAgents } from '../lib/api';
import { StatusDot } from '../components/StatusDot';
import { TierBadge } from '../components/TierBadge';
import { AgentCard } from '../components/AgentCard';
import { TopologyDiagram } from '../components/TopologyDiagram';
import { getRecentAgents, loadAndMergeRecentAgents, type RecentAgent } from '../lib/user-actions';
import { GenChip } from '../components/GenChip';
import NewAgentModal from '../components/NewAgentModal';

const TIERS = ['For You', 'Recent', 'All', 'T0', 'T1', 'T2', 'T3'] as const;
const STATUS_FILTERS = ['All', 'Active', 'Dead'] as const;
const TIER_ORDER: Record<string, number> = { T0: 0, T1: 1, T2: 2, T3: 3 };

type ViewMode = 'cards' | 'table' | 'topology';

export default function Agents() {
  const { data, isLoading } = useAgents();
  const { data: user } = useUser();
  const queryClient = useQueryClient();
  const [showNewAgent, setShowNewAgent] = useState(false);
  const [tierFilter, setTierFilter] = useState<string>('For You');
  const [selectedAgent, setSelectedAgent] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>('cards');
  const [pendingSpawn, setPendingSpawn] = useState<Set<string>>(new Set());
  const [pendingKill, setPendingKill] = useState<Set<string>>(new Set());
  const [statusFilter, setStatusFilter] = useState<string>('Active');
  const [clientFilter, setClientFilter] = useState<string>('All');
  const [machineFilter, setMachineFilter] = useState<string>('All');
  const [recentAgents, setRecentAgents] = useState<RecentAgent[]>(getRecentAgents());

  // On mount: merge localStorage with server-persisted recents
  useEffect(() => {
    loadAndMergeRecentAgents().then(setRecentAgents);
  }, []);

  const { data: adaptiveScores } = useQuery({
    queryKey: ['adaptive-agents'],
    queryFn: () => getAdaptiveAgents(),
    enabled: tierFilter === 'For You',
    refetchInterval: 30_000,
  });

  const spawnMutation = useMutation({
    mutationFn: (id: string) => spawnAgent(id),
    onMutate: (id) => setPendingSpawn((s) => new Set(s).add(id)),
    onSettled: (_, __, id) => {
      setPendingSpawn((s) => { const n = new Set(s); n.delete(id); return n; });
      queryClient.invalidateQueries({ queryKey: ['agents'] });
    },
  });

  const killMutation = useMutation({
    mutationFn: (id: string) => killAgent(id),
    onMutate: (id) => setPendingKill((s) => new Set(s).add(id)),
    onSettled: (_, __, id) => {
      setPendingKill((s) => { const n = new Set(s); n.delete(id); return n; });
      queryClient.invalidateQueries({ queryKey: ['agents'] });
    },
  });

  const allAgents = data?.agents || [];
  // Filter agents by user permissions
  const agents = user?.allowed_agents === '*'
    ? allAgents
    : allAgents.filter((a: any) => canSeeAgent(user?.allowed_agents || '*', a.id));

  // Extract unique clients, types, and machines from tags
  const clients = useMemo(() => {
    const set = new Set<string>();
    agents.forEach((a: any) => {
      const tags: string[] = a.tags || [];
      tags.forEach((t: string) => { if (t.startsWith('client:')) set.add(t.slice(7)); });
    });
    return ['All', 'Internal', ...Array.from(set).sort()];
  }, [agents]);

  const machines = useMemo(() => {
    const set = new Set<string>();
    agents.forEach((a: any) => {
      const tags: string[] = a.tags || [];
      tags.forEach((t: string) => { if (t.startsWith('machine:')) set.add(t.slice(8)); });
      // Fallback to machine field
      if (a.machine && !tags.some((t: string) => t.startsWith('machine:'))) set.add(a.machine);
    });
    return ['All', ...Array.from(set).sort()];
  }, [agents]);

  // Helper: check if agent has a tag
  const hasTag = (a: any, prefix: string, value: string) => {
    const tags: string[] = a.tags || [];
    return tags.includes(`${prefix}:${value}`);
  };
  const hasAnyTagWithPrefix = (a: any, prefix: string) => {
    const tags: string[] = a.tags || [];
    return tags.some((t: string) => t.startsWith(`${prefix}:`));
  };

  // Filter by client tag
  const clientFiltered = clientFilter === 'All'
    ? agents
    : clientFilter === 'Internal'
      ? agents.filter((a: any) => !hasAnyTagWithPrefix(a, 'client'))
      : agents.filter((a: any) => hasTag(a, 'client', clientFilter));

  // Filter by machine tag
  const machineFiltered = machineFilter === 'All'
    ? clientFiltered
    : clientFiltered.filter((a: any) => hasTag(a, 'machine', machineFilter) || a.machine === machineFilter);

  // Filter by tier
  const tierFiltered = tierFilter === 'All' || tierFilter === 'For You' || tierFilter === 'Recent'
    ? machineFiltered
    : machineFiltered.filter((a: any) => a.tier === tierFilter);

  // Filter by status
  const filtered = statusFilter === 'All'
    ? tierFiltered
    : statusFilter === 'Active'
      ? tierFiltered.filter((a: any) => a.alive)
      : tierFiltered.filter((a: any) => !a.alive);

  // Sort: alive first, then by tier (default)
  const defaultSorted = [...filtered].sort((a: any, b: any) => {
    if (a.alive && !b.alive) return -1;
    if (!a.alive && b.alive) return 1;
    return (TIER_ORDER[a.tier] ?? 9) - (TIER_ORDER[b.tier] ?? 9);
  });

  // When "For You", sort by adaptive score; when "Recent", sort by recency
  const sorted = useMemo(() => {
    if (tierFilter === 'Recent') {
      const recentIds = recentAgents.map(r => r.id);
      const recentSet = new Set(recentIds);
      const inRecent = filtered.filter((a: any) => recentSet.has(a.id));
      // Sort by position in recentAgents (most recent first)
      inRecent.sort((a: any, b: any) => recentIds.indexOf(a.id) - recentIds.indexOf(b.id));
      return inRecent;
    }
    if (tierFilter !== 'For You' || !adaptiveScores || !filtered.length) return defaultSorted;
    const scoreMap = new Map(adaptiveScores.map(s => [s.agent_id, s]));
    return [...filtered].sort((a: any, b: any) => {
      const sa = scoreMap.get(a.id)?.score ?? 0;
      const sb = scoreMap.get(b.id)?.score ?? 0;
      return sb - sa;
    });
  }, [tierFilter, adaptiveScores, filtered, defaultSorted, recentAgents]);

  if (isLoading) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold text-neutral-100 mb-6">Agents</h1>
        <p className="text-neutral-500">Loading...</p>
      </div>
    );
  }

  // Health summary
  const healthy = agents.filter((a: any) => a.alive).length;
  const sleeping = agents.filter((a: any) => a.machine === 'mac' && (a.machine_status === 'sleeping' || a.machine_status === 'offline')).length;
  const stale = agents.filter((a: any) => !a.alive && a.always_on).length;
  const down = agents.filter((a: any) => !a.alive && !a.always_on).length;

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-neutral-100">Agents</h1>
            <p className="text-sm text-neutral-500 mt-0.5">{sorted.length} of {agents.length} agents</p>
          </div>
          <button
            onClick={() => setShowNewAgent(true)}
            className="flex items-center gap-2 px-4 py-2 min-h-[44px] text-sm rounded-lg bg-blue-900/50 border border-blue-700/50 text-blue-200 hover:bg-blue-800/60 transition-colors"
          >
            <Plus size={16} />
            New agent
          </button>
        </div>
        <NewAgentModal
          open={showNewAgent}
          onClose={() => setShowNewAgent(false)}
          taken={new Set(agents.map((a: { id: string }) => String(a.id)))}
          onCreated={() => { queryClient.invalidateQueries({ queryKey: ['agents'] }); }}
        />
        {/* Options bar — horizontally scrollable on mobile */}
        <div className="flex items-center gap-3 overflow-x-auto pb-2 -mx-6 px-6 scrollbar-hide" style={{ WebkitOverflowScrolling: 'touch' }}>
          {/* Status filter */}
          <div className="flex rounded-lg border border-neutral-700 overflow-hidden shrink-0">
            {STATUS_FILTERS.map((s) => (
              <button
                key={s}
                onClick={() => setStatusFilter(s)}
                className={clsx(
                  'px-3 py-1.5 text-sm transition-colors whitespace-nowrap',
                  statusFilter === s
                    ? 'bg-neutral-700 text-neutral-100'
                    : 'bg-neutral-900 text-neutral-500 hover:text-neutral-300'
                )}
              >
                {s}
              </button>
            ))}
          </div>
          {/* View toggle */}
          <div className="flex rounded-lg border border-neutral-700 overflow-hidden shrink-0">
            <button
              onClick={() => setViewMode('cards')}
              className={clsx(
                'flex items-center gap-1.5 px-3 py-1.5 text-sm transition-colors whitespace-nowrap',
                viewMode === 'cards'
                  ? 'bg-neutral-700 text-neutral-100'
                  : 'bg-neutral-900 text-neutral-500 hover:text-neutral-300'
              )}
            >
              <LayoutGrid size={14} />
              Cards
            </button>
            <button
              onClick={() => setViewMode('table')}
              className={clsx(
                'flex items-center gap-1.5 px-3 py-1.5 text-sm transition-colors whitespace-nowrap',
                viewMode === 'table'
                  ? 'bg-neutral-700 text-neutral-100'
                  : 'bg-neutral-900 text-neutral-500 hover:text-neutral-300'
              )}
            >
              <List size={14} />
              Table
            </button>
            <button
              onClick={() => setViewMode('topology')}
              className={clsx(
                'flex items-center gap-1.5 px-3 py-1.5 text-sm transition-colors whitespace-nowrap',
                viewMode === 'topology'
                  ? 'bg-neutral-700 text-neutral-100'
                  : 'bg-neutral-900 text-neutral-500 hover:text-neutral-300'
              )}
            >
              <GitBranch size={14} />
              Topology
            </button>
          </div>
          {/* Tier filter */}
          <select
            value={tierFilter}
            onChange={(e) => setTierFilter(e.target.value)}
            className="bg-neutral-900 border border-neutral-700 text-neutral-300 text-sm rounded-lg px-3 py-1.5 focus:outline-none focus:border-neutral-500 shrink-0"
          >
            {TIERS.map((t) => (
              <option key={t} value={t}>{t === 'All' ? 'All Tiers' : t === 'For You' ? 'For You' : t === 'Recent' ? 'Recent' : t}</option>
            ))}
          </select>
          {/* Client filter */}
          {clients.length > 1 && (
            <select
              value={clientFilter}
              onChange={(e) => setClientFilter(e.target.value)}
              className="bg-neutral-900 border border-neutral-700 text-neutral-300 text-sm rounded-lg px-3 py-1.5 focus:outline-none focus:border-neutral-500 shrink-0"
            >
              {clients.map((c) => (
                <option key={c} value={c}>{c === 'All' ? 'All Clients' : c}</option>
              ))}
            </select>
          )}
          {/* Machine filter */}
          {machines.length > 1 && (
            <select
              value={machineFilter}
              onChange={(e) => setMachineFilter(e.target.value)}
              className="bg-neutral-900 border border-neutral-700 text-neutral-300 text-sm rounded-lg px-3 py-1.5 focus:outline-none focus:border-neutral-500 shrink-0"
            >
              {machines.map((m) => (
                <option key={m} value={m}>{m === 'All' ? 'All Machines' : m}</option>
              ))}
            </select>
          )}
        </div>
      </div>

      {/* Health summary bar */}
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-2 h-2 rounded-full bg-green-500" />
          <span className="text-neutral-300">{healthy} healthy</span>
        </span>
        {sleeping > 0 && (
          <span className="flex items-center gap-1.5">
            <span className="inline-block w-2 h-2 rounded-full bg-amber-500" />
            <span className="text-neutral-300">{sleeping} sleeping (Mac)</span>
          </span>
        )}
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-2 h-2 rounded-full bg-yellow-500" />
          <span className="text-neutral-300">{stale} stale</span>
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-2 h-2 rounded-full bg-red-500" />
          <span className="text-neutral-300">{down} down</span>
        </span>
      </div>

      {/* Card view */}
      {viewMode === 'cards' && (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {sorted.map((agent: any) => {
            const hasActiveContext = tierFilter === 'For You' &&
              (adaptiveScores?.find(s => s.agent_id === agent.id)?.signals?.active_context ?? 0) > 0.5;
            return (
              <div
                key={agent.id}
                id={`agent-${agent.id}`}
                className={clsx(
                  'relative',
                  hasActiveContext && 'before:absolute before:left-0 before:top-0 before:bottom-0 before:w-[3px] before:bg-emerald-500 before:rounded-l before:z-10'
                )}
              >
                <AgentCard
                  agent={agent}
                  onSpawn={(id) => spawnMutation.mutate(id)}
                  onKill={(id) => killMutation.mutate(id)}
                  spawning={pendingSpawn.has(agent.id)}
                  killing={pendingKill.has(agent.id)}
                />
              </div>
            );
          })}
          {sorted.length === 0 && (
            agents.length === 0 ? (
              <div className="col-span-3 text-center py-12">
                <p className="text-neutral-300 font-medium">No agents yet</p>
                <p className="text-sm text-neutral-500 mt-1 mb-4">Create one here, or run <code className="text-neutral-400">orchestra agent create &lt;name&gt;</code> in a terminal.</p>
                <button
                  onClick={() => setShowNewAgent(true)}
                  className="inline-flex items-center gap-2 px-4 py-2 min-h-[44px] text-sm rounded-lg bg-blue-600 text-white hover:bg-blue-500 transition-colors"
                >
                  <Plus size={16} />
                  New agent
                </button>
              </div>
            ) : (
              <p className="col-span-3 text-neutral-600 text-center py-8">No agents match this filter</p>
            )
          )}
        </div>
      )}

      {/* Table view */}
      {viewMode === 'table' && (
        <div className="rounded-xl border border-neutral-800 bg-neutral-900 overflow-x-auto">
          <table className="w-full text-sm min-w-[600px]">
            <thead>
              <tr className="text-neutral-500 text-xs uppercase tracking-wider border-b border-neutral-800">
                <th className="text-left px-4 py-3 font-medium">Agent</th>
                <th className="text-left px-4 py-3 font-medium">Tier</th>
                <th className="text-left px-4 py-3 font-medium">Machine</th>
                <th className="text-left px-4 py-3 font-medium">Status</th>
                <th className="text-left px-4 py-3 font-medium">Inbox</th>
                <th className="text-left px-4 py-3 font-medium">Current Task</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((agent: any) => {
                const isSelected = selectedAgent === agent.id;
                return (
                  <AgentRow
                    key={agent.id}
                    agent={agent}
                    isSelected={isSelected}
                    onClick={() => setSelectedAgent(isSelected ? null : agent.id)}
                  />
                );
              })}
              {sorted.length === 0 && (
                <tr><td colSpan={6} className="px-4 py-8 text-neutral-600 text-center">No agents found</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* Topology view */}
      {viewMode === 'topology' && (
        <div className="rounded-xl border border-neutral-800 bg-neutral-900 p-4 overflow-x-auto">
          <TopologyDiagram agents={agents} />
        </div>
      )}
    </div>
  );
}

function AgentRow({ agent, isSelected, onClick }: { agent: any; isSelected: boolean; onClick: () => void }) {
  const taskText = agent.current_task || agent.state || '—';
  const truncatedTask = taskText.length > 40 ? taskText.slice(0, 40) + '...' : taskText;

  return (
    <>
      <tr
        onClick={onClick}
        className={clsx(
          'border-t border-neutral-800/50 cursor-pointer transition-colors',
          isSelected ? 'bg-neutral-800/50' : 'hover:bg-neutral-800/30'
        )}
      >
        <td className="px-4 py-2.5 flex items-center gap-2">
          <StatusDot status={agent.alive ? 'running' : 'stopped'} />
          <span className="text-neutral-100 font-medium">{agent.name}</span>
          <GenChip generation={agent.generation} />
        </td>
        <td className="px-4 py-2.5"><TierBadge tier={agent.tier} /></td>
        <td className="px-4 py-2.5 text-neutral-400">{agent.machine || '—'}</td>
        <td className="px-4 py-2.5">
          <span className={clsx('text-xs font-medium', agent.alive ? 'text-green-400' : 'text-red-400')}>
            {agent.alive ? 'online' : 'stopped'}
          </span>
        </td>
        <td className="px-4 py-2.5 text-neutral-400">{agent.inbox_count ?? 0}</td>
        <td className="px-4 py-2.5 text-neutral-500">{truncatedTask}</td>
      </tr>
      {isSelected && (
        <tr className="border-t border-neutral-800/30">
          <td colSpan={6} className="px-4 py-4 bg-neutral-800/20">
            <AgentDetail agent={agent} />
          </td>
        </tr>
      )}
    </>
  );
}

function AgentDetail({ agent }: { agent: any }) {
  const fields = [
    { label: 'System Prompt', value: agent.system_prompt || agent.system_prompt_path },
    { label: 'Memory Scope', value: agent.memory_scope, isArray: true },
    { label: 'Working Directory', value: agent.working_dir || agent.cwd },
    { label: 'Parent Agent', value: agent.parent },
    { label: 'Can Spawn', value: agent.can_spawn, isArray: true },
    { label: 'Always On', value: agent.always_on != null ? String(agent.always_on) : undefined },
    { label: 'Spawned At', value: agent.spawned_at ? new Date(agent.spawned_at).toLocaleString() : undefined },
    { label: 'Last Message', value: agent.last_message ? new Date(agent.last_message).toLocaleString() : undefined },
  ];

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-8 gap-y-2 text-sm">
      {fields.map(({ label, value, isArray }) => {
        if (value == null && !isArray) return null;
        let display: string;
        if (isArray && Array.isArray(value)) {
          display = value.length > 0 ? value.join(', ') : '—';
        } else {
          display = value || '—';
        }
        return (
          <div key={label} className="flex gap-2">
            <span className="text-neutral-500 shrink-0">{label}:</span>
            <span className="text-neutral-300 break-all">{display}</span>
          </div>
        );
      })}
    </div>
  );
}
