import { useState, useRef } from 'react';
import { clsx } from 'clsx';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { ClientsPeopleTabs } from '../components/ClientsPeopleTabs';
import {
  Users, Search, Plus, X, ChevronRight, ChevronDown,
  Globe, Mail, Phone, Link2, List, Columns,
} from 'lucide-react';

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

const REL_BADGE: Record<string, { label: string; color: string }> = {
  client: { label: 'Client', color: 'bg-emerald-500/15 text-emerald-400' },
  client_contact: { label: 'Contact', color: 'bg-cyan-500/15 text-cyan-400' },
  partner: { label: 'Partner', color: 'bg-blue-500/15 text-blue-400' },
  business_partner: { label: 'Partner', color: 'bg-blue-500/15 text-blue-400' },
  prospect: { label: 'Prospect', color: 'bg-amber-500/15 text-amber-400' },
  prospect_partner: { label: 'Prospect', color: 'bg-amber-500/15 text-amber-400' },
  active_collaborator: { label: 'Collaborator', color: 'bg-violet-500/15 text-violet-400' },
  collaborator: { label: 'Collaborator', color: 'bg-violet-500/15 text-violet-400' },
  vendor_contact: { label: 'Vendor', color: 'bg-neutral-700 text-neutral-400' },
  contact: { label: 'Contact', color: 'bg-neutral-700 text-neutral-400' },
};

// ── Main Component ──────────────────────────────────────────────

