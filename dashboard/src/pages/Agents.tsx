import { useState, useMemo, useEffect, useRef } from 'react';
import { clsx } from 'clsx';
import { LayoutGrid, List, GitBranch, Plus } from 'lucide-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useAgents } from '../hooks/useAgents';
import { useSystem } from '../hooks/useSystem';
import { useUser, canSeeAgent } from '../hooks/useUser';
import { spawnAgent, killAgent, getAdaptiveAgents, fetchPairCounts, fetchRecentMessages, fetchProjects } from '../lib/api';
import { AgentsSummaryStrip } from '../components/AgentsSummaryStrip';
import { AgentDetailPanel } from '../components/AgentDetailPanel';
import { ConversationPanel } from '../components/ConversationPanel';
import { FleetTicker } from '../components/FleetTicker';
import { TimeBar } from '../components/TimeBar';
import { SearchResults } from '../components/SearchResults';
import { searchFleet } from '../lib/agentSearch';
import { useOrchestraStore } from '../stores/useOrchestraStore';
import { newlyArrived, type TrafficMessage } from '../lib/fleetTraffic';
import { StatusDot } from '../components/StatusDot';
import { TierBadge } from '../components/TierBadge';
import { AgentCard } from '../components/AgentCard';
import { TopologyDiagram } from '../components/TopologyDiagram';
import { getRecentAgents, loadAndMergeRecentAgents, type RecentAgent } from '../lib/user-actions';
import { GenChip } from '../components/GenChip';
import NewAgentModal from '../components/NewAgentModal';

