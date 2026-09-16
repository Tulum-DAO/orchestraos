import { useState } from 'react';
import { clsx } from 'clsx';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Star, Plus, ChevronDown, ChevronRight, Check, Circle,
  Target, Calendar, X,
} from 'lucide-react';
import { useUser } from '../hooks/useUser';

// ── API ─────────────────────────────────────────────────────────

async function fetchJson<T>(path: string): Promise<T> {
  const res = await fetch(`/api${path}`);
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

async function apiPost(path: string, body: any) {
  const res = await fetch(`/api${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

async function apiPatch(path: string, body: any) {
  const res = await fetch(`/api${path}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

// ── Helpers ─────────────────────────────────────────────────────

const SCOPE_BADGE: Record<string, { label: string; color: string }> = {
  global: { label: 'GLOBAL', color: 'bg-purple-500/15 text-purple-400 border-purple-500/30' },
  client: { label: 'CLIENT', color: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30' },
  project: { label: 'PROJECT', color: 'bg-blue-500/15 text-blue-400 border-blue-500/30' },
};

const PRIORITY_COLOR: Record<string, string> = {
  P0: 'text-red-400', P1: 'text-orange-400', P2: 'text-yellow-400', P3: 'text-green-400',
};

const PROGRESS_COLOR = (pct: number) =>
  pct >= 80 ? 'bg-green-500' : pct >= 40 ? 'bg-amber-500' : 'bg-neutral-600';

function daysAgo(ts: string | null): string {
  if (!ts) return 'never';
  const diff = Date.now() - new Date(ts).getTime();
  const days = Math.floor(diff / 86400000);
  if (days === 0) return 'today';
  if (days === 1) return '1d ago';
  return `${days}d ago`;
}

function daysUntil(ts: string | null): string {
  if (!ts) return '';
  const diff = new Date(ts).getTime() - Date.now();
  const days = Math.ceil(diff / 86400000);
  if (days < 0) return `${Math.abs(days)}d overdue`;
  if (days === 0) return 'today';
  return `${days}d left`;
}

// ── Component ───────────────────────────────────────────────────

export default function Strategy() {
  const queryClient = useQueryClient();
  const { data: user } = useUser();
  const isAdmin = !user || user.role === 'admin';

  const { data, isLoading } = useQuery({
    queryKey: ['north-stars'],
    queryFn: () => fetchJson<any>(isAdmin ? '/north-stars?grouped=true' : '/north-stars'),
    refetchInterval: 15000,
  });

  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [addObj, setAddObj] = useState('');
  const [addScope, setAddScope] = useState<'global' | 'client' | 'project'>('project');
  const [addProject, setAddProject] = useState('');
  const [addClient, setAddClient] = useState('');

  const createMutation = useMutation({
    mutationFn: (body: any) => apiPost('/north-stars', body),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['north-stars'] }); setAddOpen(false); setAddObj(''); },
  });

  const krMutation = useMutation({
    mutationFn: ({ nsId, krId, body }: { nsId: string; krId: string; body: any }) =>
      apiPatch(`/north-stars/${nsId}/key-results/${krId}`, body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['north-stars'] }),
  });

  const addKrMutation = useMutation({
    mutationFn: ({ nsId, description }: { nsId: string; description: string }) =>
      apiPost(`/north-stars/${nsId}/key-results`, { description }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['north-stars'] }),
  });

  // Fetch projects + clients for dropdowns
  const { data: projectsData } = useQuery({
    queryKey: ['projects'],
    queryFn: () => fetchJson<any>('/projects'),
  });
  const { data: clientsData } = useQuery({
    queryKey: ['clients'],
    queryFn: () => fetchJson<any>('/clients'),
  });
  const projectList: string[] = (projectsData?.projects || []).map((p: any) => p.slug || p.name);
  const clientList: string[] = (clientsData?.clients || []).map((c: any) => c.slug || c.id || c.name);

  const updateMutation = useMutation({
    mutationFn: ({ id, body }: { id: string; body: any }) => apiPatch(`/north-stars/${id}`, body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['north-stars'] }),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => fetch(`/api/north-stars/${id}`, { method: 'DELETE' }).then(r => r.json()),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['north-stars'] }),
  });

  const northStars: any[] = data?.north_stars ?? [];
  const grouped: Record<string, any[]> = data?.grouped ?? {};

  // Separate the operator's vs tenant north stars
  const myStars = isAdmin ? northStars.filter((ns: any) => ns.tenant_id === 'operator') : northStars;
  const tenantStars = isAdmin ? Object.entries(grouped).filter(([k]) => k !== 'operator') : [];

  if (isLoading) return <p className="text-neutral-500 p-8">Loading...</p>;

  return (
    <div className="p-4 md:p-6 space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Strategy</h1>
          <p className="text-xs text-neutral-500 mt-0.5">{northStars.length} north star{northStars.length !== 1 ? 's' : ''}</p>
        </div>
        <button
          onClick={() => setAddOpen(true)}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-violet-600 text-white text-sm font-medium rounded-lg hover:bg-violet-500 transition-colors"
        >
          <Plus size={16} />
          <span className="hidden sm:inline">Add North Star</span>
        </button>
      </div>

      {/* Add north star */}
      {addOpen && (
        <div className="rounded-xl border border-neutral-700 bg-neutral-900 p-4 space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-white">New North Star</h3>
            <button onClick={() => setAddOpen(false)} className="text-neutral-500 hover:text-neutral-300"><X size={16} /></button>
          </div>
          <input
            value={addObj}
            onChange={(e) => setAddObj(e.target.value)}
            placeholder="What does success look like?"
            className="w-full bg-neutral-800 border border-neutral-700 rounded-lg px-3 py-2 text-sm text-neutral-200 placeholder-neutral-600 focus:outline-none focus:border-violet-500"
            autoFocus
          />
          <div className="flex gap-2">
            <select value={addScope} onChange={(e) => setAddScope(e.target.value as any)}
              className="bg-neutral-800 text-neutral-300 text-xs rounded-lg px-2 py-1.5 border border-neutral-700">
              <option value="global">Global</option>
              <option value="client">Client</option>
              <option value="project">Project</option>
            </select>
            {addScope === 'project' && (
              <select value={addProject} onChange={(e) => setAddProject(e.target.value)}
                className="bg-neutral-800 text-neutral-300 text-xs rounded-lg px-2 py-1.5 border border-neutral-700 flex-1">
                <option value="">Select project</option>
                {projectList.map(p => <option key={p} value={p}>{p}</option>)}
              </select>
            )}
            {addScope === 'client' && (
              <select value={addClient} onChange={(e) => setAddClient(e.target.value)}
                className="bg-neutral-800 text-neutral-300 text-xs rounded-lg px-2 py-1.5 border border-neutral-700 flex-1">
                <option value="">Select client</option>
                {clientList.map(c => <option key={c} value={c}>{c}</option>)}
              </select>
            )}
          </div>
          <button
            onClick={() => {
              if (!addObj.trim()) return;
              createMutation.mutate({
                objective: addObj.trim(),
                scope: addScope,
                project_id: addScope === 'project' ? addProject : undefined,
                client_id: addScope === 'client' ? addClient : undefined,
              });
            }}
            disabled={!addObj.trim() || createMutation.isPending}
            className="w-full px-3 py-2 bg-violet-600 text-white text-sm font-medium rounded-lg hover:bg-violet-500 disabled:opacity-50 transition-colors"
          >
            Create
          </button>
        </div>
      )}

      {/* Empty state */}
      {northStars.length === 0 && !addOpen && (
        <div className="flex flex-col items-center py-16 text-neutral-500">
          <Star className="w-12 h-12 mb-3 text-neutral-600" />
          <p className="text-lg font-medium text-neutral-400">No north stars set</p>
          <p className="text-sm">Set a strategic objective to guide your agent fleet.</p>
        </div>
      )}

      {/* My North Stars */}
      {myStars.length > 0 && (
        <div className="space-y-3">
          {isAdmin && tenantStars.length > 0 && (
            <h2 className="text-xs font-medium text-neutral-500 uppercase tracking-wide">My North Stars</h2>
          )}
          {myStars.map((ns: any) => (
            <NorthStarCard
              key={ns.id}
              ns={ns}
              isExpanded={expandedId === ns.id}
              onToggle={() => setExpandedId(expandedId === ns.id ? null : ns.id)}
              onKrToggle={(krId: string, status: string) =>
                krMutation.mutate({ nsId: ns.id, krId, body: { status: status === 'completed' ? 'pending' : 'completed' } })
              }
              onAddKr={(desc: string) => addKrMutation.mutate({ nsId: ns.id, description: desc })}
              onUpdate={(body: any) => updateMutation.mutate({ id: ns.id, body })}
              onDelete={() => { if (confirm('Abandon this north star?')) deleteMutation.mutate(ns.id); }}
            />
          ))}
        </div>
      )}

      {/* Tenant North Stars (admin only) */}
      {isAdmin && tenantStars.map(([tenantId, stars]) => (
        <div key={tenantId} className="space-y-3">
          <h2 className="text-xs font-medium text-neutral-500 uppercase tracking-wide">
            {tenantId}'s Strategy
          </h2>
          {stars.map((ns: any) => (
            <NorthStarCard
              key={ns.id}
              ns={ns}
              isExpanded={expandedId === ns.id}
              onToggle={() => setExpandedId(expandedId === ns.id ? null : ns.id)}
              onKrToggle={(krId: string, status: string) =>
                krMutation.mutate({ nsId: ns.id, krId, body: { status: status === 'completed' ? 'pending' : 'completed' } })
              }
              onAddKr={(desc: string) => addKrMutation.mutate({ nsId: ns.id, description: desc })}
              onUpdate={(body: any) => updateMutation.mutate({ id: ns.id, body })}
              onDelete={() => { if (confirm('Abandon this north star?')) deleteMutation.mutate(ns.id); }}
            />
          ))}
        </div>
      ))}
    </div>
  );
}

