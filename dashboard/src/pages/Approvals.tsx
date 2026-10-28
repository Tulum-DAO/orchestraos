import { useState, useMemo, useRef, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { clsx } from 'clsx';
import {
  ShieldCheck, Brain, Bot, Rocket, ChevronDown, ChevronRight,
  CheckCircle2, XCircle,
} from 'lucide-react';
import { dismissMenuCard, post } from '../lib/api';
import {
  initialMenuAnswerState,
  selectOption,
  setText,
  buildMenuAnswerPayload,
  type MenuAnswerState,
  type MenuOption,
} from '../lib/menuAnswer';
import MenuAnswerBlock from '../components/chat/MenuAnswerCard';
import DecisionCard, { ApprovalAnswer } from '../components/chat/DecisionCard';
import ApprovalExtras from '../components/chat/ApprovalExtras';
import { approvalCardBody, approvalCardAgent } from '../lib/approvalCard';

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
  agent_id?: string | null;
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
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['unified-approvals'] }),
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

      {focusNotice && (
        <p role="status" className="text-sm text-neutral-300 rounded-lg border border-neutral-700 bg-neutral-900 px-3 py-2">{focusNotice}</p>
      )}

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
            const isMenu = item.kind === 'menu';
            const approving = approveMutation.isPending && approveMutation.variables === item.id;
            const denying = denyMutation.isPending && denyMutation.variables?.id === item.id;
            const approvalError = (approveMutation.variables === item.id && approveMutation.error?.message)
              || (denyMutation.variables?.id === item.id && denyMutation.error?.message) || null;
            return (
              <DecisionCard key={item.id} id={`approval-${item.id}`} highlighted={highlightId === item.id}
                kind={isMenu ? 'decision' : 'approval'} question={item.title} agent={approvalCardAgent(item) || null}
                risk={item.risk_level} body={approvalCardBody(item)} open
                extra={<ApprovalExtras item={item} expanded={expandedId === item.id}
                  onToggle={() => setExpandedId(expandedId === item.id ? null : item.id)} />}
                onDismiss={isMenu ? async () => {
                  const err = await dismissMenuCard(item.id, item.agent_id, item.menu?.question || item.title);
                  if (!err) void queryClient.invalidateQueries({ queryKey: ['unified-approvals'] });
                  return err;
                } : undefined}>
                {/* kind='menu' rows answer via option/free-text only (watch_gateway.py: "menu rows answer via
                    answer=='option' only"), so a menu card never shows Approve/Deny. */}
                {isMenu ? (
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
                  <ApprovalAnswer sending={approving || denying} error={approvalError}
                    onApprove={() => approveMutation.mutate(item.id)}
                    onDeny={(reason) => denyMutation.mutate({ id: item.id, reason })} />
                )}
              </DecisionCard>
            );
          })}
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
