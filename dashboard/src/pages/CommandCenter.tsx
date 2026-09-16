import { useMemo } from 'react';
import { clsx } from 'clsx';
import { useQuery } from '@tanstack/react-query';
import {
  ExternalLink,
  GitCommit,
  AlertTriangle,
  BookOpen,
} from 'lucide-react';

// ── Types ───────────────────────────────────────────────────────────

interface Agent {
  id: string;
  name: string;
  tier: string;
  alive: boolean;
}

interface DeployedUrl {
  url: string;
  label: string;
}

interface Project {
  slug: string;
  staging_url: string | null;
  command_center_url: string | null;
  deliverables_url?: string | null;
  deployed_urls?: DeployedUrl[];
  git: {
    hash: string;
    date: string;
    message: string;
    author: string;
  } | null;
  commits_14d: number;
  tasks: {
    pending: number;
    in_progress: number;
    completed: number;
    blocked: number;
    total: number;
  };
  blockers: { title?: string; description?: string }[];
  roadmap: {
    current_phase: number;
    total_phases: number;
    phase_label: string;
    status: string;
  } | null;
  agents: Agent[];
  client: {
    name: string;
    owner: string;
    status: string;
    pm_agent: string | null;
  } | null;
}

// ── Helpers ─────────────────────────────────────────────────────────

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return `${Math.floor(days / 30)}mo ago`;
}

function truncate(s: string, max: number): string {
  return s.length > max ? s.slice(0, max) + '...' : s;
}

// ── Card ────────────────────────────────────────────────────────────

