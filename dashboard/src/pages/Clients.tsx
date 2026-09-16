import { useState } from 'react';
import { clsx } from 'clsx';
import { useQuery } from '@tanstack/react-query';
import { fetchClients } from '../lib/api';
import { ClientsPeopleTabs } from '../components/ClientsPeopleTabs';
import {
  ChevronDown, ChevronRight, Globe, Users,
  Bot, ListTodo, Star, AlertTriangle, FolderOpen, Package,
} from 'lucide-react';
import { PriorityBadge } from '../components/PriorityBadge';

// ── API helpers ─────────────────────────────────────────────────

async function fetchJson<T>(path: string): Promise<T> {
  const res = await fetch(`/api${path}`);
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

// ── Status helpers ──────────────────────────────────────────────

const STATUS_DOT: Record<string, string> = {
  active: 'bg-green-500', stable: 'bg-green-500', online: 'bg-green-500',
  onboarding: 'bg-amber-500', setup: 'bg-amber-500',
  planned: 'bg-neutral-500', paused: 'bg-neutral-500',
  running: 'bg-green-500', stopped: 'bg-red-500',
  pending: 'bg-neutral-500', in_progress: 'bg-blue-500', completed: 'bg-green-500',
  complete: 'bg-green-500', blocked: 'bg-amber-500',
};

// ── Main Component ──────────────────────────────────────────────

export default function Clients() {
  const { data, isLoading } = useQuery({
    queryKey: ['clients'],
    queryFn: fetchClients,
  });

  const [expandedId, setExpandedId] = useState<string | null>(null);

  if (isLoading) return <p className="text-neutral-500 p-8">Loading...</p>;

  const clients = data?.clients ?? [];

  return (
    <div className="p-4 md:p-6 space-y-4">
      <ClientsPeopleTabs active="clients" count={clients.length} />

      {clients.length === 0 && (
        <p className="text-neutral-600 text-sm">No clients found.</p>
      )}

      <div className="space-y-3">
        {clients.map((client: any) => {
          const isExpanded = expandedId === client.id;
          const slug = client.slug || client.id;
          return (
            <div key={slug} className="rounded-xl border border-neutral-800 bg-neutral-900 overflow-hidden">
              <button
                onClick={() => setExpandedId(isExpanded ? null : slug)}
                className="w-full text-left p-5 hover:bg-neutral-800/30 transition-colors"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="space-y-1 min-w-0 flex-1">
                    <div className="flex items-center gap-2.5 flex-wrap">
                      <span className="text-white font-semibold text-lg">{client.name || slug}</span>
                      {client.type && (
                        <span className={clsx('text-xs px-2 py-0.5 rounded-full font-medium',
                          client.type === 'full-stack' ? 'bg-green-500/15 text-green-400' :
                          client.type === 'co-builder' ? 'bg-blue-500/15 text-blue-400' :
                          'bg-neutral-700 text-neutral-400'
                        )}>{client.type}</span>
                      )}
                      {client.status && (
                        <span className="flex items-center gap-1.5">
                          <span className={clsx('w-1.5 h-1.5 rounded-full', STATUS_DOT[client.status] || 'bg-neutral-600')} />
                          <span className="text-xs text-neutral-500">{client.status}</span>
                        </span>
                      )}
                    </div>
                    {client.location && <p className="text-sm text-neutral-500">{client.location}</p>}
                  </div>
                  {isExpanded ? <ChevronDown size={16} className="text-neutral-500 mt-1" /> : <ChevronRight size={16} className="text-neutral-500 mt-1" />}
                </div>

                {/* Stats row */}
                <div className="flex flex-wrap items-center gap-4 mt-3">
                  {client.system_count > 0 && (
                    <div className="flex items-center gap-1.5 text-xs">
                      <Globe size={12} className="text-neutral-500" />
                      <span className="text-neutral-400">{client.system_count} systems</span>
                    </div>
                  )}
                  {client.pm_agent && (
                    <div className="flex items-center gap-1.5 text-xs">
                      <Bot size={12} className="text-neutral-500" />
                      <span className="text-neutral-400">{client.pm_agent}</span>
                    </div>
                  )}
                  {client.primary_contact && (
                    <div className="flex items-center gap-1.5 text-xs ml-auto">
                      <Users size={12} className="text-neutral-500" />
                      <span className="text-neutral-400">{client.primary_contact}</span>
                    </div>
                  )}
                </div>
              </button>

              {isExpanded && <ClientDetail slug={slug} />}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Client Detail (loads all sections) ──────────────────────────

function ClientDetail({ slug }: { slug: string }) {
  // Fetch all data in parallel
  const { data: client } = useQuery({
    queryKey: ['client-detail', slug],
    queryFn: () => fetchJson<any>(`/clients/${slug}`),
  });

  const { data: agentsData } = useQuery({
    queryKey: ['client-agents', slug],
    queryFn: () => fetchJson<any>('/agents'),
  });

  const { data: tasksData } = useQuery({
    queryKey: ['client-tasks', slug],
    queryFn: () => fetchJson<any>(`/v2/tasks?client=${slug}&limit=20`),
  });

  const { data: nsData } = useQuery({
    queryKey: ['client-northstars', slug],
    queryFn: () => fetchJson<any>(`/north-stars?client=${slug}`),
  });

  const { data: projectsData } = useQuery({
    queryKey: ['client-projects', slug],
    queryFn: () => fetchJson<any>('/projects'),
  });

  // Filter agents by client
  const agents = (agentsData?.agents || []).filter((a: any) =>
    a.client === slug || (a.memory_scope || []).includes(slug) || a.id?.includes(slug)
  );

  // Filter projects
  const projects = (projectsData?.projects || []).filter((p: any) =>
    p.slug === slug || p.slug?.includes(slug)
  );

  const tasks = tasksData?.tasks || [];
  const northStars = nsData?.north_stars || [];
  const deliverables = client?.deliverables || [];
  const ecosystem = client?.ecosystem;

  // People: merge from client.json team + ecosystem contacts
  const rawPeople = client?.people || [];
  const ecoPeople = ecosystem?.contacts || {};
  let people: any[] = [];
  if (Array.isArray(rawPeople) && rawPeople.length > 0) {
    people = rawPeople;
  } else if (typeof rawPeople === 'object' && !Array.isArray(rawPeople) && Object.keys(rawPeople).length > 0) {
    people = Object.entries(rawPeople).map(([k, v]: [string, any]) => typeof v === 'string' ? { name: v, role: k } : { ...v, role: v.role || k });
  }
  // Merge ecosystem contacts if people is still empty
  if (people.length === 0 && Object.keys(ecoPeople).length > 0) {
    people = Object.entries(ecoPeople).map(([k, v]: [string, any]) => ({ ...v, role: v.role || k }));
  }

  const [openSections, setOpenSections] = useState<Set<string>>(new Set(['people', 'projects', 'agents']));
  const toggle = (s: string) => setOpenSections(prev => {
    const n = new Set(prev);
    n.has(s) ? n.delete(s) : n.add(s);
    return n;
  });

  return (
    <div className="border-t border-neutral-800 divide-y divide-neutral-800">

      {/* People */}
      <Section title="People" icon={Users} count={people.length} open={openSections.has('people')} onToggle={() => toggle('people')}>
        {people.length === 0 ? <Empty text="No people added" /> : (
          <div className="flex flex-wrap gap-2">
            {(Array.isArray(people) ? people : Object.entries(people).map(([k, v]: [string, any]) => typeof v === 'string' ? v : { ...v, role: k })).map((p: any, i: number) => (
              <div key={i} className="bg-neutral-800 rounded-lg px-3 py-2">
                <p className="text-sm text-neutral-200">{typeof p === 'string' ? p : p.name || p}</p>
                {p.role && <p className="text-[10px] text-neutral-500">{p.role}</p>}
              </div>
            ))}
          </div>
        )}
      </Section>

      {/* Projects */}
      <Section title="Projects" icon={FolderOpen} count={projects.length} open={openSections.has('projects')} onToggle={() => toggle('projects')}>
        {projects.length === 0 ? <Empty text="No projects" /> : (
          <div className="space-y-2">
            {projects.map((p: any) => (
              <div key={p.slug} className="flex items-center gap-3 bg-neutral-800 rounded-lg px-3 py-2">
                <span className={clsx('w-1.5 h-1.5 rounded-full', STATUS_DOT[p.status] || 'bg-neutral-600')} />
                <span className="text-sm text-neutral-200 flex-1">{p.name}</span>
                <span className="text-[10px] text-neutral-500">{p.active_agents}/{p.total_agents} agents</span>
              </div>
            ))}
          </div>
        )}
      </Section>

      {/* Agents */}
      <Section title="Agents" icon={Bot} count={agents.length} open={openSections.has('agents')} onToggle={() => toggle('agents')}>
        {agents.length === 0 ? <Empty text="No agents assigned" /> : (
          <div className="space-y-1.5">
            {agents.map((a: any) => (
              <div key={a.id} className="flex items-center gap-3 bg-neutral-800 rounded-lg px-3 py-2">
                <span className={clsx('w-2 h-2 rounded-full', a.tmux_alive || a.alive ? 'bg-green-500' : 'bg-red-500')} />
                <span className="text-sm text-neutral-200 flex-1">{a.name || a.id}</span>
                <span className="text-[10px] text-neutral-500">{a.tier}</span>
              </div>
            ))}
          </div>
        )}
      </Section>

      {/* Deliverables */}
      <Section title="Deliverables" icon={Package} count={deliverables.length} open={openSections.has('deliverables')} onToggle={() => toggle('deliverables')}>
        {deliverables.length === 0 ? <Empty text="No deliverables" /> : (
          <div className="space-y-1.5">
            {deliverables.map((d: any, i: number) => (
              <div key={i} className="flex items-center gap-3 bg-neutral-800 rounded-lg px-3 py-2">
                <span className={clsx('w-1.5 h-1.5 rounded-full', STATUS_DOT[d.status] || 'bg-neutral-600')} />
                <span className={clsx('text-sm flex-1', d.status === 'complete' ? 'text-neutral-500 line-through' : 'text-neutral-200')}>
                  {d.name}
                </span>
                {d.assigned_to && <span className="text-[10px] text-neutral-500">{d.assigned_to}</span>}
              </div>
            ))}
          </div>
        )}
      </Section>

      {/* North Stars */}
      <Section title="North Stars" icon={Star} count={northStars.length} open={openSections.has('northstars')} onToggle={() => toggle('northstars')}>
        {northStars.length === 0 ? <Empty text="No north stars set" /> : (
          <div className="space-y-2">
            {northStars.map((ns: any) => (
              <div key={ns.id} className="bg-neutral-800 rounded-lg px-3 py-2.5">
                <p className="text-sm text-neutral-200">{ns.objective}</p>
                <div className="flex items-center gap-2 mt-1.5">
                  <div className="flex-1 h-1 rounded-full bg-neutral-700 overflow-hidden">
                    <div className="h-full rounded-full bg-green-500" style={{ width: `${ns.kr_progress || 0}%` }} />
                  </div>
                  <span className="text-[10px] text-neutral-500">{ns.kr_done}/{ns.kr_total} KRs</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </Section>

      {/* Tasks */}
      <Section title="Tasks" icon={ListTodo} count={tasks.length} open={openSections.has('tasks')} onToggle={() => toggle('tasks')}>
        {tasks.length === 0 ? <Empty text="No tasks" /> : (
          <div className="space-y-1.5">
            {tasks.slice(0, 10).map((t: any) => (
              <div key={t.id} className="flex items-center gap-2 bg-neutral-800 rounded-lg px-3 py-2">
                <PriorityBadge priority={t.priority} />
                <span className={clsx('text-sm flex-1 truncate', t.status === 'completed' ? 'text-neutral-500 line-through' : 'text-neutral-200')}>
                  {t.title}
                </span>
                <span className={clsx('text-[10px] px-1.5 py-0.5 rounded',
                  t.status === 'completed' ? 'bg-green-500/15 text-green-400' :
                  t.status === 'in_progress' ? 'bg-blue-500/15 text-blue-400' :
                  t.status === 'blocked' ? 'bg-amber-500/15 text-amber-400' :
                  'bg-neutral-700 text-neutral-400'
                )}>{t.status}</span>
              </div>
            ))}
            {tasks.length > 10 && <p className="text-[10px] text-neutral-600 text-center">+{tasks.length - 10} more</p>}
          </div>
        )}
      </Section>

      {/* Ecosystem */}
      {ecosystem && (
        <Section title="Ecosystem" icon={Globe} count={ecosystem.systems?.length || 0} open={openSections.has('ecosystem')} onToggle={() => toggle('ecosystem')}>
          <div className="space-y-4">
            {/* Systems */}
            {ecosystem.systems?.length > 0 && (
              <div>
                <h5 className="text-[10px] text-neutral-500 uppercase tracking-wide mb-1.5">Systems</h5>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm min-w-[400px]">
                    <thead>
                      <tr className="text-neutral-500 text-xs border-b border-neutral-800">
                        <th className="text-left py-1 pr-3 font-medium">Name</th>
                        <th className="text-left py-1 pr-3 font-medium">Platform</th>
                        <th className="text-left py-1 font-medium">Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {ecosystem.systems.map((s: any, i: number) => (
                        <tr key={i} className="border-b border-neutral-800/50">
                          <td className="py-1 pr-3 text-neutral-200">{s.name}</td>
                          <td className="py-1 pr-3 text-neutral-400">{s.platform}</td>
                          <td className="py-1">
                            <span className="flex items-center gap-1.5">
                              <span className={clsx('w-1.5 h-1.5 rounded-full', STATUS_DOT[s.status] || 'bg-neutral-600')} />
                              <span className="text-neutral-400 text-xs">{s.status}</span>
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {/* Integrations */}
            {ecosystem.integrations?.length > 0 && (
              <div>
                <h5 className="text-[10px] text-neutral-500 uppercase tracking-wide mb-1.5">Integrations</h5>
                {ecosystem.integrations.map((int: any, i: number) => (
                  <div key={i} className="flex items-center gap-2 text-xs py-0.5">
                    <span className={clsx('w-1.5 h-1.5 rounded-full', STATUS_DOT[int.status] || 'bg-neutral-600')} />
                    <span className="text-neutral-300">{int.from}</span>
                    <span className="text-neutral-600">&rarr;</span>
                    <span className="text-neutral-300">{int.to}</span>
                  </div>
                ))}
              </div>
            )}

            {/* Fragilities */}
            {ecosystem.fragilities?.length > 0 && (
              <div>
                <h5 className="text-[10px] text-red-400/80 uppercase tracking-wide mb-1.5">Fragilities</h5>
                {ecosystem.fragilities.map((f: string, i: number) => (
                  <div key={i} className="flex items-start gap-2 text-xs bg-red-950/20 border border-red-900/30 rounded px-2 py-1.5 mb-1">
                    <AlertTriangle size={11} className="text-red-400 mt-0.5 shrink-0" />
                    <span className="text-red-300">{f}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </Section>
      )}
    </div>
  );
}

// ── Collapsible Section ─────────────────────────────────────────

function Section({ title, icon: Icon, count, open, onToggle, children }: {
  title: string;
  icon: any;
  count: number;
  open: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  return (
    <div>
      <button onClick={onToggle} className="w-full flex items-center gap-2.5 px-4 py-3 hover:bg-neutral-800/30 transition-colors">
        <Icon size={14} className="text-neutral-500" />
        <span className="text-xs font-medium text-neutral-400 uppercase tracking-wide flex-1 text-left">{title}</span>
        <span className="text-[10px] text-neutral-600 bg-neutral-800 px-1.5 py-0.5 rounded-full">{count}</span>
        {open ? <ChevronDown size={14} className="text-neutral-600" /> : <ChevronRight size={14} className="text-neutral-600" />}
      </button>
      {open && <div className="px-4 pb-3">{children}</div>}
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return <p className="text-xs text-neutral-600 py-2">{text}</p>;
}
