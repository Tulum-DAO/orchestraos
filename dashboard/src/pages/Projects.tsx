import { useState, useMemo } from 'react';
import { clsx } from 'clsx';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { fetchProjects } from '../lib/api';
import {
  Bot,
  ChevronDown,
  ChevronUp,
  Users,
  GitBranch,
  Circle,
  ArrowUpRight,
  ArrowRight,
} from 'lucide-react';

// ── Status config ────────────────────────────────────────────────────

const STATUS_FILTERS = ['All', 'Active', 'Client', 'Paused'] as const;

const STATUS_CONFIG: Record<string, { dot: string; label: string; badge: string; group: string }> = {
  active:         { dot: 'bg-green-500',  label: 'Active',   badge: 'bg-green-500/15 text-green-400',  group: 'Active' },
  client_active:  { dot: 'bg-emerald-500', label: 'Client Active', badge: 'bg-emerald-500/15 text-emerald-400', group: 'Client' },
  client_blocked: { dot: 'bg-red-500',    label: 'Blocked',  badge: 'bg-red-500/15 text-red-400',      group: 'Client' },
  stable:         { dot: 'bg-blue-500',   label: 'Stable',   badge: 'bg-blue-500/15 text-blue-400',    group: 'Active' },
  at_risk:        { dot: 'bg-amber-500',  label: 'At Risk',  badge: 'bg-amber-500/15 text-amber-400',  group: 'Active' },
  paused:         { dot: 'bg-neutral-500', label: 'Paused',  badge: 'bg-neutral-500/15 text-neutral-400', group: 'Paused' },
  prototype:      { dot: 'bg-purple-500', label: 'Prototype', badge: 'bg-purple-500/15 text-purple-400', group: 'Active' },
};

const VERTICAL_BADGE: Record<string, string> = {
  data_platform:  'bg-indigo-500/15 text-indigo-400',
  operations:     'bg-cyan-500/15 text-cyan-400',
  advertising:    'bg-amber-500/15 text-amber-400',
  tracking:       'bg-pink-500/15 text-pink-400',
  marketplace:    'bg-violet-500/15 text-violet-400',
  client:         'bg-emerald-500/15 text-emerald-400',
  tool:           'bg-neutral-700 text-neutral-400',
};

function getStatusConfig(status: string) {
  return STATUS_CONFIG[status] || { dot: 'bg-neutral-600', label: status, badge: 'bg-neutral-700 text-neutral-400', group: 'Active' };
}

// ── Components ───────────────────────────────────────────────────────