export default function People() {
  const queryClient = useQueryClient();
  const [view, setView] = useState<'list' | 'pipeline'>('pipeline');
  const [search, setSearch] = useState('');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [addName, setAddName] = useState('');
  const [activePipeline, setActivePipeline] = useState('pipeline_client');

  const { data: peopleData, isLoading: peopleLoading } = useQuery({
    queryKey: ['people', search],
    queryFn: () => fetchJson<any>(`/people?${search ? `search=${encodeURIComponent(search)}` : ''}`),
    refetchInterval: 15000,
  });

  const { data: pipelinesData } = useQuery({
    queryKey: ['pipelines'],
    queryFn: () => fetchJson<any>('/people/pipelines/all'),
    refetchInterval: 15000,
  });

  const { data: kanbanData } = useQuery({
    queryKey: ['pipeline-kanban', activePipeline],
    queryFn: () => fetchJson<any>(`/people/pipelines/${activePipeline}/people`),
    refetchInterval: 10000,
    enabled: view === 'pipeline',
  });

  const createMutation = useMutation({
    mutationFn: (body: any) => apiPost('/people', body),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['people'] }); queryClient.invalidateQueries({ queryKey: ['pipeline-kanban'] }); setAddOpen(false); setAddName(''); },
  });

  const moveMutation = useMutation({
    mutationFn: ({ id, stage_id }: { id: string; stage_id: string }) => apiPatch(`/people/${id}`, { stage_id }),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['pipeline-kanban'] }); queryClient.invalidateQueries({ queryKey: ['people'] }); },
  });

  const people = peopleData?.people ?? [];
  const pipelines = pipelinesData?.pipelines ?? [];
  const columns = kanbanData?.columns ?? [];

  if (peopleLoading) return <p className="text-neutral-500 p-8">Loading...</p>;

  return (
    <div className="p-4 md:p-6 flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <ClientsPeopleTabs active="people" count={people.length} />
        <div className="flex items-center gap-2">
          {/* View toggle */}
          <div className="flex rounded-md border border-neutral-700 overflow-hidden">
            <button onClick={() => setView('pipeline')}
              className={clsx('text-xs px-2.5 py-1 transition-colors', view === 'pipeline' ? 'bg-neutral-700 text-white' : 'text-neutral-500')}>
              <Columns size={14} />
            </button>
            <button onClick={() => setView('list')}
              className={clsx('text-xs px-2.5 py-1 transition-colors', view === 'list' ? 'bg-neutral-700 text-white' : 'text-neutral-500')}>
              <List size={14} />
            </button>
          </div>
          <button onClick={() => setAddOpen(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-violet-600 text-white text-sm font-medium rounded-lg hover:bg-violet-500 transition-colors">
            <Plus size={16} />
          </button>
        </div>
      </div>

      {/* Add person */}
      {addOpen && (
        <div className="flex gap-2 mb-3">
          <input value={addName} onChange={(e) => setAddName(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && addName.trim()) createMutation.mutate({ name: addName.trim() }); if (e.key === 'Escape') setAddOpen(false); }}
            placeholder="Person's name" autoFocus
            className="flex-1 bg-neutral-800 border border-neutral-700 rounded-lg px-3 py-2 text-sm text-neutral-200 placeholder-neutral-600 focus:outline-none focus:border-violet-500" />
          <button onClick={() => { if (addName.trim()) createMutation.mutate({ name: addName.trim() }); }}
            disabled={!addName.trim()} className="px-3 py-2 bg-violet-600 text-white rounded-lg disabled:opacity-50"><Plus size={16} /></button>
          <button onClick={() => setAddOpen(false)} className="p-2 text-neutral-500"><X size={16} /></button>
        </div>
      )}

      {/* Search */}
      <div className="relative mb-3">
        <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-neutral-600" />
        <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search people..."
          className="w-full bg-neutral-800 border border-neutral-700 rounded-lg pl-8 pr-3 py-1.5 text-sm text-neutral-200 placeholder-neutral-600 focus:outline-none" />
      </div>

      {/* ═══ PIPELINE VIEW ═══ */}
      {view === 'pipeline' && (
        <>
          {/* Pipeline tabs */}
          <div className="flex gap-1 bg-neutral-900 rounded-lg p-1 overflow-x-auto mb-3 shrink-0">
            {pipelines.map((p: any) => (
              <button key={p.id} onClick={() => setActivePipeline(p.id)}
                className={clsx('flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md transition-colors whitespace-nowrap',
                  activePipeline === p.id ? 'bg-neutral-700 text-white' : 'text-neutral-500 hover:text-neutral-300')}>
                {p.name}
                {p.person_count > 0 && (
                  <span className="text-[10px] bg-neutral-600 text-neutral-300 px-1.5 py-0.5 rounded-full">{p.person_count}</span>
                )}
              </button>
            ))}
          </div>

          {/* Kanban columns */}
          <div className="flex overflow-x-auto gap-3 flex-1 min-h-0 pb-16 snap-x snap-mandatory md:snap-none md:pb-2 md:grid"
            style={{ WebkitOverflowScrolling: 'touch', gridTemplateColumns: `repeat(${columns.length}, minmax(0, 1fr))` }}>
            {columns.map((col: any) => (
              <div key={col.stage.id}
                className="flex flex-col min-h-0 min-w-[72vw] w-[72vw] md:w-auto md:min-w-0 snap-start shrink-0 md:shrink">
                {/* Column header */}
                <div className="flex items-center gap-2 mb-2 px-1">
                  <span className="w-2 h-2 rounded-full" style={{ backgroundColor: pipelinesData?.pipelines?.find((p: any) => p.id === activePipeline)?.color || '#6b7280' }} />
                  <span className="text-sm font-semibold text-neutral-300">{col.stage.name}</span>
                  <span className="text-xs bg-neutral-800 text-neutral-500 px-1.5 py-0.5 rounded-full">{col.people.length}</span>
                </div>

                {/* Cards */}
                <div className="overflow-y-auto overflow-x-hidden flex-1 space-y-2 pr-1">
                  {col.people.length === 0 && <p className="text-neutral-700 text-xs px-2 py-4 text-center">Empty</p>}
                  {col.people.map((person: any) => (
                    <PipelineCard key={person.id} person={person} stages={columns.map((c: any) => c.stage)}
                      currentStageId={col.stage.id}
                      onSelect={() => setSelectedId(selectedId === person.id ? null : person.id)}
                      isSelected={selectedId === person.id}
                      onMove={(stageId: string) => moveMutation.mutate({ id: person.id, stage_id: stageId })} />
                  ))}
                </div>
              </div>
            ))}
            {/* Spacer for last column */}
            <div className="shrink-0 w-[28vw] md:hidden" aria-hidden />
          </div>
        </>
      )}

      {/* ═══ LIST VIEW ═══ */}
      {view === 'list' && (
        <div className="space-y-2 overflow-y-auto flex-1">
          {people.length === 0 && (
            <div className="flex flex-col items-center py-12 text-neutral-500">
              <Users className="w-10 h-10 mb-2 text-neutral-600" />
              <p className="text-sm">No people found</p>
            </div>
          )}
          {people.map((person: any) => (
            <ListCard key={person.id} person={person}
              isSelected={selectedId === person.id}
              onSelect={() => setSelectedId(selectedId === person.id ? null : person.id)} />
          ))}
        </div>
      )}
    </div>
  );
}

