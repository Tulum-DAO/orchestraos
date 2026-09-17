import { useState, useMemo, useRef } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { clsx } from 'clsx';
import {
  Inbox as InboxIcon, ShieldCheck, Brain, Bot, Rocket,
  ClipboardList, CheckCircle2, XCircle, X, User,
  ChevronDown, ChevronRight, ExternalLink, Clock, MessageSquare,
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

interface Questionnaire {
  id: string;
  title: string;
  description: string;
  question_count: number;
  status: 'pending' | 'completed';
  created_at: string;
  completed_at?: string;
  created_by: string;
  assigned_to: string;
}

/** Seat-to-seat mail row from GET /api/messages/recent (both directions of an exchange). */
interface MailRow {
  id: string; from_agent: string; to_agent: string; type: string | null; subject: string | null;
  priority: string | null; status: string | null; created_at: string | null; acknowledged_at: string | null;
}

type InboxItem =
  | { kind: 'approval'; data: UnifiedApproval; ts: number }
  | { kind: 'questionnaire'; data: Questionnaire; ts: number };


// ── Constants ────────────────────────────────────────────────

const TABS = [
  { key: 'all', label: 'All', icon: InboxIcon },
  { key: 'approvals', label: 'Approvals', icon: ShieldCheck },
  { key: 'questionnaires', label: 'Questionnaires', icon: ClipboardList },
] as const;

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

export default function Inbox() {
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<string>('all');
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [denyModalId, setDenyModalId] = useState<string | null>(null);
  const [denyReason, setDenyReason] = useState('');
  const denyInputRef = useRef<HTMLTextAreaElement>(null);

  // Fetch approvals
  const { data: approvalsData, isLoading: approvalsLoading } = useQuery({
    queryKey: ['unified-approvals'],
    queryFn: () => fetchJson<{ items: UnifiedApproval[]; total: number }>('/approvals/unified'),
    refetchInterval: 5000,
  });

  // Seat-to-seat mail (Tier 0 item 3: "two seats exchange a message; both visible in Inbox")
  const { data: mailData } = useQuery({
    queryKey: ['messages-recent'],
    queryFn: () => fetchJson<{ messages: MailRow[] }>('/messages/recent?limit=40'),
    refetchInterval: 5000,
  });
  const mail = mailData?.messages ?? [];

  // Fetch questionnaires
  const { data: qData, isLoading: qLoading } = useQuery({
    queryKey: ['questionnaires'],
    queryFn: () => fetchJson<{ questionnaires: Questionnaire[]; total: number; pending: number }>('/questionnaires'),
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

  // V3 universal Respond for kind='menu' rows — see Approvals.tsx for the
  // full contract note (A1/A2/A3, D4). Same route, same behavior, both
  // surfaces read the SAME canonical feed.
  const [menuAnswers, setMenuAnswers] = useState<Record<string, MenuAnswerState>>({});
  const getMenuAnswer = (id: string) => menuAnswers[id] ?? initialMenuAnswerState;
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
      setMenuAnswerError((m) => ({ ...m, [id]: err?.message || 'Could not send — try again.' }));
    },
  });

  // Merge into unified feed
  const pendingApprovals = (approvalsData?.items ?? []).filter(i => i.status === 'pending');
  const pendingQuestionnaires = (qData?.questionnaires ?? []).filter(q => q.status === 'pending');

  const allItems: InboxItem[] = useMemo(() => {
    const items: InboxItem[] = [];

    for (const a of pendingApprovals) {
      items.push({ kind: 'approval', data: a, ts: new Date(a.created_at || 0).getTime() });
    }

    for (const q of pendingQuestionnaires) {
      items.push({ kind: 'questionnaire', data: q, ts: new Date(q.created_at || 0).getTime() });
    }

    return items.sort((a, b) => b.ts - a.ts);
  }, [pendingApprovals, pendingQuestionnaires]);

  const filtered = useMemo(() => {
    if (tab === 'all') return allItems;
    if (tab === 'approvals') return allItems.filter(i => i.kind === 'approval');
    if (tab === 'questionnaires') return allItems.filter(i => i.kind === 'questionnaire');
    return allItems;
  }, [allItems, tab]);

  const totalPending = allItems.length;
  const isLoading = approvalsLoading || qLoading;

  if (isLoading) return <p className="text-neutral-500 p-8">Loading...</p>;

  return (
    <div className="p-6 space-y-5">
      {/* Header */}
      <div className="flex items-center gap-3">
        <h1 className="text-2xl font-bold text-white">Inbox</h1>
        {totalPending > 0 && (
          <span className="bg-red-600 text-white text-xs font-bold px-2 py-0.5 rounded-full animate-pulse">
            {totalPending}
          </span>
        )}
        <div className="ml-auto text-xs text-neutral-500">
          {pendingApprovals.length} approvals · {pendingQuestionnaires.length} questionnaires
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 bg-neutral-900 rounded-lg p-1 overflow-x-auto">
        {TABS.map(({ key, label, icon: Icon }) => {
          const count = key === 'all'
            ? totalPending
            : key === 'approvals'
              ? pendingApprovals.length
              : pendingQuestionnaires.length;
          return (
            <button
              key={key}
              onClick={() => setTab(key)}
              className={clsx(
                'flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md transition-colors whitespace-nowrap',
                tab === key ? 'bg-neutral-700 text-white' : 'text-neutral-500 hover:text-neutral-300'
              )}
            >
              <Icon size={13} />
              {label}
              {count > 0 && (
                <span className="bg-red-600/80 text-white text-[10px] font-bold px-1.5 py-0.5 rounded-full ml-0.5">{count}</span>
              )}
            </button>
          );
        })}
      </div>

      {/* Empty state */}
      {filtered.length === 0 && (
        <div className="flex flex-col items-center justify-center py-16 text-neutral-500">
          <InboxIcon className="w-12 h-12 mb-3 text-neutral-600" />
          <p className="text-lg font-medium text-neutral-400">Inbox clear</p>
          <p className="text-sm">No pending items{tab !== 'all' ? ` in ${tab}` : ''}</p>
        </div>
      )}

      {/* Items */}
      <div className="space-y-3">
        {filtered.map((item) => {
          if (item.kind === 'approval') {
            const a = item.data;
            const isExpanded = expandedId === a.id;

            return (
              <div key={`a-${a.id}`} className={clsx(
                'rounded-xl border border-neutral-800 bg-neutral-900 overflow-hidden border-l-4',
                URGENCY_BORDER[a.urgency],
                a.urgency === 'critical' && 'shadow-[0_0_12px_rgba(239,68,68,0.3)]'
              )}>
                <div className="p-4 space-y-3">
                  {/* Badges */}
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-[10px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded bg-violet-500/15 text-violet-400 border border-violet-500/30">
                      approval
                    </span>
                    {a.type === 'learning' && (
                      <span className="text-[10px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded bg-blue-500/10 text-blue-400 border border-blue-500/30 flex items-center gap-1">
                        <Brain size={9} /> Learning
                      </span>
                    )}
                    {a.type === 'agent' && (
                      <span className="text-[10px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded bg-green-500/10 text-green-400 border border-green-500/30 flex items-center gap-1">
                        <Bot size={9} /> Agent
                      </span>
                    )}
                    {a.type === 'deployment' && (
                      <span className="text-[10px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded bg-cyan-500/10 text-cyan-400 border border-cyan-500/30 flex items-center gap-1">
                        <Rocket size={9} /> Deploy
                      </span>
                    )}
                    {a.pattern_type === 'correction' && (
                      <span className="text-[10px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-400 border border-amber-500/30">
                        correction
                      </span>
                    )}
                    {a.initiated_by === 'operator' && (
                      <span className="text-[10px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded bg-blue-500/15 text-blue-400 border border-blue-500/30 flex items-center gap-1">
                        <User size={9} /> the operator
                      </span>
                    )}
                    <span className={clsx('text-[10px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded border', URGENCY_BADGE[a.urgency])}>
                      {a.urgency}
                    </span>
                    <span className={clsx('text-xs', RISK_COLORS[a.risk_level])}>
                      {a.risk_level} risk
                    </span>
                  </div>

                  <h3 className="text-sm font-medium text-neutral-100 leading-snug">{a.title}</h3>

                  {a.mechanism_label && (
                    <p className="text-xs text-neutral-500">
                      {a.mechanism_label} · {relativeTime(a.created_at)}
                    </p>
                  )}

                  {/* Sandbox badge */}
                  {a.sandbox_result ? (
                    <div className={clsx(
                      'flex items-center gap-2 text-xs px-3 py-1.5 rounded-lg',
                      a.sandbox_result.passed ? 'bg-green-500/10 text-green-400' : 'bg-red-500/10 text-red-400'
                    )}>
                      {a.sandbox_result.passed ? <CheckCircle2 size={14} /> : <XCircle size={14} />}
                      Sandbox: {a.sandbox_result.passed ? 'PASS' : 'FAIL'} ({a.sandbox_result.total || 0} tests)
                    </div>
                  ) : null}

                  {/* Evidence */}
                  {a.evidence_summary && (
                    <p className="text-xs text-neutral-400 bg-neutral-800/70 rounded-lg px-3 py-2 leading-relaxed italic">
                      {a.evidence_summary}
                    </p>
                  )}

                  {/* Rule text */}
                  {a.rule_text && (
                    <div className="bg-neutral-950 border border-neutral-800 rounded-lg px-3 py-2">
                      <p className="text-[10px] text-neutral-600 uppercase tracking-wider mb-1">Rule that will be added</p>
                      <p className="text-xs text-neutral-300 font-mono leading-relaxed">{a.rule_text}</p>
                    </div>
                  )}

                  {/* One-pager expand */}
                  {a.onepager_url && (
                    <button
                      onClick={() => setExpandedId(isExpanded ? null : a.id)}
                      className="flex items-center gap-1.5 text-xs text-neutral-400 hover:text-neutral-200 transition-colors"
                    >
                      {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                      <ExternalLink size={12} />
                      {isExpanded ? 'Hide details' : 'View full analysis'}
                    </button>
                  )}

                  {isExpanded && a.onepager_url && (
                    <div className="rounded-lg overflow-hidden border border-neutral-700 bg-black">
                      <iframe src={a.onepager_url} className="w-full border-0" style={{ minHeight: '500px' }} title="Proposal details" />
                    </div>
                  )}

                  {/* kind='menu' rows answer via option/free-text only (watch_gateway.py:654-656) */}
                  {a.kind === 'menu' ? (
                    <MenuAnswerBlock
                      item={a}
                      state={getMenuAnswer(a.id)}
                      onSelect={(n) => setMenuAnswers((m) => ({ ...m, [a.id]: selectOption(getMenuAnswer(a.id), n) }))}
                      onText={(t) => setMenuAnswers((m) => ({ ...m, [a.id]: setText(getMenuAnswer(a.id), t) }))}
                      onRespond={() => {
                        const payload = buildMenuAnswerPayload(getMenuAnswer(a.id));
                        if (!payload) return;
                        answerMenuMutation.mutate({ id: a.id, ...payload });
                      }}
                      sending={answerMenuMutation.isPending && answerMenuMutation.variables?.id === a.id}
                      error={menuAnswerError[a.id]}
                    />
                  ) : (
                    <div className="flex gap-2 pt-1">
                      <button
                        onClick={() => approveMutation.mutate(a.id)}
                        disabled={approveMutation.isPending}
                        className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-green-600 hover:bg-green-500 text-white text-sm font-medium transition-colors disabled:opacity-50"
                      >
                        <CheckCircle2 size={15} />
                        Approve
                      </button>
                      <button
                        onClick={() => { setDenyModalId(a.id); setDenyReason(''); setTimeout(() => denyInputRef.current?.focus(), 100); }}
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
          }

          // Questionnaire item
          const q = item.data;
          const isExpanded = expandedId === `q-${q.id}`;

          return (
            <div key={`q-${q.id}`} className="rounded-xl border-l-4 border-l-amber-500 border border-neutral-800 bg-neutral-900 overflow-hidden">
              <div className="p-4 space-y-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-[10px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-400 border border-amber-500/30 flex items-center gap-1">
                    <ClipboardList size={9} /> questionnaire
                  </span>
                  <span className="text-xs text-neutral-500 flex items-center gap-1">
                    <MessageSquare size={11} /> {q.question_count} questions
                  </span>
                  <span className="text-xs text-neutral-500 flex items-center gap-1">
                    <Clock size={11} /> {relativeTime(q.created_at)}
                  </span>
                </div>

                <h3 className="text-sm font-medium text-neutral-100 leading-snug">{q.title}</h3>
                <p className="text-xs text-neutral-500">{q.description}</p>

                <button
                  onClick={() => setExpandedId(isExpanded ? null : `q-${q.id}`)}
                  className="flex items-center gap-1.5 text-xs text-amber-400 hover:text-amber-300 transition-colors font-medium"
                >
                  {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                  {isExpanded ? 'Close questionnaire' : 'Open questionnaire'}
                </button>

                {isExpanded && (
                  <div className="rounded-lg overflow-hidden border border-neutral-700 bg-black">
                    <iframe
                      src={`/api/questionnaires/${q.id}/html`}
                      className="w-full border-0"
                      style={{ minHeight: '600px' }}
                      title={q.title}
                    />
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Seat mail — every message between seats, newest first, with its delivery/ack state */}
      <div className="space-y-2">
        <div className="flex items-center gap-2 text-sm font-semibold text-neutral-300">
          <MessageSquare size={14} /> Seat mail
          <span className="ml-auto text-xs font-normal text-neutral-500">{mail.length} recent · msg_store</span>
        </div>
        {mail.length === 0 ? (
          <p className="text-xs text-neutral-500">No seat-to-seat messages yet. Send one: <code>python3 msg_store.py send --from a --to b --subject hi --body-file note.txt</code></p>
        ) : (
          <div className="divide-y divide-neutral-800 rounded-lg border border-neutral-800 bg-neutral-900/60">
            {mail.map((m) => (
              <div key={m.id} className="flex items-center gap-3 px-3 py-2 text-xs" data-testid="mail-row">
                <span className="font-mono text-neutral-400 w-44 truncate" title={m.id}>{m.id}</span>
                <span className="text-neutral-200 truncate">
                  <span className="text-sky-400">{m.from_agent}</span> → <span className="text-emerald-400">{m.to_agent}</span>
                </span>
                <span className="text-neutral-400 truncate flex-1">{m.subject ?? ''}</span>
                <span className={clsx('px-1.5 py-0.5 rounded font-medium',
                  m.status === 'acknowledged' ? 'bg-emerald-900/50 text-emerald-300'
                  : m.status === 'pending' ? 'bg-amber-900/50 text-amber-300' : 'bg-neutral-800 text-neutral-300')}>
                  {m.status ?? '?'}
                </span>
                <span className="text-neutral-500 w-24 text-right" title={m.created_at ?? ''}>{m.created_at ? relativeTime(m.created_at) : ''}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Deny modal */}
      {denyModalId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" onClick={() => setDenyModalId(null)}>
          <div className="bg-neutral-900 border border-neutral-700 rounded-2xl w-full max-w-md shadow-2xl p-6 space-y-4" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between">
              <h3 className="text-lg font-semibold text-white">Why are you denying this?</h3>
              <button onClick={() => setDenyModalId(null)} className="text-neutral-500 hover:text-neutral-300">
                <X size={20} />
              </button>
            </div>
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
    </div>
  );
}
