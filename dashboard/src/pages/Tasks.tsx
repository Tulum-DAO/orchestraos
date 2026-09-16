import { useState, useMemo, useRef, useCallback } from 'react';
import { clsx } from 'clsx';
import { Plus, ChevronDown, ChevronRight, X, Send, LayoutDashboard, FolderOpen } from 'lucide-react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { PriorityBadge } from '../components/PriorityBadge';

// ── API ─────────────────────────────────────────────────────────

const V2 = '/api/v2/tasks';

async function fetchV2Tasks(params?: string) {
  const res = await fetch(`${V2}${params ? '?' + params : ''}`);
  if (!res.ok) throw new Error(`Tasks: ${res.status}`);
  return res.json();
}

async function fetchPipelines() {
  const res = await fetch('/api/pipelines');
  if (!res.ok) throw new Error(`Pipelines: ${res.status}`);
  return res.json();
}

async function fetchAgents() {
  const res = await fetch('/api/agents');
  if (!res.ok) throw new Error(`Agents: ${res.status}`);
  return res.json();
}

async function patchTask(id: string, body: any) {
  const res = await fetch(`${V2}/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`Patch: ${res.status}`);
  return res.json();
}

async function createTask(body: any) {
  const res = await fetch(V2, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`Create: ${res.status}`);
  return res.json();
}

// ── Constants ───────────────────────────────────────────────────

const DEFAULT_COLUMNS = [
  { key: 'pending', label: 'To Do', dot: 'bg-neutral-500', statuses: ['pending'] },
  { key: 'in_progress', label: 'Doing', dot: 'bg-blue-500', statuses: ['in_progress', 'review'] },
  { key: 'blocked', label: 'Blocked', dot: 'bg-amber-500', statuses: ['blocked'] },
  { key: 'completed', label: 'Done', dot: 'bg-green-500', statuses: ['completed'] },
];

const PRIORITY_ORDER: Record<string, number> = { critical: 0, high: 1, medium: 2, low: 3 };

const STATUS_FLOW: Record<string, string> = {
  pending: 'in_progress',
  in_progress: 'completed',
  blocked: 'in_progress',
  completed: 'pending',
  review: 'completed',
};

const STATUS_DOT_COLOR: Record<string, string> = {
  running: 'bg-green-500',
  alive: 'bg-green-500',
  active: 'bg-green-500',
  stopped: 'bg-neutral-500',
  spawning: 'bg-yellow-500',
  unknown: 'bg-neutral-600',
};

function relativeTime(ts: string): string {
  if (!ts) return '';
  const diff = Date.now() - new Date(ts).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'now';
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h`;
  return `${Math.floor(hrs / 24)}d`;
}

function agentInitials(name: string): string {
  return name
    .replace(/-/g, ' ')
    .split(' ')
    .filter(Boolean)
    .slice(0, 2)
    .map(w => w[0].toUpperCase())
    .join('');
}

// ── Component ───────────────────────────────────────────────────

export default function Tasks() {
  const queryClient = useQueryClient();
  const [projectFilter, setProjectFilter] = useState('all');
  const [clientFilter, setClientFilter] = useState('all');
  const [priorityFilter, setPriorityFilter] = useState('all');
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [addText, setAddText] = useState('');
  const [addType, setAddType] = useState('agent');
  const [typeFilter, setTypeFilter] = useState('all');
  const [sourceFilter, setSourceFilter] = useState('all');
  const [selectedPipeline, setSelectedPipeline] = useState('default');
  const [viewMode, setViewMode] = useState<'board' | 'project'>('board');
  const [needsMeActive, setNeedsMeActive] = useState(false);
  const [collapsedProjects, setCollapsedProjects] = useState<Set<string>>(new Set());
  const addRef = useRef<HTMLInputElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Fetch from v2 API
  const { data, isLoading } = useQuery({
    queryKey: ['tasks-v2'],
    queryFn: () => fetchV2Tasks('parent=null&limit=200'),
    refetchInterval: 10_000,
  });

  // Fetch pipelines for dynamic kanban columns
  const { data: pipelineData } = useQuery({
    queryKey: ['pipelines'],
    queryFn: fetchPipelines,
    staleTime: 60_000,
  });

  // Fetch agents for alive status
  const { data: agentsData } = useQuery({
    queryKey: ['agents'],
    queryFn: fetchAgents,
    staleTime: 15_000,
    refetchInterval: 30_000,
  });

  // Build agent status map: agent id/name → status string
  const agentStatusMap = useMemo(() => {
    const map: Record<string, string> = {};
    const agents: any[] = agentsData?.agents ?? agentsData ?? [];
    for (const a of agents) {
      const key = a.id || a.name || '';
      if (key) map[key] = a.status || (a.alive ? 'running' : 'stopped');
    }
    return map;
  }, [agentsData]);

  // Derive columns from selected pipeline
  const COLUMNS = useMemo(() => {
    if (!pipelineData?.pipelines?.length) return DEFAULT_COLUMNS;
    const pipeline = pipelineData.pipelines.find((p: any) => p.id === selectedPipeline)
      || pipelineData.pipelines.find((p: any) => p.is_default)
      || pipelineData.pipelines[0];
    if (!pipeline?.stages?.length) return DEFAULT_COLUMNS;
    return pipeline.stages.map((s: any) => ({
      key: s.key,
      label: s.label,
      dot: s.dot_color || 'bg-neutral-500',
      statuses: [s.key],
    }));
  }, [pipelineData, selectedPipeline]);

  const patchMutation = useMutation({
    mutationFn: ({ id, body }: { id: string; body: any }) => patchTask(id, body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['tasks-v2'] }),
  });

  const createMutation = useMutation({
    mutationFn: (body: any) => createTask(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tasks-v2'] });
      setAddText('');
      setAddOpen(false);
    },
  });

  const allTasks: any[] = data?.tasks ?? [];
  const clients: string[] = data?.clients ?? [];
  const projects: string[] = data?.projects ?? [];
  const summary = data?.summary ?? {};

  // "Needs Me" count — always computed from allTasks (ignores other filters so count is always visible)
  const needsMeCount = useMemo(() =>
    allTasks.filter((t: any) =>
      t.status === 'blocked' ||
      (t.assigned_to || '').toLowerCase().includes('operator') ||
      (t.type === 'approval' && t.status === 'pending')
    ).length,
  [allTasks]);

  // Filter
  const filtered = useMemo(() => {
    return allTasks.filter((t: any) => {
      if (needsMeActive) {
        const isNeeded =
          t.status === 'blocked' ||
          (t.assigned_to || '').toLowerCase().includes('operator') ||
          (t.type === 'approval' && t.status === 'pending');
        if (!isNeeded) return false;
      }
      if (projectFilter !== 'all' && t.project_id !== projectFilter) return false;
      if (clientFilter !== 'all' && t.client !== clientFilter) return false;
      if (priorityFilter !== 'all' && t.priority !== priorityFilter) return false;
      if (typeFilter !== 'all' && (t.type || 'agent') !== typeFilter) return false;
      if (sourceFilter !== 'all') {
        if (sourceFilter === 'user' && t.created_by_type === 'agent') return false;
        else if (sourceFilter === 'agent' && t.created_by_type !== 'agent') return false;
        else if (!['all', 'user', 'agent'].includes(sourceFilter) && t.source !== sourceFilter) return false;
      }
      return true;
    });
  }, [allTasks, projectFilter, clientFilter, priorityFilter, typeFilter, sourceFilter, needsMeActive]);

  // Bucket into kanban columns
  const columns = useMemo(() => {
    const buckets: Record<string, any[]> = {};
    for (const col of COLUMNS) buckets[col.key] = [];

    for (const task of filtered) {
      const s = task.status || 'pending';
      const col = COLUMNS.find((c: any) => c.statuses.includes(s));
      if (col) buckets[col.key].push(task);
    }

    // Sort each by priority then date
    for (const key of Object.keys(buckets)) {
      buckets[key].sort((a: any, b: any) => {
        const pa = PRIORITY_ORDER[a.priority] ?? 9;
        const pb = PRIORITY_ORDER[b.priority] ?? 9;
        if (pa !== pb) return pa - pb;
        return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
      });
    }

    // Cap completed at 15
    if (buckets.completed && buckets.completed.length > 15) buckets.completed = buckets.completed.slice(0, 15);

    return buckets;
  }, [filtered, COLUMNS]);

  // Group by project for "By Project" view
  const byProject = useMemo(() => {
    const groups: Record<string, any[]> = {};
    filtered.forEach((t: any) => {
      const project = t.project_id || t.client || 'Unassigned';
      (groups[project] ||= []).push(t);
    });
    // Sort each group by priority then date
    for (const key of Object.keys(groups)) {
      groups[key].sort((a: any, b: any) => {
        const pa = PRIORITY_ORDER[a.priority] ?? 9;
        const pb = PRIORITY_ORDER[b.priority] ?? 9;
        if (pa !== pb) return pa - pb;
        return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
      });
    }
    return Object.entries(groups).sort(([a], [b]) => a.localeCompare(b));
  }, [filtered]);

  const toggleProjectCollapse = useCallback((project: string) => {
    setCollapsedProjects(prev => {
      const next = new Set(prev);
      if (next.has(project)) next.delete(project);
      else next.add(project);
      return next;
    });
  }, []);

  const advanceStatus = useCallback((task: any) => {
    const next = STATUS_FLOW[task.status];
    if (next) patchMutation.mutate({ id: task.id, body: { status: next } });
  }, [patchMutation]);

  const handleAddSubmit = () => {
    const title = addText.trim();
    if (!title) return;
    createMutation.mutate({ title, type: addType });
  };

  if (isLoading) return <p className="text-neutral-500 p-8">Loading...</p>;

  return (
    <div className="p-4 md:p-6 flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div>
          <h1 className="text-2xl font-bold text-white">Tasks</h1>
          <p className="text-xs text-neutral-500 mt-0.5">
            {summary.pending || 0} pending · {summary.in_progress || 0} active · {summary.completed || 0} done
          </p>
        </div>
        <button
          onClick={() => { setAddOpen(true); setTimeout(() => addRef.current?.focus(), 100); }}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-violet-600 text-white text-sm font-medium rounded-lg hover:bg-violet-500 transition-colors"
        >
          <Plus size={16} />
          <span className="hidden sm:inline">Add Task</span>
        </button>
      </div>

      {/* Add task input */}
      {addOpen && (
        <div className="flex flex-wrap gap-2 mb-3">
          <input
            ref={addRef}
            value={addText}
            onChange={(e) => setAddText(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') handleAddSubmit(); if (e.key === 'Escape') setAddOpen(false); }}
            placeholder="What needs to be done?"
            className="flex-1 min-w-[200px] bg-neutral-800 border border-neutral-700 rounded-lg px-3 py-2 text-sm text-neutral-200 placeholder-neutral-600 focus:outline-none focus:border-violet-500"
          />
          <select value={addType} onChange={(e) => setAddType(e.target.value)}
            className="bg-neutral-800 text-neutral-300 text-xs rounded-lg px-2 py-2 border border-neutral-700">
            <option value="agent">Agent</option>
            <option value="human">Human</option>
            <option value="call">Call</option>
            <option value="external">External</option>
          </select>
          <button
            onClick={handleAddSubmit}
            disabled={!addText.trim() || createMutation.isPending}
            className="px-3 py-2 bg-violet-600 text-white rounded-lg hover:bg-violet-500 disabled:opacity-50 transition-colors"
          >
            <Send size={16} />
          </button>
          <button onClick={() => setAddOpen(false)} className="p-2 text-neutral-500 hover:text-neutral-300">
            <X size={16} />
          </button>
        </div>
      )}

      {/* Filters + Needs Me */}
      <div className="flex flex-wrap gap-2 mb-3">
        {/* Needs Me pill */}
        <button
          onClick={() => setNeedsMeActive(v => !v)}
          className={clsx(
            'flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium border transition-colors',
            needsMeActive
              ? 'bg-amber-500/20 border-amber-500/50 text-amber-300'
              : 'bg-neutral-800 border-neutral-700 text-neutral-400 hover:text-neutral-200'
          )}
        >
          Needs Me
          <span className={clsx(
            'px-1.5 py-0.5 rounded-full text-[10px] font-bold',
            needsMeCount > 0 ? 'bg-amber-500/30 text-amber-300' : 'bg-neutral-700 text-neutral-500'
          )}>
            {needsMeCount}
          </span>
        </button>

        {projects.length > 0 && (
          <select value={projectFilter} onChange={(e) => setProjectFilter(e.target.value)}
            className="bg-neutral-800 text-neutral-300 text-xs rounded-lg px-2 py-1.5 border border-neutral-700 focus:outline-none">
            <option value="all">All projects</option>
            {projects.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
        )}
        {clients.length > 0 && (
          <select value={clientFilter} onChange={(e) => setClientFilter(e.target.value)}
            className="bg-neutral-800 text-neutral-300 text-xs rounded-lg px-2 py-1.5 border border-neutral-700 focus:outline-none">
            <option value="all">All clients</option>
            {clients.map(c => <option key={c} value={c}>{c}</option>)}
          </select>
        )}
        <select value={priorityFilter} onChange={(e) => setPriorityFilter(e.target.value)}
          className="bg-neutral-800 text-neutral-300 text-xs rounded-lg px-2 py-1.5 border border-neutral-700 focus:outline-none">
          <option value="all">All priorities</option>
          <option value="critical">Critical</option>
          <option value="high">High</option>
          <option value="medium">Medium</option>
          <option value="low">Low</option>
        </select>
        <select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}
          className="bg-neutral-800 text-neutral-300 text-xs rounded-lg px-2 py-1.5 border border-neutral-700 focus:outline-none">
          <option value="all">All types</option>
          <option value="agent">Agent</option>
          <option value="human">Human</option>
          <option value="call">Call</option>
          <option value="external">External</option>
        </select>
        <select value={sourceFilter} onChange={(e) => setSourceFilter(e.target.value)}
          className="bg-neutral-800 text-neutral-300 text-xs rounded-lg px-2 py-1.5 border border-neutral-700 focus:outline-none">
          <option value="all">All sources</option>
          <option value="user">User requests</option>
          <option value="agent">Agent-created</option>
          <option value="dashboard">Dashboard</option>
          <option value="telegram">Telegram</option>
          <option value="voice">Voice</option>
          <option value="inject">Inject</option>
        </select>
        {pipelineData?.pipelines?.length > 1 && (
          <select value={selectedPipeline} onChange={(e) => setSelectedPipeline(e.target.value)}
            className="bg-neutral-800 text-neutral-300 text-xs rounded-lg px-2 py-1.5 border border-neutral-700 focus:outline-none">
            {pipelineData.pipelines.map((p: any) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
        )}
      </div>

      {/* View toggle */}
      <div className="flex items-center gap-1 mb-3 p-1 bg-neutral-800/60 rounded-lg w-fit border border-neutral-700/50">
        <button
          onClick={() => setViewMode('board')}
          className={clsx(
            'flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors',
            viewMode === 'board'
              ? 'bg-neutral-700 text-white'
              : 'text-neutral-500 hover:text-neutral-300'
          )}
        >
          <LayoutDashboard size={13} />
          Board
        </button>
        <button
          onClick={() => setViewMode('project')}
          className={clsx(
            'flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors',
            viewMode === 'project'
              ? 'bg-neutral-700 text-white'
              : 'text-neutral-500 hover:text-neutral-300'
          )}
        >
          <FolderOpen size={13} />
          By Project
        </button>
      </div>

      {/* ── Board view (kanban) ─────────────────────────────────── */}
      {viewMode === 'board' && (
        <div
          ref={scrollRef}
          className="flex overflow-x-auto gap-3 flex-1 min-h-0 pb-16 snap-x snap-mandatory md:snap-none md:pb-2 md:grid"
          style={{ gridTemplateColumns: `repeat(${COLUMNS.length}, minmax(0, 1fr))`, WebkitOverflowScrolling: 'touch' }}
        >
          {COLUMNS.map((col: any) => {
            const tasks = columns[col.key] ?? [];
            return (
              <div
                key={col.key}
                className="flex flex-col min-h-0 min-w-[72vw] w-[72vw] md:w-auto md:min-w-0 snap-start shrink-0 md:shrink"
              >
                {/* Column header */}
                <div className="flex items-center gap-2 mb-2 px-1">
                  <span className={clsx('w-2 h-2 rounded-full', col.dot)} />
                  <span className="text-sm font-semibold text-neutral-300">{col.label}</span>
                  <span className="text-xs bg-neutral-800 text-neutral-500 px-1.5 py-0.5 rounded-full">{tasks.length}</span>
                </div>

                {/* Cards */}
                <div className="overflow-y-auto overflow-x-hidden flex-1 space-y-2 pr-1">
                  {tasks.length === 0 && (
                    <p className="text-neutral-700 text-xs px-2 py-4 text-center">No tasks</p>
                  )}
                  {tasks.map((task: any) => (
                    <TaskCard
                      key={task.id}
                      task={task}
                      isExpanded={expandedId === task.id}
                      onToggle={() => setExpandedId(expandedId === task.id ? null : task.id)}
                      onAdvance={() => advanceStatus(task)}
                      onPatch={(body: any) => patchMutation.mutate({ id: task.id, body })}
                      agentStatusMap={agentStatusMap}
                    />
                  ))}
                </div>
              </div>
            );
          })}
          {/* Spacer after last column so it snaps left-aligned like the others */}
          <div className="shrink-0 w-[28vw] md:hidden" aria-hidden />
        </div>
      )}

      {/* ── By Project view ─────────────────────────────────────── */}
      {viewMode === 'project' && (
        <div className="flex-1 min-h-0 overflow-y-auto space-y-3 pb-4">
          {byProject.length === 0 && (
            <p className="text-neutral-700 text-xs px-2 py-8 text-center">No tasks match the current filters</p>
          )}
          {byProject.map(([projectName, tasks]) => {
            const isCollapsed = collapsedProjects.has(projectName);
            const total = tasks.length;
            const done = tasks.filter((t: any) => t.status === 'completed').length;
            const pct = total > 0 ? Math.round((done / total) * 100) : 0;
            const hasBlocked = tasks.some((t: any) => t.status === 'blocked');

            return (
              <div key={projectName} className="border border-neutral-800 rounded-xl overflow-hidden">
                {/* Section header */}
                <button
                  onClick={() => toggleProjectCollapse(projectName)}
                  className="w-full flex items-center gap-3 px-4 py-3 bg-neutral-800/50 hover:bg-neutral-800 transition-colors text-left"
                >
                  {isCollapsed
                    ? <ChevronRight size={15} className="text-neutral-500 shrink-0" />
                    : <ChevronDown size={15} className="text-neutral-500 shrink-0" />
                  }
                  <FolderOpen size={14} className="text-violet-400 shrink-0" />
                  <span className="text-sm font-semibold text-neutral-200 flex-1 truncate">{projectName}</span>
                  {hasBlocked && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-400 font-medium mr-1">blocked</span>
                  )}
                  <span className="text-xs text-neutral-500 shrink-0 mr-2">{total} task{total !== 1 ? 's' : ''}</span>
                  {/* Progress bar */}
                  <div className="hidden sm:flex items-center gap-2 shrink-0">
                    <div className="w-20 h-1.5 bg-neutral-700 rounded-full overflow-hidden">
                      <div
                        className="h-full bg-green-500 rounded-full transition-all"
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                    <span className="text-[10px] text-neutral-500 w-7 text-right">{pct}%</span>
                  </div>
                </button>

                {/* Task list */}
                {!isCollapsed && (
                  <div className="divide-y divide-neutral-800/60">
                    {tasks.map((task: any) => (
                      <ProjectTaskRow
                        key={task.id}
                        task={task}
                        isExpanded={expandedId === task.id}
                        onToggle={() => setExpandedId(expandedId === task.id ? null : task.id)}
                        onAdvance={() => advanceStatus(task)}
                        onPatch={(body: any) => patchMutation.mutate({ id: task.id, body })}
                        agentStatusMap={agentStatusMap}
                      />
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── Agent Badge ─────────────────────────────────────────────────

function AgentBadge({ assignedTo, agentStatusMap }: { assignedTo: string; agentStatusMap: Record<string, string> }) {
  if (!assignedTo) return null;
  const status = agentStatusMap[assignedTo] || 'unknown';
  const dotColor = STATUS_DOT_COLOR[status] || STATUS_DOT_COLOR.unknown;
  const initials = agentInitials(assignedTo);

  return (
    <span className="inline-flex items-center gap-1 bg-neutral-800 border border-neutral-700 rounded px-1.5 py-0.5 text-[10px] text-neutral-400 font-medium max-w-[100px]">
      <span className={clsx('w-1.5 h-1.5 rounded-full shrink-0', dotColor)} />
      <span className="truncate" title={assignedTo}>{initials || assignedTo}</span>
    </span>
  );
}

// ── Task Card (kanban) ───────────────────────────────────────────

function TaskCard({ task, isExpanded, onToggle, onAdvance, onPatch, agentStatusMap }: {
  task: any;
  isExpanded: boolean;
  onToggle: () => void;
  onAdvance: () => void;
  onPatch: (body: any) => void;
  agentStatusMap: Record<string, string>;
}) {
  const nextStatus = STATUS_FLOW[task.status];
  const nextLabel: Record<string, string> = {
    in_progress: 'Start', completed: 'Done', pending: 'Reopen',
  };

  // Swipe detection for mobile status advance
  const touchStartX = useRef<number>(0);
  const handleTouchStart = (e: React.TouchEvent) => { touchStartX.current = e.touches[0].clientX; };
  const handleTouchEnd = (e: React.TouchEvent) => {
    const dx = e.changedTouches[0].clientX - touchStartX.current;
    if (dx > 80) onAdvance(); // swipe right → advance
  };

  return (
    <div
      className={clsx(
        'rounded-lg border p-3 transition-colors',
        task.status === 'blocked'
          ? 'border-l-2 border-l-amber-500 border-t-neutral-800 border-r-neutral-800 border-b-neutral-800 bg-neutral-900'
          : 'border-neutral-800 bg-neutral-900',
        task.priority === 'critical' && 'border-l-2 border-l-red-500'
      )}
      onTouchStart={handleTouchStart}
      onTouchEnd={handleTouchEnd}
    >
      {/* Title row */}
      <div className="flex items-start gap-2" onClick={onToggle}>
        <PriorityBadge priority={task.priority} />
        <span className={clsx(
          'text-sm font-medium leading-tight flex-1',
          task.status === 'completed' ? 'text-neutral-500 line-through' : 'text-white'
        )}>
          {task.title}
        </span>
        {isExpanded ? <ChevronDown size={14} className="text-neutral-600 shrink-0 mt-0.5" /> : <ChevronRight size={14} className="text-neutral-600 shrink-0 mt-0.5" />}
      </div>

      {/* Tags */}
      <div className="flex flex-wrap items-center gap-1.5 mt-1.5 text-[10px]">
        {task.type && task.type !== 'agent' && (
          <span className={clsx('px-1.5 py-0.5 rounded font-semibold uppercase tracking-wider',
            task.type === 'human' ? 'bg-cyan-500/15 text-cyan-400' :
            task.type === 'call' ? 'bg-pink-500/15 text-pink-400' :
            task.type === 'external' ? 'bg-amber-500/15 text-amber-400' :
            'bg-neutral-700 text-neutral-400'
          )}>
            {task.type}
          </span>
        )}
        {task.project_id && (
          <span className="bg-violet-500/15 text-violet-400 px-1.5 py-0.5 rounded font-medium">{task.project_id}</span>
        )}
        {task.client && task.client !== task.project_id && (
          <span className="bg-emerald-500/15 text-emerald-400 px-1.5 py-0.5 rounded font-medium">{task.client}</span>
        )}
      </div>

      {/* Footer: timestamp + agent badge */}
      <div className="mt-2 flex items-center justify-between">
        <span className="text-[10px] text-neutral-600">
          {relativeTime(task.created_at)} ago
        </span>
        {task.assigned_to && (
          <AgentBadge assignedTo={task.assigned_to} agentStatusMap={agentStatusMap} />
        )}
      </div>

      {/* Expanded detail */}
      {isExpanded && (
        <div className="mt-3 pt-3 border-t border-neutral-800 space-y-3">
          {task.description && (
            <p className="text-xs text-neutral-400 leading-relaxed">{task.description}</p>
          )}

          {/* Inline status/priority controls */}
          <div className="flex gap-2">
            <select
              value={task.status}
              onChange={(e) => onPatch({ status: e.target.value })}
              className="bg-neutral-800 text-neutral-300 text-xs rounded px-2 py-1 border border-neutral-700"
            >
              <option value="pending">Pending</option>
              <option value="in_progress">In Progress</option>
              <option value="blocked">Blocked</option>
              <option value="review">Review</option>
              <option value="completed">Completed</option>
            </select>
            <select
              value={task.priority}
              onChange={(e) => onPatch({ priority: e.target.value })}
              className="bg-neutral-800 text-neutral-300 text-xs rounded px-2 py-1 border border-neutral-700"
            >
              <option value="critical">Critical</option>
              <option value="high">High</option>
              <option value="medium">Medium</option>
              <option value="low">Low</option>
            </select>
            <select
              value={task.type || 'agent'}
              onChange={(e) => onPatch({ type: e.target.value })}
              className="bg-neutral-800 text-neutral-300 text-xs rounded px-2 py-1 border border-neutral-700"
            >
              <option value="agent">Agent</option>
              <option value="human">Human</option>
              <option value="call">Call</option>
              <option value="external">External</option>
            </select>
          </div>

          {/* Quick advance button */}
          {nextStatus && (
            <button
              onClick={onAdvance}
              className={clsx(
                'text-xs px-3 py-1.5 rounded-lg font-medium transition-colors w-full',
                nextStatus === 'completed' ? 'bg-green-500/15 text-green-400 hover:bg-green-500/25' :
                nextStatus === 'in_progress' ? 'bg-blue-500/15 text-blue-400 hover:bg-blue-500/25' :
                'bg-neutral-800 text-neutral-400 hover:text-neutral-200'
              )}
            >
              {nextLabel[nextStatus] || nextStatus} →
            </button>
          )}

          {/* Metadata */}
          <div className="text-[10px] text-neutral-600 space-y-0.5">
            {task.source && <p>Source: {task.source}</p>}
            {task.created_by && <p>Created by: {task.created_by}</p>}
            {task.routed_to && <p>Routed to: {task.routed_to}</p>}
            {task.phase_id && <p>Phase: {task.phase_id}</p>}
            {task.due_date && <p>Due: {task.due_date}</p>}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Project Task Row (By Project view) ──────────────────────────

function ProjectTaskRow({ task, isExpanded, onToggle, onAdvance, onPatch, agentStatusMap }: {
  task: any;
  isExpanded: boolean;
  onToggle: () => void;
  onAdvance: () => void;
  onPatch: (body: any) => void;
  agentStatusMap: Record<string, string>;
}) {
  const nextStatus = STATUS_FLOW[task.status];
  const nextLabel: Record<string, string> = {
    in_progress: 'Start', completed: 'Done', pending: 'Reopen',
  };

  const statusDot: Record<string, string> = {
    pending: 'bg-neutral-500',
    in_progress: 'bg-blue-500',
    review: 'bg-purple-500',
    blocked: 'bg-amber-500',
    completed: 'bg-green-500',
  };

  return (
    <div className={clsx(
      'px-4 py-3 transition-colors hover:bg-neutral-800/30',
      task.status === 'blocked' && 'border-l-2 border-l-amber-500'
    )}>
      {/* Main row */}
      <div className="flex items-center gap-3 cursor-pointer" onClick={onToggle}>
        {/* Status dot */}
        <span className={clsx('w-2 h-2 rounded-full shrink-0', statusDot[task.status] || 'bg-neutral-600')} />

        {/* Priority */}
        <PriorityBadge priority={task.priority} />

        {/* Title */}
        <span className={clsx(
          'text-sm flex-1 leading-tight',
          task.status === 'completed' ? 'text-neutral-500 line-through' : 'text-neutral-200'
        )}>
          {task.title}
        </span>

        {/* Right side: type chip, agent badge, time, chevron */}
        <div className="flex items-center gap-2 shrink-0">
          {task.type && task.type !== 'agent' && (
            <span className={clsx('hidden sm:inline px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wider',
              task.type === 'human' ? 'bg-cyan-500/15 text-cyan-400' :
              task.type === 'call' ? 'bg-pink-500/15 text-pink-400' :
              task.type === 'external' ? 'bg-amber-500/15 text-amber-400' :
              'bg-neutral-700 text-neutral-400'
            )}>
              {task.type}
            </span>
          )}
          {task.assigned_to && (
            <AgentBadge assignedTo={task.assigned_to} agentStatusMap={agentStatusMap} />
          )}
          <span className="text-[10px] text-neutral-600 hidden sm:inline">{relativeTime(task.created_at)}</span>
          {isExpanded
            ? <ChevronDown size={13} className="text-neutral-600" />
            : <ChevronRight size={13} className="text-neutral-600" />
          }
        </div>
      </div>

      {/* Expanded detail */}
      {isExpanded && (
        <div className="mt-3 ml-5 pl-3 border-l border-neutral-700 space-y-3">
          {task.description && (
            <p className="text-xs text-neutral-400 leading-relaxed">{task.description}</p>
          )}

          {/* Controls */}
          <div className="flex flex-wrap gap-2">
            <select
              value={task.status}
              onChange={(e) => onPatch({ status: e.target.value })}
              className="bg-neutral-800 text-neutral-300 text-xs rounded px-2 py-1 border border-neutral-700"
            >
              <option value="pending">Pending</option>
              <option value="in_progress">In Progress</option>
              <option value="blocked">Blocked</option>
              <option value="review">Review</option>
              <option value="completed">Completed</option>
            </select>
            <select
              value={task.priority}
              onChange={(e) => onPatch({ priority: e.target.value })}
              className="bg-neutral-800 text-neutral-300 text-xs rounded px-2 py-1 border border-neutral-700"
            >
              <option value="critical">Critical</option>
              <option value="high">High</option>
              <option value="medium">Medium</option>
              <option value="low">Low</option>
            </select>
          </div>

          {nextStatus && (
            <button
              onClick={onAdvance}
              className={clsx(
                'text-xs px-3 py-1.5 rounded-lg font-medium transition-colors',
                nextStatus === 'completed' ? 'bg-green-500/15 text-green-400 hover:bg-green-500/25' :
                nextStatus === 'in_progress' ? 'bg-blue-500/15 text-blue-400 hover:bg-blue-500/25' :
                'bg-neutral-800 text-neutral-400 hover:text-neutral-200'
              )}
            >
              {nextLabel[nextStatus] || nextStatus} →
            </button>
          )}

          <div className="text-[10px] text-neutral-600 space-y-0.5">
            {task.source && <p>Source: {task.source}</p>}
            {task.created_by && <p>Created by: {task.created_by}</p>}
            {task.routed_to && <p>Routed to: {task.routed_to}</p>}
            {task.phase_id && <p>Phase: {task.phase_id}</p>}
            {task.due_date && <p>Due: {task.due_date}</p>}
          </div>
        </div>
      )}
    </div>
  );
}