function ProjectCard({ project, isExpanded, onToggle }: {
  project: any;
  isExpanded: boolean;
  onToggle: () => void;
}) {
  const sc = getStatusConfig(project.status);
  const verticalStyle = VERTICAL_BADGE[project.vertical] || 'bg-neutral-700 text-neutral-400';
  const slug = project.slug || project.key || project.name?.toLowerCase().replace(/\s+/g, '-');

  return (
    <div className="rounded-xl border border-neutral-800 bg-neutral-900 overflow-hidden">
      {/* Card header */}
      <button onClick={onToggle} className="w-full text-left p-5 hover:bg-neutral-800/30 transition-colors">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            {/* Name + badges row */}
            <div className="flex items-center gap-2.5 flex-wrap">
              <span className="text-white font-semibold text-lg">{project.name}</span>
              <span className={clsx('text-xs px-2 py-0.5 rounded-full font-medium', sc.badge)}>
                {sc.label}
              </span>
              <span className={clsx('text-xs px-2 py-0.5 rounded-full font-medium', verticalStyle)}>
                {project.vertical?.replace('_', ' ')}
              </span>
              {project.priority != null && (
                <span className="text-xs text-neutral-600 font-mono">P{project.priority}</span>
              )}
              {project.blocker_count > 0 && (
                <span className="text-xs px-2 py-0.5 rounded-full font-medium bg-red-500/15 text-red-400">
                  {project.blocker_count} blocker{project.blocker_count !== 1 ? 's' : ''}
                </span>
              )}
            </div>
            {/* Summary */}
            <p className="text-sm text-neutral-400 mt-1.5 line-clamp-2">{project.summary}</p>
          </div>
          <div className="text-neutral-500 shrink-0 mt-1">
            {isExpanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
          </div>
        </div>

        {/* Stats row */}
        <div className="flex flex-wrap items-center gap-4 mt-3">
          {/* Agent count */}
          <div className="flex items-center gap-1.5 text-xs">
            <Bot size={12} className="text-neutral-500" />
            <span className={project.active_agents > 0 ? 'text-green-400' : 'text-neutral-500'}>
              {project.active_agents}/{project.total_agents} agents
            </span>
          </div>
          {/* Team */}
          {project.team?.length > 0 && (
            <div className="flex items-center gap-1.5 text-xs">
              <Users size={12} className="text-neutral-500" />
              <span className="text-neutral-400">{project.team.join(', ')}</span>
            </div>
          )}
          {/* PM */}
          {project.pm && (
            <div className="flex items-center gap-1.5 text-xs ml-auto">
              <GitBranch size={12} className="text-neutral-500" />
              <span className="text-neutral-400">{project.pm}</span>
            </div>
          )}
        </div>
      </button>

      {/* Expanded detail */}
      {isExpanded && <ProjectDetail project={project} slug={slug} />}
    </div>
  );
}

function ProjectDetail({ project, slug }: { project: any; slug: string }) {
  const navigate = useNavigate();
  return (
    <div className="border-t border-neutral-800 px-5 pb-5 pt-4 space-y-5">
      {/* Roadmap link */}
      <button
        onClick={() => navigate(`/roadmaps/${slug}`)}
        className="flex items-center gap-2 px-4 py-2 rounded-lg bg-violet-600 hover:bg-violet-500 text-white text-sm font-medium transition-colors w-fit"
      >
        View Roadmap <ArrowRight size={14} />
      </button>

      {/* Current State */}
      {project.current_state && (
        <div>
          <h4 className="text-xs font-medium text-neutral-500 uppercase tracking-wide mb-2 flex items-center gap-1.5">
            <ArrowUpRight size={12} /> Current State
          </h4>
          <p className="text-sm text-neutral-300 bg-neutral-800/50 rounded-lg px-3 py-2">{project.current_state}</p>
        </div>
      )}

      {/* Agents */}
      {project.agents?.length > 0 && (
        <div>
          <h4 className="text-xs font-medium text-neutral-500 uppercase tracking-wide mb-2 flex items-center gap-1.5">
            <Bot size={12} /> Assigned Agents
          </h4>
          <div className="overflow-x-auto">
            <table className="w-full text-sm min-w-[500px]">
              <thead>
                <tr className="text-neutral-500 text-xs border-b border-neutral-800">
                  <th className="text-left py-1.5 pr-4 font-medium">Agent</th>
                  <th className="text-left py-1.5 pr-4 font-medium">Tier</th>
                  <th className="text-left py-1.5 pr-4 font-medium">Status</th>
                  <th className="text-left py-1.5 font-medium">Current Task</th>
                </tr>
              </thead>
              <tbody>
                {project.agents.map((agent: any) => (
                  <tr key={agent.id} className="border-b border-neutral-800/50">
                    <td className="py-1.5 pr-4">
                      <div className="flex items-center gap-2">
                        <span className={clsx(
                          'inline-block w-2 h-2 rounded-full shrink-0',
                          agent.alive ? 'bg-green-500' : 'bg-red-500'
                        )} />
                        <span className="text-neutral-200 font-medium">{agent.name}</span>
                      </div>
                    </td>
                    <td className="py-1.5 pr-4">
                      <span className={clsx(
                        'text-xs px-1.5 py-0.5 rounded font-medium',
                        agent.tier === 'T0' ? 'bg-purple-100 text-purple-800'
                          : agent.tier === 'T1' ? 'bg-blue-100 text-blue-800'
                          : agent.tier === 'T2' ? 'bg-green-100 text-green-800'
                          : 'bg-gray-100 text-gray-800'
                      )}>
                        {agent.tier}
                      </span>
                    </td>
                    <td className="py-1.5 pr-4">
                      <span className={clsx('text-xs font-medium', agent.alive ? 'text-green-400' : 'text-red-400')}>
                        {agent.alive ? 'online' : 'stopped'}
                      </span>
                    </td>
                    <td className="py-1.5 text-neutral-500 max-w-[300px] truncate">
                      {agent.current_task || '---'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Repo */}
      {project.repo && (
        <div className="flex items-center gap-2 text-sm">
          <span className="text-neutral-500">Repo:</span>
          <span className="text-neutral-300 font-mono text-xs bg-neutral-800 rounded px-2 py-0.5">{project.repo}</span>
        </div>
      )}
    </div>
  );
}

// ── Main Page ────────────────────────────────────────────────────────

export default function Projects() {
  const { data, isLoading } = useQuery({
    queryKey: ['projects'],
    queryFn: fetchProjects,
  });

  const [statusFilter, setStatusFilter] = useState<string>('All');
  const [expandedSlug, setExpandedSlug] = useState<string | null>(null);

  const projects = data?.projects ?? [];

  const filtered = useMemo(() => {
    if (statusFilter === 'All') return projects;
    return projects.filter((p: any) => {
      const group = getStatusConfig(p.status).group;
      return group === statusFilter;
    });
  }, [projects, statusFilter]);

  // Summary counts
  const activeCount = projects.filter((p: any) => ['active', 'stable', 'at_risk', 'prototype'].includes(p.status)).length;
  const clientCount = projects.filter((p: any) => ['client_active', 'client_blocked'].includes(p.status)).length;
  const totalAgents = projects.reduce((sum: number, p: any) => sum + (p.active_agents || 0), 0);

  if (isLoading) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold text-neutral-100 mb-6">Projects</h1>
        <p className="text-neutral-500">Loading...</p>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-neutral-100">Projects</h1>
            <p className="text-sm text-neutral-500 mt-0.5">{filtered.length} of {projects.length} projects</p>
          </div>
        </div>

        {/* Filter bar */}
        <div className="flex items-center gap-3 overflow-x-auto pb-2 -mx-6 px-6 scrollbar-hide" style={{ WebkitOverflowScrolling: 'touch' }}>
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
        </div>
      </div>

      {/* Health summary */}
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
        <span className="flex items-center gap-1.5">
          <Circle size={8} className="text-green-500 fill-green-500" />
          <span className="text-neutral-300">{activeCount} active</span>
        </span>
        <span className="flex items-center gap-1.5">
          <Circle size={8} className="text-emerald-500 fill-emerald-500" />
          <span className="text-neutral-300">{clientCount} clients</span>
        </span>
        <span className="flex items-center gap-1.5">
          <Bot size={14} className="text-neutral-500" />
          <span className="text-neutral-300">{totalAgents} agents online</span>
        </span>
      </div>

      {/* Project cards */}
      <div className="grid grid-cols-1 gap-4">
        {filtered.map((project: any) => (
          <ProjectCard
            key={project.slug}
            project={project}
            isExpanded={expandedSlug === project.slug}
            onToggle={() => setExpandedSlug(expandedSlug === project.slug ? null : project.slug)}
          />
        ))}
        {filtered.length === 0 && (
          <p className="text-neutral-600 text-center py-8">No projects found</p>
        )}
      </div>
    </div>
  );
}