const TIERS = ['For You', 'Recent', 'All', 'T0', 'T1', 'T2', 'T3'] as const;
// Spec §3 names these All / Active / Down. 'Dead' was the pre-existing label; review's
// browser pass flagged the mismatch. The FILTER VALUE is what the predicate below compares,
// so renaming the label means renaming the value — 'Down' is now the not-alive branch.
const STATUS_FILTERS = ['All', 'Active', 'Down'] as const;
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
  // 2D Agents View spec §2/§3 and build-order step 1: nothing important is hidden.
  // Defaulting to 'Active' meant a down agent was invisible until you thought to
  // look for it, which is the opposite of what this page is for. Default is All.
  const [statusFilter, setStatusFilter] = useState<string>('All');
  const [clientFilter, setClientFilter] = useState<string>('All');
  const [machineFilter, setMachineFilter] = useState<string>('All');
  const [recentAgents, setRecentAgents] = useState<RecentAgent[]>(getRecentAgents());
  // Spec §9: selection, moment, window and search live in ONE store rather than in this
  // component, so every surface reads the same values. §3's one-panel-at-a-time rule is
  // enforced in the store's setters — openAgentPanel and openConversation clear each other
  // there, so it cannot be violated by a caller that forgets.
  const search = useOrchestraStore((st) => st.search);
  const setSearch = useOrchestraStore((st) => st.setSearch);
  const highlight = useOrchestraStore((st) => st.highlight);
  const setHighlight = useOrchestraStore((st) => st.setHighlight);
  const windowHours = useOrchestraStore((st) => st.windowHours);
  const setWindowHours = useOrchestraStore((st) => st.setWindowHours);
  const asof = useOrchestraStore((st) => st.asof);
  const setAsof = useOrchestraStore((st) => st.setAsof);
  const panelAgentId = useOrchestraStore((st) => st.selectedAgentId);
  const selectedConnection = useOrchestraStore((st) => st.selectedConnection);
  const openAgentPanel = useOrchestraStore((st) => st.openAgentPanel);
  const openConversation = useOrchestraStore((st) => st.openConversation);
  const closePanel = useOrchestraStore((st) => st.closePanel);

  // On mount: merge localStorage with server-persisted recents
  useEffect(() => {
    loadAndMergeRecentAgents().then(setRecentAgents);
  }, []);

  const { data: system } = useSystem();
  // Canonical per-pair message counts (spec §16): the ONE source for every count and
  // thickness on a connection line and for the strip's message/connection numbers.
  const { data: pairData } = useQuery({
    // asof is in the key on purpose: without it, scrubbing back in time would be served the
    // cached counts for "now" and the scrubber would look broken in the most confusing
    // possible way — plausible numbers for the wrong moment.
    queryKey: ['pair-counts', windowHours, asof],
    queryFn: () => fetchPairCounts(windowHours, asof),
    // Polling a frozen past moment is pointless work and would also make a scrubbed view
    // twitch as if it were live.
    refetchInterval: asof ? false : 15_000,
  });

  // Step 7. Recent mail drives BOTH the ticker and the travelling dots, from the same
  // canonical table as the line counts — so a dot, a ticker row and a line count are three
  // views of one event rather than three stores disagreeing.
  const { data: recent } = useQuery({
    queryKey: ['recent-messages'],
    queryFn: () => fetchRecentMessages(40),
    // Frozen while scrubbed: a live ticker beside a past moment is showing stale as fresh,
    // which §12 rules out by name.
    refetchInterval: asof ? false : 8_000,
    enabled: !asof,
  });
  // useMemo so the identity is stable and the effect below can depend on the ARRAY rather
  // than on the query result it was derived from — the two are equivalent today, but the
  // lint rule is right that depending on the wrong one is how a stale-closure bug starts.
  const recentMessages = useMemo<TrafficMessage[]>(() => recent?.messages ?? [], [recent]);
  // A dot is emitted only for an id that was absent from the previous poll, and it is cleared
  // once its one-shot animation has played. Never a loop: spec §2 says if a dot moves, a
  // message is actually moving, so idle traffic must render idle lines.
  const seenIdsRef = useRef<Set<string>>(new Set());
  const [dots, setDots] = useState<TrafficMessage[]>([]);
  const [freshIds, setFreshIds] = useState<Set<string>>(new Set());
  useEffect(() => {
    if (recentMessages.length === 0) return;
    const fresh = newlyArrived(seenIdsRef.current, recentMessages);
    seenIdsRef.current = new Set(recentMessages.map((m) => m.id));
    if (fresh.length === 0) return;
    setDots(fresh);
    setFreshIds(new Set(fresh.map((m) => m.id)));
    // 1100ms > the 900ms keyframe, so the dot unmounts after it finishes rather than mid-flight.
    const t = setTimeout(() => { setDots([]); setFreshIds(new Set()); }, 1100);
    return () => clearTimeout(t);
  }, [recentMessages]);

  // Repos for search. Empty on this install (the projects knowledge file has no entries yet),
  // which is an honest empty state rather than a reason to synthesise one.
  const { data: projects } = useQuery({
    queryKey: ['projects'],
    queryFn: () => fetchProjects() as Promise<{ projects: { slug: string; name?: string; repo?: string | null; agents?: { id: string }[] }[] }>,
    staleTime: 60_000,
  });

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

  // Step 10. Same `agents` array the rest of the page uses, so a search hit and a card can
  // never describe different fleets. Repos come from /api/projects; prompts are derived from
  // the agents' own system_prompt paths rather than from a new store (§16).
  const searchResults = useMemo(
    () => searchFleet(search, { agents, repos: projects?.projects ?? [] }),
    [search, agents, projects],
  );

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
  const statusFiltered = statusFilter === 'All'
    ? tierFiltered
    : statusFilter === 'Active'
      ? tierFiltered.filter((a: any) => a.alive)
      : tierFiltered.filter((a: any) => !a.alive);

  // Spec §8 in full — matching repos, prompts and files, and grouping results by type — is
  // build-order step 10. This is the agents-only half, which is step 8's first line ("search
  // matches agents first"). Deliberately shipped rather than leaving the strip's search box
  // inert: a control that does nothing is worse than a control that does less than the spec.
  const q = search.trim().toLowerCase();
  const filtered = q
    ? statusFiltered.filter((a: { id?: string; name?: string; role?: string; tier?: string }) =>
        [a.id, a.name, a.role, a.tier].some((v) => String(v ?? '').toLowerCase().includes(q)))
    : statusFiltered;

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

  // Summary-strip numbers (spec §3). All of them derive from the two canonical sources —
  // /api/agents for liveness, /api/messages/pair-counts for traffic — so the strip cannot
  // disagree with the connection lines or with the status-filter counts below it.
  const pairs = pairData?.pairs ?? [];
  const messagesInWindow = pairs.reduce((n, p) => n + p.count, 0);
  const activeConnections = pairs.filter((p) => p.count > 0).length;
  const busiest = pairs.length ? pairs[0] : null;   // endpoint already orders by count desc
  // The VPS is the always-on machine this view is about; hostname comes from /api/system,
  // which is fine for a LABEL — §16 only forbids using that route's agent COUNTS.
  const machineName = system?.machines?.vps?.hostname ?? 'unknown host';
  const machineLive = (system?.machines?.vps?.status ?? 'online') === 'online';

  // Peers an agent actually exchanged messages with in the window, busiest first — `pairs`
  // already arrives ordered by count, so no second sort. This is what makes spec §5's
  // "connection count (click to list)" a list rather than a bare number.
  // `onGraph` distinguishes a peer that is drawn in the tree from one that is not (telegram,
  // operator, the beats). Both are real correspondents; only the first is a node. Graph peers
  // sort first so the list leads with what the diagram shows.
  const graphIds = new Set<string>(agents.map((a: { id: string }) => a.id));
  const peersOf = (id: string): { id: string; onGraph: boolean }[] =>
    pairs
      .filter((p) => p.a === id || p.b === id)
      .map((p) => { const other = p.a === id ? p.b : p.a; return { id: other, onGraph: graphIds.has(other) }; })
      .sort((x, y) => Number(y.onGraph) - Number(x.onGraph));
  const panelAgent = panelAgentId ? agents.find((a: { id: string }) => a.id === panelAgentId) : null;

  // Health summary
  const healthy = agents.filter((a: any) => a.alive).length;
  const sleeping = agents.filter((a: any) => a.machine === 'mac' && (a.machine_status === 'sleeping' || a.machine_status === 'offline')).length;
  const stale = agents.filter((a: any) => !a.alive && a.always_on).length;
  const down = agents.filter((a: any) => !a.alive && !a.always_on).length;

  return (
    <div className="p-6 space-y-6">
      {/* Header. Spec §3 wants the top bar fixed and never covered — sticky rather than
          position:fixed so it stays inside this page's scroll container and cannot end up
          floating over another route's content. The opaque background matters: without it
          the cards scroll visibly through the numbers. */}
      <div className="sticky top-0 z-20 -mx-6 px-6 pt-1 pb-3 bg-neutral-950 border-b border-neutral-900 space-y-3">
        <div className="flex flex-col sm:flex-row items-stretch sm:items-start justify-between gap-3 sm:gap-4">
          <div className="min-w-0 flex-1">
            <AgentsSummaryStrip
              machineName={machineName}
              machineLive={machineLive}
              agentsUp={healthy}
              agentsTotal={agents.length}
              messages24h={messagesInWindow}
              activeConnections={activeConnections}
              busiest={busiest}
              windowHours={windowHours}
              search={search}
              onSearchChange={setSearch}
              onSelectBusiest={busiest ? () => openConversation(busiest.a, busiest.b) : undefined}
            />
            <p className="text-sm text-neutral-500 mt-0.5">
              showing {sorted.length} of {agents.length}
            </p>
            {search.trim() && (
              <div className="mt-2">
                <SearchResults
                  results={searchResults}
                  onPick={(hit) => {
                    if (hit.kind === 'agent') {
                      // §8: picking an agent selects it AND opens its panel.
                      setHighlight(null);
                      setSearch('');
                      openAgentPanel(hit.id);
                    } else {
                      // §8: picking a repo or prompt HIGHLIGHTS the agents that use it. It
                      // opens nothing — that is the difference between the two verbs, and
                      // conflating them would make a repo pick look like an agent pick.
                      closePanel();
                      setSearch('');
                      setHighlight({ label: hit.label, agentIds: hit.agentIds ?? [] });
                    }
                  }}
                />
              </div>
            )}
            {highlight && (
              <div className="mt-2 flex items-center gap-2 text-xs">
                <span className="px-2 py-0.5 rounded-lg border border-sky-800 bg-sky-950/40 text-sky-200">
                  {highlight.label} · {highlight.agentIds.length} agent{highlight.agentIds.length === 1 ? '' : 's'}
                </span>
                <button
                  type="button"
                  onClick={() => setHighlight(null)}
                  className="text-neutral-500 hover:text-neutral-200 underline decoration-neutral-700"
                >
                  clear
                </button>
              </div>
            )}
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

      {/* Topology view. Spec §3: the panel sits beside the graph and the graph stays visible.
          Stacked full-width under lg rather than a true overlay sheet — §12's small-screen
          bullet asks for full-screen, and full-width-stacked is that shape without a second
          modal implementation; a real sheet can replace it when someone uses this on a phone. */}
      {viewMode === 'topology' && (
        <div className="flex flex-col lg:flex-row gap-4 items-stretch">
          <div className="flex-1 min-w-0 rounded-xl border border-neutral-800 bg-neutral-900 p-4 overflow-x-auto">
            <TopologyDiagram
              agents={agents}
              pairs={pairs}
              windowHours={windowHours}
              onSelectConnection={openConversation}
              onSelectAgent={openAgentPanel}
              selectedAgentId={panelAgentId}
              onClearSelection={() => { closePanel(); setHighlight(null); }}
              brightIds={highlight?.agentIds ?? null}
              dots={dots}
            />
            {/* Bottom live ticker (spec §3). The time scrubber that shares this row is
                step 9 and is not built. */}
            <div className="border-t border-neutral-800 mt-2">
              <TimeBar
                windowHours={windowHours}
                onWindowChange={setWindowHours}
                asof={asof}
                onAsofChange={setAsof}
              />
              {asof ? (
                // §12: never show stale as fresh. The ticker is a live feed by definition, so
                // while a past moment is being viewed it says what it is instead of quietly
                // rendering now's traffic under a scrubbed graph.
                <div className="text-xs text-amber-300/80 py-2">
                  Viewing a past moment — live ticker paused. Press Live to resume.
                </div>
              ) : (
                <FleetTicker
                  messages={recentMessages}
                  freshIds={freshIds}
                  onSelectConnection={openConversation}
                />
              )}
            </div>
          </div>
          {(panelAgent || selectedConnection) && (
            <aside className="w-full lg:w-[380px] shrink-0 lg:max-h-[75vh]">
              {selectedConnection ? (
                <ConversationPanel
                  a={selectedConnection[0]}
                  b={selectedConnection[1]}
                  onOpenAgent={openAgentPanel}
                  onClose={closePanel}
                  windowHours={windowHours}
                  asof={asof}
                />
              ) : panelAgent ? (
                <AgentDetailPanel
                  agent={panelAgent}
                  connectionCount={peersOf(panelAgent.id).length}
                  connections={peersOf(panelAgent.id)}
                  onOpenConversation={(peer) => openConversation(panelAgent.id, peer)}
                  onClose={closePanel}
                />
              ) : null}
            </aside>
          )}
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
