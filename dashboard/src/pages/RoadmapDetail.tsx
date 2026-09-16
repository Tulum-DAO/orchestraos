import { useState, useMemo, useRef, useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { clsx } from 'clsx';
import {
  ArrowLeft, ChevronDown, ChevronRight, AlertTriangle,
  CheckCircle2, Circle, Clock, XCircle, Plus, Pencil, Trash2, Check, X,
} from 'lucide-react';
import { fetchRoadmaps, patchTask, createTask, deleteTask } from '../lib/api';
import { PriorityBadge } from '../components/PriorityBadge';

// ── Status config ─────────────────────────────────────────────────────

type TaskStatus = 'pending' | 'in_progress' | 'completed' | 'blocked';

const STATUS_CONFIG: Record<TaskStatus, { label: string; badge: string; icon: React.ReactNode }> = {
  pending:     { label: 'Pending',     badge: 'bg-neutral-700 text-neutral-400',   icon: <Circle size={14} className="text-neutral-500" /> },
  in_progress: { label: 'In Progress', badge: 'bg-blue-500/20 text-blue-400',      icon: <Clock size={14} className="text-blue-400" /> },
  completed:   { label: 'Completed',   badge: 'bg-green-500/20 text-green-400',    icon: <CheckCircle2 size={14} className="text-green-500" /> },
  blocked:     { label: 'Blocked',     badge: 'bg-red-500/20 text-red-400',        icon: <XCircle size={14} className="text-red-400" /> },
};

const ALL_STATUSES: TaskStatus[] = ['pending', 'in_progress', 'completed', 'blocked'];

function normalizeStatus(s?: string): TaskStatus {
  if (!s) return 'pending';
  const lower = s.toLowerCase();
  if (['complete', 'completed', 'done'].includes(lower)) return 'completed';
  if (['active', 'in_progress', 'working'].includes(lower)) return 'in_progress';
  if (lower === 'blocked') return 'blocked';
  return 'pending';
}

// ── StatusBadge ───────────────────────────────────────────────────────

function StatusBadge({ status, onChange }: { status: TaskStatus; onChange?: (s: TaskStatus) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const cfg = STATUS_CONFIG[status];

  useEffect(() => {
    if (!open) return;
    function handler(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  if (!onChange) {
    return (
      <span className={clsx('text-[11px] px-2 py-0.5 rounded-full font-medium', cfg.badge)}>
        {cfg.label}
      </span>
    );
  }

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen(v => !v)}
        className={clsx('text-[11px] px-2 py-0.5 rounded-full font-medium hover:opacity-80 transition-opacity cursor-pointer', cfg.badge)}
      >
        {cfg.label}
      </button>
      {open && (
        <div className="absolute right-0 top-full mt-1 z-50 bg-neutral-900 border border-neutral-700 rounded-lg shadow-xl overflow-hidden min-w-[120px]">
          {ALL_STATUSES.map(s => (
            <button
              key={s}
              onClick={() => { onChange(s); setOpen(false); }}
              className={clsx(
                'w-full text-left px-3 py-2 text-xs hover:bg-neutral-800 transition-colors flex items-center gap-2',
                s === status ? 'text-white' : 'text-neutral-400'
              )}
            >
              {STATUS_CONFIG[s].icon}
              {STATUS_CONFIG[s].label}
              {s === status && <Check size={12} className="ml-auto text-neutral-500" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ── TaskRow ───────────────────────────────────────────────────────────

function TaskRow({
  task,
  phaseIdx,
  taskIdx,
  projectKey,
}: {
  task: any;
  phaseIdx: number;
  taskIdx: number;
  projectKey: string;
}) {
  const queryClient = useQueryClient();
  const status = normalizeStatus(task.status);
  const isDone = status === 'completed';

  const [editing, setEditing] = useState(false);
  const [editName, setEditName] = useState(task.name || '');
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing && inputRef.current) inputRef.current.focus();
  }, [editing]);

  const patchMutation = useMutation({
    mutationFn: (updates: { status?: string; name?: string }) =>
      patchTask(projectKey, phaseIdx, taskIdx, updates),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['roadmaps'] }),
  });

  const deleteMutation = useMutation({
    mutationFn: () => deleteTask(projectKey, phaseIdx, taskIdx),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['roadmaps'] }),
  });

  const toggleDone = () => {
    patchMutation.mutate({ status: isDone ? 'pending' : 'completed' });
  };

  const changeStatus = (s: TaskStatus) => {
    patchMutation.mutate({ status: s });
  };

  const commitEdit = () => {
    const trimmed = editName.trim();
    if (trimmed && trimmed !== task.name) {
      patchMutation.mutate({ name: trimmed });
    }
    setEditing(false);
  };

  const cancelEdit = () => {
    setEditName(task.name || '');
    setEditing(false);
  };

  return (
    <div className={clsx(
      'group px-4 py-3 flex items-center gap-3 hover:bg-neutral-800/30 transition-colors',
      patchMutation.isPending && 'opacity-60'
    )}>
      {/* Checkbox */}
      <button
        onClick={toggleDone}
        className="shrink-0 focus:outline-none"
        aria-label={isDone ? 'Mark pending' : 'Mark complete'}
      >
        {isDone
          ? <CheckCircle2 size={17} className="text-green-500" />
          : <Circle size={17} className="text-neutral-600 hover:text-neutral-400 transition-colors" />
        }
      </button>

      {/* Title (inline edit) */}
      {editing ? (
        <div className="flex-1 flex items-center gap-2">
          <input
            ref={inputRef}
            value={editName}
            onChange={e => setEditName(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') commitEdit(); if (e.key === 'Escape') cancelEdit(); }}
            className="flex-1 bg-neutral-800 border border-neutral-600 rounded px-2 py-1 text-sm text-neutral-100 focus:outline-none focus:border-neutral-400"
          />
          <button onClick={commitEdit} className="text-green-400 hover:text-green-300 p-1">
            <Check size={14} />
          </button>
          <button onClick={cancelEdit} className="text-neutral-500 hover:text-neutral-400 p-1">
            <X size={14} />
          </button>
        </div>
      ) : (
        <span
          className={clsx(
            'flex-1 text-sm leading-snug select-none',
            isDone ? 'text-neutral-500 line-through' : 'text-neutral-200'
          )}
        >
          {task.name}
        </span>
      )}

      {/* Status badge + actions (only visible on hover or active) */}
      {!editing && (
        <div className="flex items-center gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
          <StatusBadge status={status} onChange={changeStatus} />
          <button
            onClick={() => { setEditing(true); setEditName(task.name || ''); }}
            className="p-1 text-neutral-600 hover:text-neutral-300 transition-colors"
            title="Edit task"
          >
            <Pencil size={13} />
          </button>
          <button
            onClick={() => deleteMutation.mutate()}
            className="p-1 text-neutral-600 hover:text-red-400 transition-colors"
            title="Delete task"
          >
            <Trash2 size={13} />
          </button>
        </div>
      )}
      {/* Always show status badge (non-interactive) when not hovering */}
      {!editing && (
        <div className="opacity-100 group-hover:opacity-0 transition-opacity absolute right-4 pointer-events-none">
          <StatusBadge status={status} />
        </div>
      )}
    </div>
  );
}

// ── AddTaskForm ───────────────────────────────────────────────────────

function AddTaskForm({
  phaseIdx,
  projectKey,
  onClose,
}: {
  phaseIdx: number;
  projectKey: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => { inputRef.current?.focus(); }, []);

  const createMutation = useMutation({
    mutationFn: () => createTask(projectKey, phaseIdx, { name: name.trim(), description: description.trim() || undefined }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['roadmaps'] });
      onClose();
    },
  });

  const submit = () => {
    if (!name.trim()) return;
    createMutation.mutate();
  };

  return (
    <div className="px-4 py-3 border-t border-neutral-800 bg-neutral-950/60 space-y-2">
      <input
        ref={inputRef}
        value={name}
        onChange={e => setName(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter') submit(); if (e.key === 'Escape') onClose(); }}
        placeholder="Task name…"
        className="w-full bg-neutral-800 border border-neutral-700 rounded-lg px-3 py-2 text-sm text-neutral-100 placeholder:text-neutral-600 focus:outline-none focus:border-neutral-500"
      />
      <input
        value={description}
        onChange={e => setDescription(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter') submit(); if (e.key === 'Escape') onClose(); }}
        placeholder="Description (optional)"
        className="w-full bg-neutral-800 border border-neutral-700 rounded-lg px-3 py-2 text-sm text-neutral-100 placeholder:text-neutral-600 focus:outline-none focus:border-neutral-500"
      />
      <div className="flex items-center gap-2">
        <button
          onClick={submit}
          disabled={!name.trim() || createMutation.isPending}
          className="px-3 py-1.5 text-xs font-medium rounded-lg bg-violet-600 hover:bg-violet-500 disabled:opacity-40 disabled:cursor-not-allowed text-white transition-colors"
        >
          {createMutation.isPending ? 'Adding…' : 'Add task'}
        </button>
        <button onClick={onClose} className="px-3 py-1.5 text-xs text-neutral-500 hover:text-neutral-300 transition-colors">
          Cancel
        </button>
      </div>
    </div>
  );
}

// ── PhaseSection ──────────────────────────────────────────────────────

function PhaseSection({
  phase,
  phaseIdx,
  projectKey,
  defaultOpen,
}: {
  phase: any;
  phaseIdx: number;
  projectKey: string;
  defaultOpen: boolean;
}) {
  const [isOpen, setIsOpen] = useState(defaultOpen);
  const [adding, setAdding] = useState(false);

  const tasks: any[] = phase.tasks || [];
  const doneCount = tasks.filter(t => normalizeStatus(t.status) === 'completed').length;
  const phaseProgress = tasks.length > 0 ? (doneCount / tasks.length) * 100 : 0;

  return (
    <div className="rounded-xl border border-neutral-800 bg-neutral-900 overflow-hidden">
      {/* Phase header */}
      <button
        onClick={() => setIsOpen(v => !v)}
        className="w-full text-left p-4 hover:bg-neutral-800/30 transition-colors"
      >
        <div className="flex items-center gap-3">
          <span className="text-neutral-600">
            {isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          </span>
          <span className="text-white font-medium">{phase.name}</span>
          <span className="text-xs text-neutral-500 ml-auto tabular-nums">
            {doneCount}/{tasks.length}
          </span>
          <span className="text-xs text-neutral-600 tabular-nums w-10 text-right">
            {Math.round(phaseProgress)}%
          </span>
        </div>
        {/* Progress bar */}
        <div className="w-full h-1.5 rounded-full bg-neutral-800 overflow-hidden mt-2.5">
          <div
            className={clsx(
              'h-full rounded-full transition-all duration-500',
              phaseProgress === 100 ? 'bg-green-500' : phaseProgress > 50 ? 'bg-blue-500' : 'bg-violet-500'
            )}
            style={{ width: `${phaseProgress}%` }}
          />
        </div>
      </button>

      {/* Task list */}
      {isOpen && (
        <div className="border-t border-neutral-800">
          {tasks.length > 0 ? (
            <div className="divide-y divide-neutral-800/60 relative">
              {tasks.map((task: any, taskIdx: number) => (
                <TaskRow
                  key={taskIdx}
                  task={task}
                  phaseIdx={phaseIdx}
                  taskIdx={taskIdx}
                  projectKey={projectKey}
                />
              ))}
            </div>
          ) : (
            <p className="text-xs text-neutral-600 px-4 py-3">No tasks in this phase</p>
          )}

          {/* Add task form or button */}
          {adding ? (
            <AddTaskForm phaseIdx={phaseIdx} projectKey={projectKey} onClose={() => setAdding(false)} />
          ) : (
            <div className="px-4 py-2 border-t border-neutral-800/60">
              <button
                onClick={() => setAdding(true)}
                className="flex items-center gap-1.5 text-xs text-neutral-600 hover:text-neutral-300 transition-colors py-1"
              >
                <Plus size={13} /> Add task
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────

export default function RoadmapDetail() {
  const { projectSlug } = useParams<{ projectSlug: string }>();

  const { data: roadmaps, isLoading } = useQuery({
    queryKey: ['roadmaps'],
    queryFn: fetchRoadmaps,
  });

  // Find the project
  const allProjects = roadmaps?.projects ?? roadmaps?.summary ?? [];
  const project = useMemo(() => {
    return allProjects.find((p: any) =>
      p.key === projectSlug ||
      p.id === projectSlug ||
      p.name?.toLowerCase().replace(/\s+/g, '-') === projectSlug
    );
  }, [allProjects, projectSlug]);

  const phases: any[] = project?.phases ?? [];

  // Overall progress
  const { totalTasks, doneTasks } = useMemo(() => {
    let total = 0;
    let done = 0;
    for (const ph of phases) {
      const tasks = ph.tasks || [];
      total += tasks.length;
      done += tasks.filter((t: any) => normalizeStatus(t.status) === 'completed').length;
    }
    return { totalTasks: total, doneTasks: done };
  }, [phases]);

  const overallProgress = totalTasks > 0 ? (doneTasks / totalTasks) * 100 : 0;

  if (isLoading) return <p className="text-neutral-500 p-8">Loading…</p>;

  if (!project) {
    return (
      <div className="p-6">
        <Link to="/projects" className="flex items-center gap-2 text-sm text-neutral-400 hover:text-white mb-4 transition-colors">
          <ArrowLeft size={16} /> Back to Projects
        </Link>
        <p className="text-neutral-500">Project not found: {projectSlug}</p>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-6">
      {/* Back link */}
      <Link to="/projects" className="flex items-center gap-2 text-sm text-neutral-400 hover:text-white transition-colors">
        <ArrowLeft size={16} /> Projects
      </Link>

      {/* Page header */}
      <div className="space-y-3">
        <div className="flex items-center gap-3 flex-wrap">
          {project.priority && <PriorityBadge priority={project.priority} />}
          <h1 className="text-2xl font-bold text-white">{project.name}</h1>
          <span className="text-xs text-neutral-600 font-mono bg-neutral-800 px-2 py-0.5 rounded">
            {project.key || projectSlug}
          </span>
        </div>

        {/* Overall progress bar */}
        <div className="space-y-1.5">
          <div className="flex items-center justify-between text-sm">
            <span className="text-neutral-400">
              {doneTasks}/{totalTasks} tasks complete
            </span>
            <span className="text-neutral-500 tabular-nums">{Math.round(overallProgress)}%</span>
          </div>
          <div className="w-full h-2.5 rounded-full bg-neutral-800 overflow-hidden">
            <div
              className={clsx(
                'h-full rounded-full transition-all duration-700',
                overallProgress === 100 ? 'bg-green-500' : 'bg-violet-500'
              )}
              style={{ width: `${overallProgress}%` }}
            />
          </div>
        </div>

        {/* Stats row */}
        <div className="flex flex-wrap items-center gap-4 text-xs text-neutral-500">
          <span>{phases.length} phase{phases.length !== 1 ? 's' : ''}</span>
          <span className="text-neutral-700">·</span>
          <span className="flex items-center gap-1">
            <Circle size={6} className="fill-neutral-500 text-neutral-500" />
            {totalTasks - doneTasks} remaining
          </span>
          <span className="flex items-center gap-1">
            <Circle size={6} className="fill-green-500 text-green-500" />
            {doneTasks} done
          </span>
        </div>
      </div>

      {/* Phase sections */}
      <div className="space-y-3">
        {phases.map((phase: any, phaseIdx: number) => (
          <PhaseSection
            key={phaseIdx}
            phase={phase}
            phaseIdx={phaseIdx}
            projectKey={project.key || projectSlug || ''}
            defaultOpen={phaseIdx === 0}
          />
        ))}

        {phases.length === 0 && (
          <p className="text-neutral-600 text-center py-10 border border-dashed border-neutral-800 rounded-xl">
            No phases defined for this project
          </p>
        )}
      </div>

      {/* Blockers */}
      {project.blockers?.length > 0 && (
        <div className="space-y-3">
          <h2 className="text-xs font-semibold text-neutral-500 uppercase tracking-wider flex items-center gap-2">
            <AlertTriangle size={12} className="text-red-500" />
            Blockers <span className="text-red-500">{project.blockers.length}</span>
          </h2>
          {project.blockers.map((b: any, i: number) => (
            <div key={i} className="rounded-xl border border-red-900/40 bg-red-950/20 p-4 flex items-start gap-2">
              <AlertTriangle size={14} className="text-red-500 shrink-0 mt-0.5" />
              <div>
                <p className="text-sm text-red-300">{typeof b === 'string' ? b : b.description || b.fact}</p>
                {b.approval_id && (
                  <Link to="/approvals" className="text-xs text-red-400/60 hover:text-red-400 mt-1 inline-block">
                    View related approval &rarr;
                  </Link>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