function ProjectCard({ project, maxCommits }: { project: Project; maxCommits: number }) {
  const isClient = !!project.client;
  const hasBlockers = project.blockers.length > 0;
  const commitPct = maxCommits > 0 ? (project.commits_14d / maxCommits) * 100 : 0;
  const roadmap = project.roadmap;
  const roadmapPct =
    roadmap && roadmap.total_phases > 0
      ? (roadmap.current_phase / roadmap.total_phases) * 100
      : null;

  return (
    <div
      className={clsx(
        'rounded-xl border bg-neutral-900 overflow-hidden',
        isClient ? 'border-l-4 border-l-violet-500 border-neutral-800' : 'border-neutral-800',
      )}
    >
      {/* Header */}
      <div className="p-5 pb-0">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="text-lg font-semibold text-white truncate">{project.slug}</h3>
            {isClient && (
              <p className="text-xs text-violet-400 mt-0.5">{project.client!.name}</p>
            )}
          </div>

          {/* Action buttons */}
          <div className="flex items-center gap-1.5 shrink-0 flex-wrap justify-end">
            {/* Deliverables button — primary action, links to one-pager with all assets */}
            {project.deliverables_url && (
              <a
                href={project.deliverables_url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-lg bg-violet-500/15 text-violet-400 hover:bg-violet-500/25 transition-colors font-medium"
              >
                <ExternalLink size={12} /> Deliverables
              </a>
            )}
            {/* Staging / dev server link */}
            {project.staging_url && (
              <a
                href={project.staging_url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-lg bg-neutral-800 text-neutral-300 hover:text-white hover:bg-neutral-700 transition-colors"
              >
                <ExternalLink size={12} /> Staging
              </a>
            )}
          </div>
        </div>
      </div>

      {/* Body */}
      <div className="p-5 pt-4 space-y-4">
        {/* Last commit */}
        {project.git && (
          <div className="space-y-1">
            <div className="flex items-center gap-2 text-xs text-neutral-500">
              <GitCommit size={12} />
              <span className="font-mono text-neutral-400">{project.git.hash}</span>
              <span>{timeAgo(project.git.date)}</span>
              <span className="ml-auto text-neutral-600">{project.git.author}</span>
            </div>
            <p className="text-sm text-neutral-400 truncate">{truncate(project.git.message, 80)}</p>
          </div>
        )}

        {/* Activity bar */}
        <div className="space-y-1">
          <p className="text-xs text-neutral-500">{project.commits_14d} commits in 14 days</p>
          <div className="h-1.5 rounded-full bg-neutral-800 overflow-hidden">
            <div
              className="h-full rounded-full bg-violet-500/70 transition-all"
              style={{ width: `${commitPct}%` }}
            />
          </div>
        </div>

        {/* Active agents */}
        {project.agents.length > 0 && (
          <div className="flex items-center gap-1.5 flex-wrap">
            {project.agents.map((agent) => (
              <div
                key={agent.id}
                title={`${agent.name} (${agent.tier}) — ${agent.alive ? 'online' : 'stopped'}`}
                className="relative w-7 h-7 rounded-full bg-neutral-800 flex items-center justify-center text-[10px] font-bold text-neutral-300 cursor-default"
              >
                {agent.name.charAt(0).toUpperCase()}
                <span
                  className={clsx(
                    'absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 rounded-full border-2 border-neutral-900',
                    agent.alive ? 'bg-green-500' : 'bg-neutral-600',
                  )}
                />
              </div>
            ))}
          </div>
        )}

        {/* Roadmap progress */}
        {roadmap && roadmap.total_phases > 0 && (
          <div className="space-y-1">
            <div className="flex items-center justify-between text-xs">
              <span className="text-neutral-400">
                Phase {roadmap.current_phase}/{roadmap.total_phases}
              </span>
              <span className="text-neutral-500 truncate ml-2 max-w-[60%] text-right">
                {roadmap.phase_label}
              </span>
            </div>
            <div className="h-1.5 rounded-full bg-neutral-800 overflow-hidden">
              <div
                className="h-full rounded-full bg-blue-500/70 transition-all"
                style={{ width: `${roadmapPct}%` }}
              />
            </div>
          </div>
        )}

        {/* Task pills */}
        {project.tasks.total > 0 && (
          <div className="flex flex-wrap gap-2">
            {project.tasks.pending > 0 && (
              <span className="text-xs px-2 py-0.5 rounded-full bg-neutral-800 text-neutral-400">
                {project.tasks.pending} pending
              </span>
            )}
            {project.tasks.in_progress > 0 && (
              <span className="text-xs px-2 py-0.5 rounded-full bg-blue-500/15 text-blue-400">
                {project.tasks.in_progress} active
              </span>
            )}
            {project.tasks.completed > 0 && (
              <span className="text-xs px-2 py-0.5 rounded-full bg-green-500/15 text-green-400">
                {project.tasks.completed} done
              </span>
            )}
            {project.tasks.blocked > 0 && (
              <span className="text-xs px-2 py-0.5 rounded-full bg-red-500/15 text-red-400">
                {project.tasks.blocked} blocked
              </span>
            )}
          </div>
        )}
      </div>

      {/* Blockers */}
      {hasBlockers && (
        <div className="border-t border-red-500/20 bg-red-500/5 px-5 py-3">
          <div className="flex items-center gap-1.5 text-xs font-medium text-red-400 mb-1.5">
            <AlertTriangle size={12} /> Blockers
          </div>
          <ul className="space-y-1">
            {project.blockers.map((b, i) => (
              <li key={i} className="text-xs text-red-300/80 truncate">
                {b.title || b.description || 'Unnamed blocker'}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

// ── Reference Hubs ──────────────────────────────────────────────────
// Standalone link cards (vendor docs, internal hubs) — not project-shaped,
// rendered above the project grid. Each card has a single "Docs" button
// that opens the hub URL in a new tab.

interface ReferenceHub {
  slug: string;
  title: string;
  subtitle: string;
  description: string;
  docs_url: string;
  accent: 'violet' | 'green' | 'amber';
}

// Populate with links to your own vendor/reference documentation hubs; empty
// by default (the section renders nothing when this is empty).
const REFERENCE_HUBS: ReferenceHub[] = [];

const ACCENT_STYLES = {
  violet: {
    border: 'border-l-violet-500',
    icon: 'text-violet-400',
    btn: 'bg-violet-500/15 text-violet-400 hover:bg-violet-500/25',
  },
  green: {
    border: 'border-l-emerald-500',
    icon: 'text-emerald-400',
    btn: 'bg-emerald-500/15 text-emerald-400 hover:bg-emerald-500/25',
  },
  amber: {
    border: 'border-l-amber-500',
    icon: 'text-amber-400',
    btn: 'bg-amber-500/15 text-amber-400 hover:bg-amber-500/25',
  },
};

function ReferenceHubCard({ hub }: { hub: ReferenceHub }) {
  const accent = ACCENT_STYLES[hub.accent];
  return (
    <div
      className={clsx(
        'rounded-xl border bg-neutral-900 overflow-hidden border-l-4 border-neutral-800',
        accent.border,
      )}
    >
      <div className="p-5">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex items-start gap-3">
            <BookOpen size={20} className={clsx('shrink-0 mt-0.5', accent.icon)} />
            <div className="min-w-0">
              <h3 className="text-lg font-semibold text-white truncate">{hub.title}</h3>
              <p className="text-xs text-neutral-500 mt-0.5">{hub.subtitle}</p>
            </div>
          </div>
          <a
            href={hub.docs_url}
            target="_blank"
            rel="noopener noreferrer"
            className={clsx(
              'flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-lg transition-colors font-medium shrink-0',
              accent.btn,
            )}
          >
            <ExternalLink size={12} /> Docs
          </a>
        </div>
        <p className="text-sm text-neutral-400 mt-3 leading-relaxed">{hub.description}</p>
      </div>
    </div>
  );
}

// ── Page ────────────────────────────────────────────────────────────

export default function CommandCenter() {
  const { data, isLoading, error } = useQuery<{ projects: Project[] }>({
    queryKey: ['project-status'],
    queryFn: async () => {
      // Try project-status first (rich format), fall back to projects (simple format)
      try {
        const r = await fetch('/api/project-status');
        if (r.ok) {
          const d = await r.json();
          if (d.projects?.length) return d;
        }
      } catch {}
      // Fallback: adapt /api/projects response to expected shape
      const r = await fetch('/api/projects');
      const d = await r.json();
      return {
        projects: (d.projects || []).map((p: any) => ({
          slug: p.slug,
          staging_url: p.staging_url || null,
          command_center_url: p.command_center_url || null,
          deliverables_url: p.deliverables_url || null,
          deployed_urls: p.deployed_urls || [],
          git: null,
          commits_14d: 0,
          tasks: { pending: 0, in_progress: 0, completed: 0, blocked: p.blocker_count || 0, total: 0 },
          blockers: [],
          roadmap: null,
          agents: (p.agents || []).map((a: any) => ({ id: a.id, name: a.name, tier: a.tier || 'T2', alive: a.alive || false })),
          client: {
            name: p.name,
            owner: 'operator',
            status: p.status || 'active',
            pm_agent: p.pm || null,
          },
        })),
      };
    },
    refetchInterval: 30_000,
  });

  const projects = useMemo(() => {
    if (!data?.projects) return [];
    return [...data.projects].sort((a, b) => {
      const da = a.git?.date ? new Date(a.git.date).getTime() : 0;
      const db = b.git?.date ? new Date(b.git.date).getTime() : 0;
      return db - da;
    });
  }, [data]);

  const maxCommits = useMemo(
    () => Math.max(1, ...projects.map((p) => p.commits_14d)),
    [projects],
  );

  if (isLoading) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold text-neutral-100 mb-6">Command Center</h1>
        <p className="text-neutral-500">Loading projects...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold text-neutral-100 mb-6">Command Center</h1>
        <p className="text-red-400">Failed to load project status.</p>
      </div>
    );
  }

  const totalAgents = projects.reduce((n, p) => n + p.agents.filter((a) => a.alive).length, 0);
  const totalBlockers = projects.reduce((n, p) => n + p.blockers.length, 0);

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-neutral-100">Command Center</h1>
        <p className="text-sm text-neutral-500 mt-0.5">
          {projects.length} projects &middot; {totalAgents} agents online
          {totalBlockers > 0 && (
            <span className="text-red-400"> &middot; {totalBlockers} blocker{totalBlockers !== 1 ? 's' : ''}</span>
          )}
        </p>
      </div>

      {/* Reference Hubs — vendor docs / internal aggregators */}
      {REFERENCE_HUBS.length > 0 && (
        <div>
          <h2 className="text-xs font-semibold text-neutral-500 uppercase tracking-wider mb-3">
            Reference Hubs
          </h2>
          <div className="grid grid-cols-1 lg:grid-cols-2 2xl:grid-cols-3 gap-4">
            {REFERENCE_HUBS.map((hub) => (
              <ReferenceHubCard key={hub.slug} hub={hub} />
            ))}
          </div>
        </div>
      )}

      {/* Projects */}
      <div>
        <h2 className="text-xs font-semibold text-neutral-500 uppercase tracking-wider mb-3">
          Projects
        </h2>
        <div className="grid grid-cols-1 lg:grid-cols-2 2xl:grid-cols-3 gap-4">
          {projects.map((project) => (
            <ProjectCard key={project.slug} project={project} maxCommits={maxCommits} />
          ))}
        </div>
      </div>

      {projects.length === 0 && (
        <p className="text-neutral-600 text-center py-12">No projects found</p>
      )}
    </div>
  );
}
