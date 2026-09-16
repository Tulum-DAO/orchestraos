import { useState, useMemo, useEffect, useRef, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { clsx } from 'clsx';
import { useQuery } from '@tanstack/react-query';
import { fetchActivity, connectActivitySSE } from '../lib/api';
import {
  MessageSquare,
  ListTodo,
  ShieldCheck,
  AlertTriangle,
  Flag,
  HeartPulse,
  Zap,
} from 'lucide-react';

// ── Types ──────────────────────────────────────────────────────────────

interface ActivityEventData {
  timestamp: string;
  agent: string;
  event: string;
  detail: string;
  task_id?: string;
}

// ── Event type mapping ─────────────────────────────────────────────────

type EventCategory = 'Message' | 'Task' | 'Approval' | 'Error' | 'Milestone' | 'Heartbeat' | 'Action';

const EVENT_CATEGORIES: EventCategory[] = [
  'Message', 'Task', 'Approval', 'Error', 'Milestone', 'Heartbeat', 'Action',
];

const EVENT_TYPE_MAP: Record<string, EventCategory> = {
  message_delivered: 'Message',
  message_sent: 'Message',
  message_injected: 'Message',
  message: 'Message',
  task_created: 'Task',
  task_routed: 'Task',
  task_completed: 'Task',
  route: 'Task',
  complete: 'Task',
  git_push: 'Action',
  approval_requested: 'Approval',
  approval_resolved: 'Approval',
  error: 'Error',
  heartbeat_stale: 'Heartbeat',
  heartbeat: 'Heartbeat',
  agent_spawned: 'Action',
  manual_spawn: 'Action',
  manual_kill: 'Action',
  spawn: 'Action',
  milestone: 'Milestone',
  context_request: 'Message',
  brief_ack: 'Message',
  brief_checkpoint: 'Message',
  brief_result: 'Message',
  brief_blocker: 'Error',
  test_event: 'Action',
};

function categorize(eventType: string): EventCategory {
  return EVENT_TYPE_MAP[eventType] || 'Action';
}

// ── Icons per category ─────────────────────────────────────────────────

const CATEGORY_ICON: Record<EventCategory, React.ComponentType<{ className?: string; size?: number }>> = {
  Message: MessageSquare,
  Task: ListTodo,
  Approval: ShieldCheck,
  Error: AlertTriangle,
  Milestone: Flag,
  Heartbeat: HeartPulse,
  Action: Zap,
};

// ── Agent initial circle colors ────────────────────────────────────────

const AGENT_COLORS = [
  'bg-blue-600', 'bg-emerald-600', 'bg-purple-600', 'bg-amber-600',
  'bg-rose-600', 'bg-cyan-600', 'bg-indigo-600', 'bg-pink-600',
  'bg-teal-600', 'bg-orange-600', 'bg-lime-600', 'bg-fuchsia-600',
];

function agentColor(name: string): string {
  let hash = 0;
  for (let i = 0; i < name.length; i++) {
    hash = name.charCodeAt(i) + ((hash << 5) - hash);
  }
  return AGENT_COLORS[Math.abs(hash) % AGENT_COLORS.length];
}

// ── Relative time ──────────────────────────────────────────────────────

function relativeTime(ts: string): string {
  const diff = Date.now() - new Date(ts).getTime();
  const seconds = Math.floor(diff / 1000);
  if (seconds < 5) return 'just now';
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

// ── Linkify detail text ────────────────────────────────────────────────

// Matches agent name patterns: words ending in -dev, -pm, -ops, -gm, etc.
const AGENT_NAME_RE = /\b([\w-]+-(?:dev|pm|ops|gm|web|ui|worker|builder|strategist|advisor|designer|researcher|auditor|engineer)|(?:pm|qa|gm|jarvis|kai|shaw)-[\w-]+)\b/g;

function linkifyDetail(detail: string, knownAgents: Set<string>): React.ReactNode[] {
  const parts: React.ReactNode[] = [];
  let last = 0;
  const text = detail;
  let match: RegExpExecArray | null;
  AGENT_NAME_RE.lastIndex = 0;
  while ((match = AGENT_NAME_RE.exec(text)) !== null) {
    const name = match[1];
    if (!knownAgents.has(name)) continue;
    if (match.index > last) {
      parts.push(text.slice(last, match.index));
    }
    parts.push(
      <Link
        key={`${name}-${match.index}`}
        to={`/agents?focus=${encodeURIComponent(name)}`}
        className="inline-flex items-center px-1 py-0.5 rounded bg-violet-500/10 text-violet-400 hover:bg-violet-500/20 transition-colors"
        onClick={(e) => e.stopPropagation()}
      >
        {name}
      </Link>
    );
    last = match.index + match[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts.length > 0 ? parts : [text];
}

// ── Component ──────────────────────────────────────────────────────────

export default function ActivityPage() {
  const { data: initialData, isLoading } = useQuery({
    queryKey: ['activity'],
    queryFn: () => fetchActivity(200),
  });

  const [liveEvents, setLiveEvents] = useState<ActivityEventData[]>([]);
  const [newEventIds, setNewEventIds] = useState<Set<string>>(new Set());
  const [agentFilter, setAgentFilter] = useState('');
  const [activeCategories, setActiveCategories] = useState<Set<EventCategory>>(
    new Set(EVENT_CATEGORIES)
  );
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [sseConnected, setSseConnected] = useState(false);
  const sseRef = useRef(false);

  // SSE connection
  useEffect(() => {
    if (sseRef.current) return;
    sseRef.current = true;
    const source = connectActivitySSE((event: any) => {
      if (event.type === 'connected') {
        setSseConnected(true);
        return;
      }
      const id = `${event.timestamp}|${event.agent}|${event.detail}`;
      setNewEventIds((prev) => new Set(prev).add(id));
      setLiveEvents((prev) => [event, ...prev.slice(0, 499)]);
      // Clear fade-in marker after animation
      setTimeout(() => {
        setNewEventIds((prev) => {
          const next = new Set(prev);
          next.delete(id);
          return next;
        });
      }, 1000);
    });
    setSseConnected(true);
    return () => {
      source.close();
      setSseConnected(false);
    };
  }, []);

  // Toggle a category pill
  const toggleCategory = useCallback((cat: EventCategory) => {
    setActiveCategories((prev) => {
      const next = new Set(prev);
      if (next.has(cat)) {
        next.delete(cat);
      } else {
        next.add(cat);
      }
      return next;
    });
  }, []);

  // Merge and dedupe events
  const allEvents = useMemo(() => {
    const base = Array.isArray(initialData) ? initialData : (initialData?.events ?? []);
    const merged = [...liveEvents, ...base];
    const seen = new Set<string>();
    return merged.filter((e: ActivityEventData) => {
      const key = `${e.timestamp}|${e.agent}|${e.detail}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }, [initialData, liveEvents]);

  // Unique agents
  const agents = useMemo(() => {
    const set = new Set<string>();
    allEvents.forEach((e) => e.agent && set.add(e.agent));
    return Array.from(set).sort();
  }, [allEvents]);

  // Set of known agent names for linkification
  const knownAgentsSet = useMemo(() => new Set(agents), [agents]);

  // Filtered events
  const filtered = useMemo(() => {
    return allEvents.filter((e: ActivityEventData) => {
      if (agentFilter && e.agent !== agentFilter) return false;
      const cat = categorize(e.event);
      if (!activeCategories.has(cat)) return false;
      if (dateFrom) {
        const from = new Date(dateFrom).getTime();
        if (new Date(e.timestamp).getTime() < from) return false;
      }
      if (dateTo) {
        // Include the entire "to" day
        const to = new Date(dateTo).getTime() + 86400000;
        if (new Date(e.timestamp).getTime() > to) return false;
      }
      return true;
    });
  }, [allEvents, agentFilter, activeCategories, dateFrom, dateTo]);

  if (isLoading) return <p className="text-neutral-500 p-8">Loading activity...</p>;

  return (
    <div className="p-6 space-y-5">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <h1 className="text-2xl font-bold text-white">Activity</h1>
        <div className="flex flex-wrap items-center gap-4">
          {/* Date range */}
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <label className="text-neutral-500">From</label>
            <input
              type="date"
              value={dateFrom}
              onChange={(e) => setDateFrom(e.target.value)}
              className="bg-neutral-900 border border-neutral-800 text-neutral-300 text-sm rounded-lg px-2.5 py-1.5 focus:outline-none focus:border-neutral-600"
            />
            <label className="text-neutral-500">To</label>
            <input
              type="date"
              value={dateTo}
              onChange={(e) => setDateTo(e.target.value)}
              className="bg-neutral-900 border border-neutral-800 text-neutral-300 text-sm rounded-lg px-2.5 py-1.5 focus:outline-none focus:border-neutral-600"
            />
          </div>

          {/* Agent dropdown */}
          <select
            value={agentFilter}
            onChange={(e) => setAgentFilter(e.target.value)}
            className="bg-neutral-900 border border-neutral-800 text-neutral-300 text-sm rounded-lg px-3 py-1.5 focus:outline-none focus:border-neutral-600"
          >
            <option value="">All agents</option>
            {agents.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Category pill toggles */}
      <div className="flex flex-wrap gap-2">
        {EVENT_CATEGORIES.map((cat) => {
          const active = activeCategories.has(cat);
          return (
            <button
              key={cat}
              onClick={() => toggleCategory(cat)}
              className={clsx(
                'px-3 py-1 rounded-full text-xs font-medium transition-colors select-none',
                active
                  ? 'bg-neutral-700 text-white'
                  : 'bg-neutral-900 text-neutral-500 border border-neutral-800'
              )}
            >
              {cat}
            </button>
          );
        })}
      </div>

      {/* Live indicator + event list */}
      <div className="rounded-xl border border-neutral-800 overflow-hidden">
        {/* Live banner */}
        {sseConnected && (
          <div className="flex items-center gap-2 px-4 py-2 border-b border-neutral-800 bg-neutral-900/60">
            <span className="relative flex h-2.5 w-2.5">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75" />
              <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-green-500" />
            </span>
            <span className="text-xs font-medium text-green-400">Live</span>
          </div>
        )}

        {/* Events */}
        <div className="divide-y divide-neutral-800/50">
          {filtered.length === 0 && (
            <div className="text-center text-neutral-600 py-12 text-sm">
              No activity events match the current filters
            </div>
          )}
          {filtered.map((event: ActivityEventData, i: number) => {
            const cat = categorize(event.event);
            const Icon = CATEGORY_ICON[cat];
            const eventKey = `${event.timestamp}|${event.agent}|${event.detail}`;
            const isNew = newEventIds.has(eventKey);
            const initial = event.agent ? event.agent.charAt(0).toUpperCase() : '?';
            const color = event.agent ? agentColor(event.agent) : 'bg-neutral-700';

            return (
              <div
                key={`${eventKey}-${i}`}
                className={clsx(
                  'flex items-center gap-3 px-4 py-3 hover:bg-neutral-900/50 transition-all',
                  isNew && 'animate-fade-in'
                )}
              >
                {/* Relative time */}
                <span className="text-xs text-neutral-600 font-mono w-16 shrink-0 text-right">
                  {relativeTime(event.timestamp)}
                </span>

                {/* Agent initial circle — clickable */}
                {event.agent ? (
                  <Link
                    to={`/agents?focus=${encodeURIComponent(event.agent)}`}
                    className={clsx(
                      'w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold text-white shrink-0 hover:ring-2 hover:ring-white/20 transition-all',
                      color
                    )}
                    title={event.agent}
                  >
                    {initial}
                  </Link>
                ) : (
                  <div
                    className={clsx(
                      'w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold text-white shrink-0',
                      color
                    )}
                  >
                    {initial}
                  </div>
                )}

                {/* Event icon */}
                <Icon size={16} className="text-neutral-500 shrink-0" />

                {/* Event content */}
                <div className="min-w-0 flex-1 flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5">
                  <span className="text-sm font-semibold text-white shrink-0">{cat}</span>
                  {event.agent && (
                    <Link
                      to={`/agents?focus=${encodeURIComponent(event.agent)}`}
                      className="inline-flex items-center px-1.5 py-0.5 rounded bg-violet-500/10 text-violet-400 text-xs hover:bg-violet-500/20 transition-colors shrink-0"
                      onClick={(e) => e.stopPropagation()}
                    >
                      {event.agent}
                    </Link>
                  )}
                  {event.detail && (
                    <span className="text-sm text-neutral-500 truncate">
                      {'\u2014 '}{linkifyDetail(event.detail, knownAgentsSet)}
                    </span>
                  )}
                </div>

                {/* Task ID — clickable */}
                {event.task_id && (
                  <Link
                    to={`/tasks?task=${encodeURIComponent(event.task_id)}`}
                    className="text-xs font-mono px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-400 hover:bg-amber-500/20 transition-colors shrink-0"
                    onClick={(e) => e.stopPropagation()}
                  >
                    {event.task_id}
                  </Link>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Fade-in animation style */}
      <style>{`
        @keyframes fadeIn {
          from { opacity: 0; transform: translateY(-4px); }
          to { opacity: 1; transform: translateY(0); }
        }
        .animate-fade-in {
          animation: fadeIn 0.4s ease-out;
        }
      `}</style>
    </div>
  );
}
