import { useState, useMemo, useRef } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { clsx } from 'clsx';
import {
  ShieldCheck, Brain, Bot, Rocket, ChevronDown, ChevronRight,
  ExternalLink, CheckCircle2, XCircle, X, User,
} from 'lucide-react';
import { post } from '../lib/api';
import {
  initialMenuAnswerState,
  selectOption,
  setText,
  buildMenuAnswerPayload,
  type MenuAnswerState,
  type MenuOption,
} from '../lib/menuAnswer';
import MenuAnswerBlock from '../components/chat/MenuAnswerCard';

async function fetchJson<T>(path: string): Promise<T> {
  const res = await fetch(`/api${path}`);
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

// ── Types ──────────────────────────────────────────────────────

interface UnifiedApproval {
  id: string;
  type: 'learning' | 'agent' | 'deployment';
  urgency: 'low' | 'normal' | 'critical';
  status: 'pending' | 'approved' | 'rejected';
  title: string;
  description: string;
  risk_level: 'low' | 'medium' | 'high';
  evidence_summary: string;
  qa_verdict: string | null;
  sandbox_result: { passed: boolean; total: number; failed?: string[]; failed_tests?: string[] } | null;
  onepager_url: string | null;
  created_at: string;
  updated_at: string;
  source: string;
  mechanism_label?: string;
  rule_text?: string;
  pattern_type?: string;
  initiated_by?: string;
  // gm-mine-menu-card seam 2: additive — undefined/null on every non-menu row.
  kind?: string | null;
  menu?: { options?: MenuOption[]; question?: string; read_only?: boolean; notice?: string } | null;
  options?: string[] | null;
}

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

// Urgency → left border color (Q6: colored left border)
const URGENCY_BORDER: Record<string, string> = {
  low: 'border-l-green-500',
  normal: 'border-l-amber-500',
  critical: 'border-l-red-500',
};

const URGENCY_BADGE: Record<string, string> = {
  low: 'text-green-400 bg-green-500/10 border-green-500/30',
  normal: 'text-amber-400 bg-amber-500/10 border-amber-500/30',
  critical: 'text-red-400 bg-red-500/10 border-red-500/30',
};

const RISK_COLORS: Record<string, string> = {
  low: 'text-green-400',
  medium: 'text-amber-400',
  high: 'text-red-400',
};

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
  const [denyModalId, setDenyModalId] = useState<string | null>(null);
  const [denyReason, setDenyReason] = useState('');
  const [resolvedOpen, setResolvedOpen] = useState(false);
  const denyInputRef = useRef<HTMLTextAreaElement>(null);
  // gm-mine-menu-card seam 2: per-row menu answer draft (option + free text),
  // independent state so A3 (selecting an option never wipes typed text) holds.
  const [menuAnswers, setMenuAnswers] = useState<Record<string, MenuAnswerState>>({});
  const getMenuAnswer = (id: string) => menuAnswers[id] ?? initialMenuAnswerState;

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

  const approveMutation = useMutation({
    mutationFn: (id: string) => post(`/approvals/unified/${id}/approve`, {}),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['unified-approvals'] }),
  });

  const denyMutation = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) =>
      post(`/approvals/unified/${id}/deny`, { reason }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['unified-approvals'] });
      setDenyModalId(null);
      setDenyReason('');
    },
  });

  // V3: kind='menu' universal Respond — routes through the SAME canonical
  // answer path approve/deny use (POST /:id/answer -> canonicalAnswer('option')
  // -> approval.py answer -> watch_gateway.apply_answer). D4: a 409/held
  // refusal is surfaced inline (menuAnswerError) and the typed draft (option +
  // text) is PRESERVED, never cleared, so the operator doesn't lose what he wrote.
  const [menuAnswerError, setMenuAnswerError] = useState<Record<string, string>>({});
  const answerMenuMutation = useMutation({
    mutationFn: ({ id, option_n, answer_text }: { id: string; option_n?: string; answer_text?: string }) =>
      post(`/approvals/unified/${id}/answer`, { option_n, answer_text }),
    onSuccess: (_data, { id }) => {
      queryClient.invalidateQueries({ queryKey: ['unified-approvals'] });
      setMenuAnswers((m) => { const next = { ...m }; delete next[id]; return next; });
      setMenuAnswerError((m) => { const next = { ...m }; delete next[id]; return next; });
    },
    onError: (err: any, { id }) => {
      // Held/refused (incl. 409 — answered elsewhere) — keep the draft intact.
      setMenuAnswerError((m) => ({ ...m, [id]: err?.message || 'Could not send — try again.' }));
    },
  });

  const items = data?.items ?? [];

  const filtered = useMemo(() => {
    if (tab === 'all') return items;
    return items.filter((i: UnifiedApproval) => i.type === tab);
  }, [items, tab]);

  const pending = filtered.filter((i: UnifiedApproval) => i.status === 'pending');
  const resolved = filtered.filter((i: UnifiedApproval) => i.status !== 'pending');
  const pendingCount = items.filter((i: UnifiedApproval) => i.status === 'pending').length;

  if (isLoading) return <p className="text-neutral-500 p-8">Loading...</p>;

  return (
    <div className="p-6 space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold text-white">Approvals</h1>
          {pendingCount > 0 && (
            <span className="bg-red-600 text-white text-xs font-bold px-2 py-0.5 rounded-full animate-pulse">
              {pendingCount}
            </span>
          )}
        </div>
        {stats && (
          <div className="hidden sm:flex items-center gap-3 text-xs text-neutral-500">
            <span>{stats.total} total</span>
            <span className="text-green-400">{stats.by_status?.approved || 0} approved</span>
            <span className="text-red-400">{stats.by_status?.rejected || 0} denied</span>
          </div>
        )}
      </div>

      {/* Tabs */}
      <div className="flex gap-1 bg-neutral-900 rounded-lg p-1 overflow-x-auto">
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
                tab === key ? 'bg-neutral-700 text-white' : 'text-neutral-500 hover:text-neutral-300'
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
        <div className="flex flex-col items-center justify-center py-16 text-neutral-500">
          <ShieldCheck className="w-12 h-12 mb-3 text-neutral-600" />
          <p className="text-lg font-medium text-neutral-400">Queue clear</p>
          <p className="text-sm">No pending approvals{tab !== 'all' ? ` in ${tab}` : ''}</p>
        </div>
      ) : (
        <div className="space-y-3">
          {pending.map((item: UnifiedApproval) => {
            const isExpanded = expandedId === item.id;

            return (
              // Q6: Colored left border by urgency
              <div key={item.id} className={clsx(
                'rounded-xl border border-neutral-800 bg-neutral-900 overflow-hidden border-l-4',
                URGENCY_BORDER[item.urgency],
                item.urgency === 'critical' && 'shadow-[0_0_12px_rgba(239,68,68,0.3)] animate-[border-pulse_2s_ease-in-out_infinite]'
              )}>
              <style>{`@keyframes border-pulse { 0%,100% { border-left-color: #ef4444; box-shadow: 0 0 8px rgba(239,68,68,0.2); } 50% { border-left-color: #f87171; box-shadow: 0 0 16px rgba(239,68,68,0.4); } }`}</style>
                <div className="p-4 space-y-3">
                  {/* Top row: badges */}
                  <div className="flex flex-wrap items-center gap-2">
                    {/* Q1: Source badges — "correction" + "the operator" */}
                    {item.pattern_type === 'correction' && (
                      <span className="text-[10px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-400 border border-amber-500/30">
                        correction
                      </span>
                    )}
                    {item.pattern_type === 'failure' && (
                      <span className="text-[10px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded bg-red-500/15 text-red-400 border border-red-500/30">
                        failure
                      </span>
                    )}
                    {item.initiated_by === 'operator' && (
                      <span className="text-[10px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded bg-blue-500/15 text-blue-400 border border-blue-500/30 flex items-center gap-1">
                        <User size={9} /> the operator
                      </span>
                    )}
                    <span className={clsx('text-[10px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded border', URGENCY_BADGE[item.urgency])}>
                      {item.urgency}
                    </span>
                    <span className={clsx('text-xs', RISK_COLORS[item.risk_level])}>
                      {item.risk_level} risk
                    </span>
                    {item.qa_verdict && (
                      <span className="text-xs text-neutral-500">
                        QA: <span className={item.qa_verdict === 'APPROVE' ? 'text-green-400' : 'text-amber-400'}>{item.qa_verdict}</span>
                      </span>
                    )}
                  </div>

                  {/* Q1: Clean title */}
                  <h3 className="text-sm font-medium text-neutral-100 leading-snug">{item.title}</h3>

                  {/* Q2: Human-readable mechanism label */}
                  {item.mechanism_label && (
                    <p className="text-xs text-neutral-500">
                      {item.mechanism_label} · {relativeTime(item.created_at)}
                    </p>
                  )}

                  {/* Q3: Sandbox result badge */}
                  {item.sandbox_result ? (
                    <div className={clsx(
                      'flex items-center gap-2 text-xs px-3 py-1.5 rounded-lg',
                      item.sandbox_result.passed ? 'bg-green-500/10 text-green-400' : 'bg-red-500/10 text-red-400'
                    )}>
                      {item.sandbox_result.passed ? <CheckCircle2 size={14} /> : <XCircle size={14} />}
                      Sandbox: {item.sandbox_result.passed ? 'PASS' : 'FAIL'} ({item.sandbox_result.total || 0} tests)
                      {(item.sandbox_result.failed?.length || item.sandbox_result.failed_tests?.length || 0) > 0 && (
                        <span className="text-red-300 ml-1">Failed: {(item.sandbox_result.failed || item.sandbox_result.failed_tests || []).join(', ')}</span>
                      )}
                    </div>
                  ) : (
                    <div className="flex items-center gap-2 text-xs px-3 py-1.5 rounded-lg bg-neutral-800 text-neutral-500">
                      <ShieldCheck size={14} />
                      Sandbox: not tested
                    </div>
                  )}

                  {/* Q4: Inline evidence */}
                  {item.evidence_summary && (
                    <p className="text-xs text-neutral-400 bg-neutral-800/70 rounded-lg px-3 py-2 leading-relaxed italic">
                      {item.evidence_summary}
                    </p>
                  )}

                  {/* Q5: Show the rule text */}
                  {item.rule_text && (
                    <div className="bg-neutral-950 border border-neutral-800 rounded-lg px-3 py-2">
                      <p className="text-[10px] text-neutral-600 uppercase tracking-wider mb-1">Rule that will be added</p>
                      <p className="text-xs text-neutral-300 font-mono leading-relaxed">{item.rule_text}</p>
                    </div>
                  )}

                  {/* One-pager expand */}
                  {item.onepager_url && (
                    <button
                      onClick={() => setExpandedId(isExpanded ? null : item.id)}
                      className="flex items-center gap-1.5 text-xs text-neutral-400 hover:text-neutral-200 transition-colors"
                    >
                      {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                      <ExternalLink size={12} />
                      {isExpanded ? 'Hide details' : 'View full analysis'}
                    </button>
                  )}

                  {isExpanded && item.onepager_url && (
                    <div className="rounded-lg overflow-hidden border border-neutral-700 bg-black">
                      <iframe src={item.onepager_url} className="w-full border-0" style={{ minHeight: '500px' }} title="Proposal details" />
                    </div>
                  )}

                  {/* kind='menu' rows answer via option/free-text only (watch_gateway.py:654-656:
                      "menu rows answer via answer=='option' only") — Approve/Deny never rendered here. */}
                  {item.kind === 'menu' ? (
                    <MenuAnswerBlock
                      item={item}
                      state={getMenuAnswer(item.id)}
                      onSelect={(n) => setMenuAnswers((m) => ({ ...m, [item.id]: selectOption(getMenuAnswer(item.id), n) }))}
                      onText={(t) => setMenuAnswers((m) => ({ ...m, [item.id]: setText(getMenuAnswer(item.id), t) }))}
                      onRespond={() => {
                        const payload = buildMenuAnswerPayload(getMenuAnswer(item.id));
                        if (!payload) return;
                        answerMenuMutation.mutate({ id: item.id, ...payload });
                      }}
                      sending={answerMenuMutation.isPending && answerMenuMutation.variables?.id === item.id}
                      error={menuAnswerError[item.id]}
                    />
                  ) : (
                    <div className="flex gap-2 pt-1">
                      <button
                        onClick={() => approveMutation.mutate(item.id)}
                        disabled={approveMutation.isPending}
                        className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-green-600 hover:bg-green-500 text-white text-sm font-medium transition-colors disabled:opacity-50"
                      >
                        <CheckCircle2 size={15} />
                        Approve
                      </button>
                      {/* Q7: Deny opens modal */}
                      <button
                        onClick={() => { setDenyModalId(item.id); setDenyReason(''); setTimeout(() => denyInputRef.current?.focus(), 100); }}
                        className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-neutral-800 hover:bg-red-500/20 text-neutral-400 hover:text-red-400 text-sm font-medium transition-colors border border-neutral-700"
                      >
                        <XCircle size={15} />
                        Deny
                      </button>
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Q7: Deny modal */}
      {denyModalId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" onClick={() => setDenyModalId(null)}>
          <div className="bg-neutral-900 border border-neutral-700 rounded-2xl w-full max-w-md shadow-2xl p-6 space-y-4" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between">
              <h3 className="text-lg font-semibold text-white">Why are you denying this?</h3>
              <button onClick={() => setDenyModalId(null)} className="text-neutral-500 hover:text-neutral-300">
                <X size={20} />
              </button>
            </div>
            <p className="text-xs text-neutral-500">Your feedback helps the learning engine avoid making the same suggestion again.</p>
            <textarea
              ref={denyInputRef}
              value={denyReason}
              onChange={(e) => setDenyReason(e.target.value)}
              placeholder="What was wrong with this proposal?"
              rows={3}
              className="w-full bg-neutral-950 border border-red-900/50 rounded-lg px-4 py-3 text-sm text-neutral-200 placeholder-neutral-600 focus:outline-none focus:border-red-500 resize-none"
            />
            <div className="flex gap-3 justify-end">
              <button
                onClick={() => setDenyModalId(null)}
                className="px-4 py-2 text-sm rounded-lg bg-neutral-800 text-neutral-400 hover:text-neutral-200 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={() => { if (denyReason.trim()) denyMutation.mutate({ id: denyModalId, reason: denyReason.trim() }); }}
                disabled={!denyReason.trim() || denyMutation.isPending}
                className="px-4 py-2 text-sm rounded-lg bg-red-600 text-white font-medium disabled:opacity-50 transition-colors"
              >
                Confirm Deny
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Resolved */}
      {resolved.length > 0 && (
        <div>
          <button
            onClick={() => setResolvedOpen(!resolvedOpen)}
            className="flex items-center gap-2 text-neutral-400 hover:text-neutral-200 transition-colors text-sm font-medium"
          >
            {resolvedOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            Recently Resolved
            <span className="text-neutral-600">{resolved.length}</span>
          </button>

          {resolvedOpen && (
            <div className="mt-3 space-y-2">
              {resolved.slice(0, 10).map((item: UnifiedApproval) => {
                const isApproved = item.status === 'approved';
                return (
                  <div key={item.id} className="flex items-center gap-3 px-4 py-3 rounded-xl border border-neutral-800 bg-neutral-900/50">
                    {isApproved
                      ? <CheckCircle2 size={16} className="text-green-500 shrink-0" />
                      : <XCircle size={16} className="text-red-500 shrink-0" />}
                    <div className="flex-1 min-w-0">
                      <p className="text-sm text-neutral-400 truncate">{item.title}</p>
                    </div>
                    <span className="text-xs text-neutral-600">{relativeTime(item.updated_at)}</span>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