// ── Pipeline Card (Kanban) ──────────────────────────────────────

function PipelineCard({ person, stages, currentStageId, onSelect, isSelected, onMove }: {
  person: any; stages: any[]; currentStageId: string;
  onSelect: () => void; isSelected: boolean;
  onMove: (stageId: string) => void;
}) {
  const touchStartX = useRef<number>(0);
  const handleTouchStart = (e: React.TouchEvent) => { touchStartX.current = e.touches[0].clientX; };
  const handleTouchEnd = (e: React.TouchEvent) => {
    const dx = e.changedTouches[0].clientX - touchStartX.current;
    if (Math.abs(dx) < 60) return;
    const currentIdx = stages.findIndex(s => s.id === currentStageId);
    if (dx > 0 && currentIdx < stages.length - 1) onMove(stages[currentIdx + 1].id);
    if (dx < 0 && currentIdx > 0) onMove(stages[currentIdx - 1].id);
  };

  const initials = (person.name || '?').split(' ').map((w: string) => w[0]).join('').slice(0, 2).toUpperCase();

  return (
    <div className="rounded-lg border border-neutral-800 bg-neutral-900 p-3"
      onTouchStart={handleTouchStart} onTouchEnd={handleTouchEnd}>
      <div className="flex items-center gap-2.5" onClick={onSelect}>
        <div className="w-8 h-8 rounded-full bg-neutral-700 flex items-center justify-center text-[10px] font-bold text-neutral-300 shrink-0">
          {initials}
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-white truncate">{person.name}</p>
          {person.title && <p className="text-[10px] text-neutral-500 truncate">{person.title}</p>}
        </div>
      </div>

      {/* Tags */}
      <div className="flex flex-wrap gap-1 mt-2">
        {person.projects?.slice(0, 2).map((pp: any) => (
          <span key={pp.project_id} className="text-[9px] bg-violet-500/15 text-violet-400 px-1.5 py-0.5 rounded">{pp.project_id}</span>
        ))}
      </div>

      {/* Expanded detail */}
      {isSelected && (
        <div className="mt-2 pt-2 border-t border-neutral-800 space-y-2">
          {person.context && <p className="text-[10px] text-neutral-400 leading-relaxed">{person.context?.slice(0, 150)}</p>}
          {/* Stage selector */}
          <select value={currentStageId} onChange={(e) => onMove(e.target.value)}
            className="w-full bg-neutral-800 text-neutral-300 text-xs rounded px-2 py-1 border border-neutral-700">
            {stages.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </div>
      )}
    </div>
  );
}

// ── List Card ───────────────────────────────────────────────────

function ListCard({ person, isSelected, onSelect }: {
  person: any; isSelected: boolean; onSelect: () => void;
}) {
  const rel = REL_BADGE[person.relationship] || REL_BADGE.contact;
  const initials = (person.name || '?').split(' ').map((w: string) => w[0]).join('').slice(0, 2).toUpperCase();

  return (
    <div className="rounded-xl border border-neutral-800 bg-neutral-900 overflow-hidden">
      <button onClick={onSelect} className="w-full text-left p-3 hover:bg-neutral-800/30 transition-colors">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-full bg-neutral-700 flex items-center justify-center text-xs font-bold text-neutral-300 shrink-0">
            {initials}
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-sm font-medium text-white">{person.name}</span>
              <span className={clsx('text-[10px] px-1.5 py-0.5 rounded font-medium', rel.color)}>{rel.label}</span>
              {person.stage_name && (
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-neutral-800 text-neutral-400">{person.stage_name}</span>
              )}
            </div>
            {person.title && <p className="text-xs text-neutral-500 truncate">{person.title}</p>}
          </div>
          {isSelected ? <ChevronDown size={14} className="text-neutral-600" /> : <ChevronRight size={14} className="text-neutral-600" />}
        </div>
      </button>

      {isSelected && <PersonDetail personId={person.id} />}
    </div>
  );
}

// ── Person Detail ───────────────────────────────────────────────

function PersonDetail({ personId }: { personId: string }) {
  const { data: person } = useQuery({
    queryKey: ['person-detail', personId],
    queryFn: () => fetchJson<any>(`/people/${personId}`),
  });

  if (!person) return <div className="px-4 pb-3 text-neutral-600 text-xs">Loading...</div>;

  return (
    <div className="border-t border-neutral-800 px-4 pb-4 pt-3 space-y-4">
      {person.context && (
        <p className="text-xs text-neutral-400 bg-neutral-800/50 rounded-lg px-3 py-2 leading-relaxed">{person.context}</p>
      )}

      {(person.email || person.phone || person.linkedin || person.website) && (
        <div className="flex flex-wrap gap-2">
          {person.email && <a href={`mailto:${person.email}`} className="flex items-center gap-1.5 text-xs text-neutral-400 hover:text-neutral-200 bg-neutral-800 rounded-lg px-2.5 py-1.5"><Mail size={12} /> {person.email}</a>}
          {person.phone && <a href={`tel:${person.phone}`} className="flex items-center gap-1.5 text-xs text-neutral-400 hover:text-neutral-200 bg-neutral-800 rounded-lg px-2.5 py-1.5"><Phone size={12} /> {person.phone}</a>}
          {person.linkedin && <a href={person.linkedin} target="_blank" rel="noopener" className="flex items-center gap-1.5 text-xs text-blue-400 hover:text-blue-300 bg-neutral-800 rounded-lg px-2.5 py-1.5"><Link2 size={12} /> LinkedIn</a>}
          {person.website && <a href={person.website} target="_blank" rel="noopener" className="flex items-center gap-1.5 text-xs text-neutral-400 hover:text-neutral-200 bg-neutral-800 rounded-lg px-2.5 py-1.5"><Globe size={12} /> Website</a>}
        </div>
      )}

      {(person.goals || person.situation) && (
        <div className="space-y-1.5">
          {person.goals && <div className="text-xs"><span className="text-neutral-500">Goals:</span> <span className="text-neutral-300">{person.goals}</span></div>}
          {person.situation && <div className="text-xs"><span className="text-neutral-500">Situation:</span> <span className="text-neutral-300">{person.situation}</span></div>}
        </div>
      )}

      {person.projects?.length > 0 && (
        <div>
          <h4 className="text-[10px] text-neutral-500 uppercase tracking-wide mb-1.5">Projects</h4>
          <div className="flex flex-wrap gap-1.5">
            {person.projects.map((pp: any) => (
              <span key={pp.project_id} className="text-xs bg-violet-500/15 text-violet-400 px-2 py-0.5 rounded">{pp.project_id}{pp.role ? ` (${pp.role})` : ''}</span>
            ))}
          </div>
        </div>
      )}

      {person.connections?.length > 0 && (
        <div>
          <h4 className="text-[10px] text-neutral-500 uppercase tracking-wide mb-1.5">Connections</h4>
          {person.connections.map((c: any) => (
            <div key={c.id} className="flex items-center gap-2 text-xs">
              <Link2 size={11} className="text-neutral-600" />
              <span className="text-neutral-300">{c.other?.name}</span>
              <span className="text-neutral-600">—</span>
              <span className="text-neutral-500">{c.relationship?.replace(/_/g, ' ')}</span>
            </div>
          ))}
        </div>
      )}

      {person.activity?.length > 0 && (
        <div>
          <h4 className="text-[10px] text-neutral-500 uppercase tracking-wide mb-1.5">Activity</h4>
          {person.activity.slice(0, 5).map((a: any) => (
            <div key={a.id} className="text-xs text-neutral-500">
              <span className="text-neutral-400">{a.summary}</span>
              <span className="text-neutral-600 ml-1.5">· {a.source}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
