import { clsx } from 'clsx';
import { Check, X } from 'lucide-react';

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

const STATUS_STYLES: Record<string, string> = {
  proposed: 'bg-neutral-700 text-neutral-300',
  running: 'bg-amber-500/20 text-amber-300 animate-pulse',
  completed: 'bg-green-500/20 text-green-300',
};

interface ExperimentCardProps {
  experiment: any;
}

export function ExperimentCard({ experiment }: ExperimentCardProps) {
  const statusClass = STATUS_STYLES[experiment.status] || 'bg-neutral-700 text-neutral-300';

  return (
    <div className="rounded-xl border border-neutral-800 bg-neutral-900 p-4 space-y-3">
      {/* Top row: status badge + agent + timestamp */}
      <div className="flex flex-wrap items-center gap-2">
        <span className={clsx('text-xs px-2 py-0.5 rounded-full font-medium', statusClass)}>
          {experiment.status}
        </span>
        {experiment.agent_id && (
          <span className="text-xs text-neutral-500">
            assigned to <span className="text-neutral-300">{experiment.agent_id}</span>
          </span>
        )}
        <span className="text-xs text-neutral-600 ml-auto">
          {experiment.created ? relativeTime(experiment.created) : ''}
        </span>
      </div>

      {/* Experiment ID */}
      <p className="text-xs font-mono text-neutral-500">{experiment.id}</p>

      {/* Hypothesis / description */}
      {experiment.hypothesis && (
        <p className="text-white text-sm font-medium">{experiment.hypothesis}</p>
      )}
      {experiment.description && (
        <p className="text-neutral-400 text-sm">{experiment.description}</p>
      )}

      {/* Completed: kept / discarded badge */}
      {experiment.status === 'completed' && (
        <div className="flex items-center gap-2">
          {experiment.kept ? (
            <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-green-500/20 text-green-300 font-medium">
              <Check className="w-3 h-3" />
              kept
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-neutral-700 text-neutral-400 font-medium">
              <X className="w-3 h-3" />
              discarded
            </span>
          )}
          {experiment.completed_at && (
            <span className="text-xs text-neutral-600">{relativeTime(experiment.completed_at)}</span>
          )}
        </div>
      )}

      {/* Learning text for kept experiments */}
      {experiment.kept && experiment.learning && (
        <div className="border-l-2 border-green-500 pl-3 py-1">
          <p className="text-xs text-green-300/80">{experiment.learning}</p>
        </div>
      )}
    </div>
  );
}