// ── North Star Card ─────────────────────────────────────────────

function NorthStarCard({ ns, isExpanded, onToggle, onKrToggle, onAddKr, onUpdate, onDelete }: {
  ns: any;
  isExpanded: boolean;
  onToggle: () => void;
  onKrToggle: (krId: string, status: string) => void;
  onAddKr: (desc: string) => void;
  onUpdate: (body: any) => void;
  onDelete: () => void;
}) {
  const [newKr, setNewKr] = useState('');
  const [editing, setEditing] = useState(false);
  const [editText, setEditText] = useState(ns.objective);
  const scope = SCOPE_BADGE[ns.scope] || SCOPE_BADGE.project;
  const krs = ns.key_results || [];
  const progress = ns.kr_progress || 0;
  const isStale = ns.status === 'stale' || (ns.last_progress && (Date.now() - new Date(ns.last_progress).getTime()) > 7 * 86400000);

  return (
    <div className={clsx(
      'rounded-xl border bg-neutral-900 overflow-hidden',
      isStale ? 'border-amber-700/50' : 'border-neutral-800'
    )}>
      {/* Card header */}
      <button onClick={onToggle} className="w-full text-left p-4 hover:bg-neutral-800/30 transition-colors">
        <div className="flex items-start gap-3">
          <Star size={18} className="text-amber-400 shrink-0 mt-0.5" />
          <div className="flex-1 min-w-0">
            {/* Badges */}
            <div className="flex items-center gap-2 flex-wrap mb-1.5">
              <span className={clsx('text-[10px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded border', scope.color)}>
                {scope.label}
              </span>
              {ns.project_id && (
                <span className="text-[10px] text-neutral-500">{ns.project_id}</span>
              )}
              {ns.client_id && (
                <span className="text-[10px] text-neutral-500">{ns.client_id}</span>
              )}
              <span className={clsx('text-[10px] font-medium', PRIORITY_COLOR[ns.priority] || 'text-neutral-500')}>
                {ns.priority}
              </span>
              {isStale && (
                <span className="text-[10px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-400 border border-amber-500/30">
                  STALE
                </span>
              )}
            </div>

            {/* Objective — click to edit when expanded */}
            {editing ? (
              <input
                value={editText}
                onChange={(e) => setEditText(e.target.value)}
                onBlur={() => { if (editText.trim() && editText !== ns.objective) onUpdate({ objective: editText.trim() }); setEditing(false); }}
                onKeyDown={(e) => { if (e.key === 'Enter') { e.currentTarget.blur(); } if (e.key === 'Escape') { setEditText(ns.objective); setEditing(false); } }}
                onClick={(e) => e.stopPropagation()}
                className="w-full bg-neutral-800 border border-violet-500 rounded px-2 py-1 text-sm font-medium text-neutral-100 focus:outline-none"
                autoFocus
              />
            ) : (
              <p className="text-sm font-medium text-neutral-100 leading-snug">{ns.objective}</p>
            )}

            {/* Progress bar + stats */}
            <div className="mt-2 flex items-center gap-3">
              <div className="flex-1 h-1.5 rounded-full bg-neutral-800 overflow-hidden">
                <div className={clsx('h-full rounded-full transition-all', PROGRESS_COLOR(progress))} style={{ width: `${progress}%` }} />
              </div>
              <span className="text-[10px] text-neutral-500 shrink-0">
                {ns.kr_done}/{ns.kr_total} KRs
              </span>
            </div>

            {/* Footer stats */}
            <div className="flex items-center gap-3 mt-1.5 text-[10px] text-neutral-600">
              {ns.aligned_tasks > 0 && (
                <span className="flex items-center gap-1"><Target size={10} /> {ns.aligned_tasks} tasks</span>
              )}
              {ns.target_date && (
                <span className="flex items-center gap-1"><Calendar size={10} /> {daysUntil(ns.target_date)}</span>
              )}
              <span>Progress: {daysAgo(ns.last_progress)}</span>
            </div>
          </div>
          {isExpanded ? <ChevronDown size={14} className="text-neutral-600 shrink-0 mt-1" /> : <ChevronRight size={14} className="text-neutral-600 shrink-0 mt-1" />}
        </div>
      </button>

      {/* Expanded: Key Results */}
      {isExpanded && (
        <div className="border-t border-neutral-800 px-4 pb-4 pt-3 space-y-2">
          <h4 className="text-[10px] font-medium text-neutral-500 uppercase tracking-wide">Key Results</h4>

          {krs.length === 0 && (
            <p className="text-xs text-neutral-600">No key results yet</p>
          )}

          {krs.map((kr: any) => (
            <button
              key={kr.id}
              onClick={() => onKrToggle(kr.id, kr.status)}
              className="flex items-start gap-2.5 w-full text-left py-1 group"
            >
              {kr.status === 'completed' ? (
                <Check size={14} className="text-green-500 shrink-0 mt-0.5" />
              ) : (
                <Circle size={14} className="text-neutral-600 group-hover:text-neutral-400 shrink-0 mt-0.5" />
              )}
              <span className={clsx(
                'text-xs leading-relaxed',
                kr.status === 'completed' ? 'text-neutral-500 line-through' : 'text-neutral-300'
              )}>
                {kr.description}
              </span>
            </button>
          ))}

          {/* Add KR */}
          <div className="flex gap-2 mt-2">
            <input
              value={newKr}
              onChange={(e) => setNewKr(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && newKr.trim()) { onAddKr(newKr.trim()); setNewKr(''); } }}
              placeholder="Add key result..."
              className="flex-1 bg-neutral-800 border border-neutral-700 rounded-lg px-2.5 py-1.5 text-xs text-neutral-300 placeholder-neutral-600 focus:outline-none focus:border-violet-500"
            />
            <button
              onClick={() => { if (newKr.trim()) { onAddKr(newKr.trim()); setNewKr(''); } }}
              className="px-2.5 py-1.5 bg-neutral-800 text-neutral-400 hover:text-neutral-200 rounded-lg text-xs transition-colors"
            >
              <Plus size={14} />
            </button>
          </div>

          {/* Actions */}
          <div className="flex gap-2 mt-3 pt-3 border-t border-neutral-800">
            <button
              onClick={(e) => { e.stopPropagation(); setEditing(true); }}
              className="text-xs px-3 py-1.5 rounded-lg bg-neutral-800 text-neutral-400 hover:text-neutral-200 transition-colors"
            >
              Edit objective
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); onDelete(); }}
              className="text-xs px-3 py-1.5 rounded-lg bg-neutral-800 text-red-400/60 hover:text-red-400 hover:bg-red-500/10 transition-colors ml-auto"
            >
              Abandon
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
