import { useState, useMemo } from 'react';
import QuestionnaireForm from '../components/QuestionnaireForm';
import { isLiveQuestionnaire } from '../lib/questionnaireForm';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { clsx } from 'clsx';
import {
  Inbox as InboxIcon, ShieldCheck,
  ClipboardList,
  ChevronDown, ChevronRight, Clock, MessageSquare,
} from 'lucide-react';
import { dismissMenuCard, post } from '../lib/api';
import type { MenuOption } from '../lib/menuAnswer';
import { MultiPartAnswer } from '../components/chat/MultiPartCard';
import DecisionQuestion from '../components/chat/DecisionQuestion';
import { isTerminalMenu } from '../lib/decisionCard';
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
  menu?: { options?: MenuOption[]; question?: string; read_only?: boolean; notice?: string; multipart?: boolean; fail_safe?: boolean } | null;
  options?: string[] | null;
  agent_id?: string | null;
  /** The row's dedup key: a terminal menu's is `menu:<session>:…` (lib/decisionCard isTerminalMenu). */
  op_key?: string | null;
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
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['unified-approvals'] }),
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

  if (isLoading) return <p className="text-tone-500 p-8">Loading...</p>;

  return (
    <div className="p-6 space-y-5">
      {/* Header */}
      <div className="flex items-center gap-3">
        <h1 className="text-2xl font-bold text-foreground">Inbox</h1>
        {totalPending > 0 && (
          <span className="bg-red-600 text-white text-xs font-bold px-2 py-0.5 rounded-full animate-pulse">
            {totalPending}
          </span>
        )}
        <div className="ml-auto text-xs text-tone-500">
          {pendingApprovals.length} approvals · {pendingQuestionnaires.length} questionnaires
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 bg-tone-900 rounded-lg p-1 overflow-x-auto">
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
                tab === key ? 'bg-tone-700 text-tone-white' : 'text-tone-500 hover:text-tone-300'
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
        <div className="flex flex-col items-center justify-center py-16 text-tone-500">
          <InboxIcon className="w-12 h-12 mb-3 text-tone-600" />
          <p className="text-lg font-medium text-tone-400">Inbox clear</p>
          <p className="text-sm">No pending items{tab !== 'all' ? ` in ${tab}` : ''}</p>
        </div>
      )}

      {/* Items */}
      <div className="space-y-3">
        {filtered.map((item) => {
          if (item.kind === 'approval') {
            const a = item.data;
            const isMenu = a.kind === 'menu';
            const approving = approveMutation.isPending && approveMutation.variables === a.id;
            const denying = denyMutation.isPending && denyMutation.variables?.id === a.id;
            const approvalError = (approveMutation.variables === a.id && approveMutation.error?.message)
              || (denyMutation.variables?.id === a.id && denyMutation.error?.message) || null;
            const dismiss = async () => {
              const err = await dismissMenuCard(a.id, a.agent_id, a.menu?.question || a.title);
              if (!err) void queryClient.invalidateQueries({ queryKey: ['unified-approvals'] });
              return err;
            };
            // A multi-part menu is read from the agent's screen part by part, then answered on the same card.
            if (isMenu && (a.menu?.multipart || a.menu?.fail_safe) && a.agent_id) {
              return (
                <MultiPartAnswer key={`a-${a.id}`} agentId={a.agent_id} agentName={approvalCardAgent(a) || null} cardId={a.id}
                  onDismiss={dismiss} />
              );
            }
            // A question (kind 'menu') is the one question card: a pick sends it, words go alone where the menu has a
            // write-in (DecisionQuestion). A menu the server sends read-only keeps its notice.
            if (isMenu && !a.menu?.read_only) {
              return (
                <DecisionQuestion key={`a-${a.id}`}
                  row={{ id: a.id, question: a.title, agent: approvalCardAgent(a) || null, risk: a.risk_level,
                    body: approvalCardBody(a), options: a.menu?.options ?? [], terminal: isTerminalMenu(a) }}
                  extra={<ApprovalExtras item={a} expanded={expandedId === a.id}
                    onToggle={() => setExpandedId(expandedId === a.id ? null : a.id)} />}
                  onAnswered={() => void queryClient.invalidateQueries({ queryKey: ['unified-approvals'] })}
                  onDismiss={dismiss} />
              );
            }
            return (
              <DecisionCard key={`a-${a.id}`} kind={isMenu ? 'decision' : 'approval'} question={a.title}
                agent={approvalCardAgent(a) || null} risk={a.risk_level} body={approvalCardBody(a)} open
                extra={<ApprovalExtras item={a} expanded={expandedId === a.id}
                  onToggle={() => setExpandedId(expandedId === a.id ? null : a.id)} />}
                onDismiss={isMenu ? dismiss : undefined}>
                {/* A menu sent read-only (a multi-part menu this client is not given whole) only says where to answer it. */}
                {isMenu ? (
                  <div className="mx-4 mb-3 text-xs text-warning bg-warning/10 border border-warning/30 rounded-xl px-3 py-2">
                    {a.menu?.notice || 'Multi-part menu — open the agent to answer.'}
                  </div>
                ) : (
                  <ApprovalAnswer sending={approving || denying} error={approvalError}
                    onApprove={() => approveMutation.mutate(a.id)}
                    onDeny={(reason) => denyMutation.mutate({ id: a.id, reason })} />
                )}
              </DecisionCard>
            );
          }

          // Questionnaire item: a live one is the one question card itself, with no panel around it.
          const q = item.data;
          if (isLiveQuestionnaire(q)) return <QuestionnaireForm key={`q-${q.id}`} id={q.id} />;
          const isExpanded = expandedId === `q-${q.id}`;

          return (
            <div key={`q-${q.id}`} className="rounded-xl border-l-4 border-l-amber-500 border border-tone-800 bg-tone-900 overflow-hidden">
              <div className="p-4 space-y-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-[10px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded bg-amber-500/15 text-tone-amber-400 border border-amber-500/30 flex items-center gap-1">
                    <ClipboardList size={9} /> questionnaire
                  </span>
                  <span className="text-xs text-tone-500 flex items-center gap-1">
                    <MessageSquare size={11} /> {q.question_count} questions
                  </span>
                  <span className="text-xs text-tone-500 flex items-center gap-1">
                    <Clock size={11} /> {relativeTime(q.created_at)}
                  </span>
                </div>

                <h3 className="text-sm font-medium text-tone-100 leading-snug">{q.title}</h3>
                <p className="text-xs text-tone-500">{q.description}</p>

                <button
                  onClick={() => setExpandedId(isExpanded ? null : `q-${q.id}`)}
                  className="flex items-center gap-1.5 text-xs text-tone-amber-400 hover:text-tone-amber-300 transition-colors font-medium"
                >
                  {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                  {isExpanded ? 'Close questionnaire' : 'Open questionnaire'}
                </button>

                {isExpanded && (
                  <div className="rounded-lg overflow-hidden border border-neutral-700 bg-black">
                    {(
                    <iframe
                      src={`/api/questionnaires/${q.id}/html`}
                      className="w-full border-0"
                      style={{ minHeight: '600px' }}
                      title={q.title}
                    />
                    )}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Seat mail — every message between seats, newest first, with its delivery/ack state */}
      <div className="space-y-2">
        <div className="flex items-center gap-2 text-sm font-semibold text-tone-300">
          <MessageSquare size={14} /> Seat mail
          <span className="ml-auto text-xs font-normal text-tone-500">{mail.length} recent · msg_store</span>
        </div>
        {mail.length === 0 ? (
          <p className="text-xs text-tone-500">No seat-to-seat messages yet. Send one: <code>python3 msg_store.py send --from a --to b --subject hi --body-file note.txt</code></p>
        ) : (
          <div className="divide-y divide-tone-800 rounded-lg border border-tone-800 bg-tone-900/60">
            {mail.map((m) => (
              <div key={m.id} className="flex items-center gap-3 px-3 py-2 text-xs" data-testid="mail-row">
                <span className="font-mono text-tone-400 w-44 truncate" title={m.id}>{m.id}</span>
                <span className="text-tone-200 truncate">
                  <span className="text-tone-sky-400">{m.from_agent}</span> → <span className="text-tone-emerald-400">{m.to_agent}</span>
                </span>
                <span className="text-tone-400 truncate flex-1">{m.subject ?? ''}</span>
                <span className={clsx('px-1.5 py-0.5 rounded font-medium',
                  m.status === 'acknowledged' ? 'bg-tone-emerald-900/50 text-tone-emerald-300'
                  : m.status === 'pending' ? 'bg-tone-amber-900/50 text-tone-amber-300' : 'bg-tone-800 text-tone-300')}>
                  {m.status ?? '?'}
                </span>
                <span className="text-tone-500 w-24 text-right" title={m.created_at ?? ''}>{m.created_at ? relativeTime(m.created_at) : ''}</span>
              </div>
            ))}
          </div>
        )}
      </div>

    </div>
  );
}
