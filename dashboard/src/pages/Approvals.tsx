import { useState, useMemo, useRef, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { clsx } from 'clsx';
import {
  ShieldCheck, Brain, Bot, Rocket, ChevronDown, ChevronRight,
  CheckCircle2, XCircle,
} from 'lucide-react';
import PendingApprovalCard, { type UnifiedApproval } from '../components/chat/PendingApprovalCard';
import { CardSurface } from '../lib/threadKit/useScreenCard';

async function fetchJson<T>(path: string): Promise<T> {
  const res = await fetch(`/api${path}`);
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

// ── Types ──────────────────────────────────────────────────────

interface Stats {
  by_type: Record<string, number>;
  by_urgency: Record<string, number>;
  by_status: Record<string, number>;
  total: number;
}

// ── Constants ─────────────────────────────────────────────────

const TABS: { key: string; label: string; icon?: typeof Brain }[] = [
  { key: 'all', label: 'All' },
  { key: 'learning', label: 'Learning', icon: Brain },
  { key: 'agent', label: 'Agent', icon: Bot },
  { key: 'deployment', label: 'Deploy', icon: Rocket },
];

function relativeTime(ts: string): string {
  const diff = Date.now() - new Date(ts).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

// ── Component ──────────────────────────────────────────────────

export default function Approvals() {
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<string>('all');
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [resolvedOpen, setResolvedOpen] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ['unified-approvals'],
    queryFn: () => fetchJson<{ items: UnifiedApproval[]; total: number }>('/approvals/unified'),
    refetchInterval: 5000,
  });

  const { data: stats } = useQuery({
    queryKey: ['unified-approvals-stats'],
    queryFn: () => fetchJson<Stats>('/approvals/unified/stats'),
    refetchInterval: 10000,
  });

  const items = data?.items ?? [];

  // ONE-SHOT FOCUS (/approvals?focus=<id>): the bell sends a card here when the agent's chat does not
  // draw it (filed before the agent's current generation, or an on-screen menu mirror). Scroll to it
  // and highlight it briefly where it sits; if it is no longer waiting, say so. The param is then
  // dropped, so a reload or a later visit does not jump again.
  const [searchParams, setSearchParams] = useSearchParams();
  const focusId = searchParams.get('focus');
  const [highlightId, setHighlightId] = useState<string | null>(null);
  const [focusNotice, setFocusNotice] = useState<string | null>(null);
  // Timers outlive the effect run: dropping the param re-runs it, and that must not cancel the scroll.
  const focusTimers = useRef<number[]>([]);
  useEffect(() => () => focusTimers.current.forEach((t) => window.clearTimeout(t)), []);
  useEffect(() => {
    if (!focusId || !data) return;
    const found = (data.items ?? []).find((i) => i.id === focusId);
    const timers = focusTimers.current;
    if (found?.status === 'pending') {
      timers.push(window.setTimeout(() => { setTab('all'); setExpandedId(focusId); setHighlightId(focusId); }, 0));
      timers.push(window.setTimeout(() => document.getElementById(`approval-${focusId}`)?.scrollIntoView({ block: 'center', behavior: 'smooth' }), 80));
      timers.push(window.setTimeout(() => setHighlightId(null), 2600));
    } else {
      timers.push(window.setTimeout(() => setFocusNotice('That card is no longer waiting — it was answered elsewhere.'), 0));
      timers.push(window.setTimeout(() => setFocusNotice(null), 6000));
    }
    setSearchParams((p) => { const next = new URLSearchParams(p); next.delete('focus'); return next; }, { replace: true });
  }, [focusId, data, setSearchParams]);

  const filtered = useMemo(() => {
    if (tab === 'all') return items;
    return items.filter((i: UnifiedApproval) => i.type === tab);
  }, [items, tab]);

  const pending = filtered.filter((i: UnifiedApproval) => i.status === 'pending');
  const resolved = filtered.filter((i: UnifiedApproval) => i.status !== 'pending');
  const pendingCount = items.filter((i: UnifiedApproval) => i.status === 'pending').length;

  if (isLoading) return <p className="text-tone-500 p-8">Loading...</p>;

  return (
    <CardSurface.Provider value="approvals">
    <div className="p-6 space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold text-foreground">Approvals</h1>
          {pendingCount > 0 && (
            <span className="bg-red-600 text-white text-xs font-bold px-2 py-0.5 rounded-full animate-pulse">
              {pendingCount}
            </span>
          )}
        </div>
        {stats && (
          <div className="hidden sm:flex items-center gap-3 text-xs text-tone-500">
            <span>{stats.total} total</span>
            <span className="text-tone-green-400">{stats.by_status?.approved || 0} approved</span>
            <span className="text-tone-red-400">{stats.by_status?.rejected || 0} denied</span>
          </div>
        )}
      </div>

      {focusNotice && (
        <p role="status" className="text-sm text-tone-300 rounded-lg border border-tone-700 bg-tone-900 px-3 py-2">{focusNotice}</p>
      )}

      {/* Tabs */}
      <div className="flex gap-1 bg-tone-900 rounded-lg p-1 overflow-x-auto">
        {TABS.map(({ key, label, icon: Icon }) => {
          const count = key === 'all'
            ? items.filter((i: UnifiedApproval) => i.status === 'pending').length
            : items.filter((i: UnifiedApproval) => i.type === key && i.status === 'pending').length;
          return (
            <button
              key={key}
              onClick={() => setTab(key)}
              className={clsx(
                'flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md transition-colors whitespace-nowrap',
                tab === key ? 'bg-tone-700 text-tone-white' : 'text-tone-500 hover:text-tone-300'
              )}
            >
              {Icon && <Icon size={13} />}
              {label}
              {count > 0 && (
                <span className="bg-red-600/80 text-white text-[10px] font-bold px-1.5 py-0.5 rounded-full ml-0.5">{count}</span>
              )}
            </button>
          );
        })}
      </div>

      {/* Pending */}
      {pending.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 text-tone-500">
          <ShieldCheck className="w-12 h-12 mb-3 text-tone-600" />
          <p className="text-lg font-medium text-tone-400">Queue clear</p>
          <p className="text-sm">No pending approvals{tab !== 'all' ? ` in ${tab}` : ''}</p>
        </div>
      ) : (
        <div className="space-y-3">
          {pending.map((item: UnifiedApproval) => (
            <PendingApprovalCard key={item.id} item={item} highlighted={highlightId === item.id}
              expanded={expandedId === item.id} onToggle={() => setExpandedId(expandedId === item.id ? null : item.id)}
              onChanged={() => void queryClient.invalidateQueries({ queryKey: ['unified-approvals'] })} />
          ))}
        </div>
      )}

      {/* Resolved */}
      {resolved.length > 0 && (
        <div>
          <button
            onClick={() => setResolvedOpen(!resolvedOpen)}
            className="flex items-center gap-2 text-tone-400 hover:text-tone-200 transition-colors text-sm font-medium"
          >
            {resolvedOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            Recently Resolved
            <span className="text-tone-600">{resolved.length}</span>
          </button>

          {resolvedOpen && (
            <div className="mt-3 space-y-2">
              {resolved.slice(0, 10).map((item: UnifiedApproval) => {
                const isApproved = item.status === 'approved';
                return (
                  <div key={item.id} className="flex items-center gap-3 px-4 py-3 rounded-xl border border-tone-800 bg-tone-900/50">
                    {isApproved
                      ? <CheckCircle2 size={16} className="text-green-500 shrink-0" />
                      : <XCircle size={16} className="text-red-500 shrink-0" />}
                    <div className="flex-1 min-w-0">
                      <p className="text-sm text-tone-400 truncate">{item.title}</p>
                    </div>
                    <span className="text-xs text-tone-600">{relativeTime(item.updated_at)}</span>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
    </CardSurface.Provider>
  );
}
