import { clsx } from 'clsx';
import { CheckCircle2, ChevronDown, ChevronRight, ExternalLink, ShieldCheck, User, XCircle } from 'lucide-react';
import { operatorUserId } from '../../lib/runtimeConfig';

/** The fields of an Approvals/Inbox row that show under a DecisionCard's text. */
export interface ApprovalExtrasItem {
  type: string;
  urgency: string;
  created_at: string;
  evidence_summary?: string | null;
  qa_verdict?: string | null;
  sandbox_result?: { passed: boolean; total: number; failed?: string[]; failed_tests?: string[] } | null;
  onepager_url?: string | null;
  mechanism_label?: string;
  rule_text?: string;
  pattern_type?: string;
  initiated_by?: string;
}

const URGENCY_TEXT: Record<string, string> = { low: 'text-success', normal: 'text-warning', critical: 'text-destructive' };

function relativeTime(ts: string): string {
  const diff = Date.now() - new Date(ts).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

/** What a page-level approval card shows under its text: who raised it and how urgent, how it would work, its
 *  sandbox result, the evidence, the rule it would add, and the full analysis. */
export default function ApprovalExtras({ item, expanded, onToggle }: { item: ApprovalExtrasItem; expanded: boolean; onToggle: () => void }) {
  const failed = item.sandbox_result?.failed || item.sandbox_result?.failed_tests || [];
  const tags = [
    item.pattern_type === 'correction' && <span key="c" className="text-warning">correction</span>,
    item.pattern_type === 'failure' && <span key="f" className="text-destructive">failure</span>,
    item.initiated_by === operatorUserId() && <span key="o" className="flex items-center gap-1"><User size={10} /> the operator</span>,
    item.urgency !== 'normal' && <span key="u" className={URGENCY_TEXT[item.urgency]}>{item.urgency}</span>,
    item.qa_verdict && <span key="q">QA: <span className={item.qa_verdict === 'APPROVE' ? 'text-success' : 'text-warning'}>{item.qa_verdict}</span></span>,
  ].filter(Boolean);
  return (
    <>
      {(tags.length > 0 || item.mechanism_label) && (
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
          {tags}
          {item.mechanism_label && <span>{item.mechanism_label} · {relativeTime(item.created_at)}</span>}
        </div>
      )}
      {item.sandbox_result ? (
        <div className={clsx('flex flex-wrap items-center gap-2 text-xs px-3 py-1.5 rounded-lg',
          item.sandbox_result.passed ? 'bg-success/10 text-success' : 'bg-destructive/10 text-destructive')}>
          {item.sandbox_result.passed ? <CheckCircle2 size={14} /> : <XCircle size={14} />}
          Sandbox: {item.sandbox_result.passed ? 'PASS' : 'FAIL'} ({item.sandbox_result.total || 0} tests)
          {failed.length > 0 && <span className="ml-1">Failed: {failed.join(', ')}</span>}
        </div>
      ) : item.type === 'learning' && (
        <div className="flex items-center gap-2 text-xs px-3 py-1.5 rounded-lg bg-muted text-muted-foreground">
          <ShieldCheck size={14} /> Sandbox: not tested
        </div>
      )}
      {item.evidence_summary && (
        <p className="text-xs text-muted-foreground bg-muted rounded-lg px-3 py-2 leading-relaxed italic">{item.evidence_summary}</p>
      )}
      {item.rule_text && (
        <div className="border border-border rounded-lg px-3 py-2">
          <p className="text-[10px] text-muted-foreground uppercase tracking-wider mb-1">Rule that will be added</p>
          <p className="text-xs font-mono leading-relaxed">{item.rule_text}</p>
        </div>
      )}
      {item.onepager_url && (
        <button onClick={onToggle} className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors">
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          <ExternalLink size={12} />
          {expanded ? 'Hide details' : 'View full analysis'}
        </button>
      )}
      {expanded && item.onepager_url && (
        <div className="rounded-lg overflow-hidden border border-border">
          <iframe src={item.onepager_url} className="w-full border-0" style={{ minHeight: '500px' }} title="Proposal details" />
        </div>
      )}
    </>
  );
}
