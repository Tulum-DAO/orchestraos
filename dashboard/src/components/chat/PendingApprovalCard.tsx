/**
 * PendingApprovalCard — one waiting row of the unified approvals feed (GET /api/approvals/unified), drawn and
 * answered exactly as the Approvals page draws it: a multi-part menu as MultiPartAnswer (read part by part, answered
 * on the same card), a question as the one question card (DecisionQuestion), an approval with Approve and Deny, and
 * a menu sent read-only with the notice that says where to answer it. The Approvals page and a card reference's
 * popover (components/kit/CardChip.tsx) both draw it, so an answer from either goes the same route.
 */
import { useState } from 'react';
import { dismissMenuCard, post } from '../../lib/api';
import type { MenuOption } from '../../lib/menuAnswer';
import { MultiPartAnswer } from './MultiPartCard';
import DecisionQuestion from './DecisionQuestion';
import { isTerminalMenu } from '../../lib/decisionCard';
import DecisionCard, { ApprovalAnswer } from './DecisionCard';
import ApprovalExtras from './ApprovalExtras';
import { approvalCardBody, approvalCardAgent } from '../../lib/approvalCard';

export interface UnifiedApproval {
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

export default function PendingApprovalCard({ item, highlighted, onChanged, id, expanded: shownExpanded, onToggle }: {
  item: UnifiedApproval;
  highlighted?: boolean;
  /** The page's own "one card's details open at a time"; the card keeps its own when not given. */
  expanded?: boolean;
  onToggle?: () => void;
  /** After an answer or a dismiss: refresh the list the card came from. */
  onChanged: () => void;
  id?: string;
}) {
  const [ownExpanded, setExpanded] = useState(false);
  const expanded = shownExpanded ?? ownExpanded;
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const isMenu = item.kind === 'menu';
  const domId = id ?? `approval-${item.id}`;
  const extra = <ApprovalExtras item={item} expanded={expanded} onToggle={onToggle ?? (() => setExpanded((v) => !v))} />;
  const dismiss = async () => {
    const err = await dismissMenuCard(item.id, item.agent_id, item.menu?.question || item.title);
    if (!err) onChanged();
    return err;
  };
  const send = async (verb: 'approve' | 'deny', body: Record<string, unknown>) => {
    setSending(true); setError(null);
    try { await post(`/approvals/unified/${encodeURIComponent(item.id)}/${verb}`, body); onChanged(); }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not send'); }
    finally { setSending(false); }
  };

  // A multi-part menu is read from the agent's screen part by part, then answered on the same card.
  if (isMenu && (item.menu?.multipart || item.menu?.fail_safe) && item.agent_id) {
    return (
      <MultiPartAnswer id={domId} highlighted={highlighted}
        agentId={item.agent_id} agentName={approvalCardAgent(item) || null} cardId={item.id} onDismiss={dismiss}
        onAnswered={onChanged} />
    );
  }
  // A question (kind 'menu') is the one question card: a pick sends it, words go alone where the menu has a
  // write-in (DecisionQuestion). A menu the server sends read-only keeps its notice.
  if (isMenu && !item.menu?.read_only) {
    return (
      <DecisionQuestion id={domId} highlighted={highlighted}
        row={{ id: item.id, question: item.title, agent: approvalCardAgent(item) || null, risk: item.risk_level,
          body: approvalCardBody(item), options: item.menu?.options ?? [], terminal: isTerminalMenu(item) }}
        extra={extra} onAnswered={onChanged} onDismiss={dismiss} />
    );
  }
  return (
    <DecisionCard id={domId} highlighted={highlighted}
      kind={isMenu ? 'decision' : 'approval'} question={item.title} agent={approvalCardAgent(item) || null}
      risk={item.risk_level} body={approvalCardBody(item)} open extra={extra}
      onDismiss={isMenu ? dismiss : undefined} report={{ id: item.id, from: approvalCardAgent(item) || null }}>
      {/* A menu sent read-only (a multi-part menu this client is not given whole) only says where to answer it. */}
      {isMenu ? (
        <div className="mx-4 mb-3 text-xs text-warning bg-warning/10 border border-warning/30 rounded-xl px-3 py-2">
          {item.menu?.notice || 'Multi-part menu — open the agent to answer.'}
        </div>
      ) : (
        <ApprovalAnswer sending={sending} error={error}
          onApprove={() => void send('approve', {})}
          onDeny={(reason) => void send('deny', { reason })} />
      )}
    </DecisionCard>
  );
}
