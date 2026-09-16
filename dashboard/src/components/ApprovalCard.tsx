import { clsx } from 'clsx';
import { APPROVAL_CATEGORY_COLORS } from '../lib/constants';

function relativeTime(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

const RISK_COLORS: Record<string, string> = {
  low: 'text-green-400',
  medium: 'text-yellow-400',
  high: 'text-orange-400',
  critical: 'text-red-400',
};

interface ApprovalCardProps {
  approval: any;
  onApprove?: (id: string) => void;
  onDeny?: (id: string) => void;
  isResolved?: boolean;
}

export function ApprovalCard({ approval, onApprove, onDeny, isResolved }: ApprovalCardProps) {
  const categoryClass = APPROVAL_CATEGORY_COLORS[approval.category] || 'bg-neutral-700 text-neutral-300';
  const riskClass = RISK_COLORS[approval.risk] || 'text-neutral-400';

  return (
    <div className={clsx(
      'rounded-xl border p-4 space-y-3',
      isResolved
        ? 'border-neutral-800/50 bg-neutral-900/50'
        : 'border-neutral-800 bg-neutral-900'
    )}>
      {/* Top row: badges + agent + timestamp */}
      <div className="flex flex-wrap items-center gap-2">
        {approval.category && (
          <span className={clsx('text-xs px-2 py-0.5 rounded-full font-medium', categoryClass)}>
            {approval.category.replace(/_/g, ' ')}
          </span>
        )}
        {approval.risk && (
          <span className={clsx('text-xs font-medium uppercase tracking-wide', riskClass)}>
            {approval.risk} risk
          </span>
        )}
        {approval.agent_id && (
          <span className="text-xs text-neutral-500">
            from <span className="text-neutral-300">{approval.agent_id}</span>
          </span>
        )}
        <span className="text-xs text-neutral-600 ml-auto">
          {approval.created ? relativeTime(approval.created) : ''}
        </span>
      </div>

      {/* Action description */}
      <p className="text-white font-medium text-sm">{approval.action || 'No action description'}</p>

      {/* Detail block */}
      {approval.detail && (
        <pre className="text-xs text-neutral-400 bg-neutral-950 rounded-lg p-3 overflow-x-auto font-mono whitespace-pre-wrap">
          {approval.detail}
        </pre>
      )}

      {/* Context */}
      {approval.context && (
        <p className="text-xs text-neutral-500">{approval.context}</p>
      )}

      {/* Resolved badge or action buttons */}
      {isResolved ? (
        <div className="flex items-center gap-2">
          <span className={clsx(
            'text-xs px-2 py-0.5 rounded-full font-medium',
            approval.decision === 'approved'
              ? 'bg-green-500/20 text-green-300'
              : 'bg-red-500/20 text-red-300'
          )}>
            {approval.decision}
          </span>
          {approval.resolved_at && (
            <span className="text-xs text-neutral-600">{relativeTime(approval.resolved_at)}</span>
          )}
        </div>
      ) : (
        <div className="flex items-center gap-3 pt-1">
          <button
            onClick={() => onApprove?.(approval.id)}
            className="px-4 py-2 min-h-[44px] rounded-lg bg-green-600 hover:bg-green-500 text-white text-sm font-medium transition-colors"
          >
            Approve
          </button>
          <button
            onClick={() => onDeny?.(approval.id)}
            className="px-4 py-2 min-h-[44px] rounded-lg bg-red-600 hover:bg-red-500 text-white text-sm font-medium transition-colors"
          >
            Deny
          </button>
        </div>
      )}
    </div>
  );
}
