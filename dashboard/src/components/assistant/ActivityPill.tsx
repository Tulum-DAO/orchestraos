/**
 * ActivityPill — the honest activity indicator (B3, Decision B).
 *
 * Shows what the assistant is actually doing — Thinking / Running {tool} /
 * Answering — derived from the stream lifecycle (or a server `activity` event
 * when present). It is NOT synthesized reasoning: on subscription auth Jarvis
 * emits no native reasoning tokens, so this reports activity/status honestly.
 * A subtle tooltip labels the true-reasoning-token streaming upgrade path.
 */
import { clsx } from 'clsx';
import { Loader2, Wrench, Sparkles, AlertTriangle } from 'lucide-react';
import type { ActivityState } from '../../lib/assistant/activity';

interface Props {
  activity: ActivityState;
  className?: string;
}

const UPGRADE_HINT =
  'Honest activity status. Jarvis streams no native reasoning tokens on ' +
  'subscription auth yet — true reasoning-token streaming is the upgrade path.';

function label(a: ActivityState): string {
  if (a.label) return a.label; // server-provided
  switch (a.phase) {
    case 'retrieving': return 'Retrieving';
    case 'thinking': return 'Thinking';
    case 'running': return a.tool ? `Running ${a.tool}` : 'Running tool';
    case 'answering': return 'Answering';
    case 'error': return "Couldn't complete";
    default: return '';
  }
}

export default function ActivityPill({ activity, className }: Props) {
  if (activity.phase === 'idle') return null;

  const isError = activity.phase === 'error';
  const isRunning = activity.phase === 'running';
  const isAnswering = activity.phase === 'answering';

  const Icon = isError ? AlertTriangle : isRunning ? Wrench : isAnswering ? Sparkles : Loader2;

  return (
    <div
      title={UPGRADE_HINT}
      className={clsx(
        'inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-medium select-none',
        isError
          ? 'bg-red-500/10 text-red-300'
          : isAnswering
            ? 'bg-green-500/10 text-green-300'
            : 'bg-blue-500/10 text-blue-300',
        className,
      )}
    >
      <Icon size={12} className={clsx(!isError && !isAnswering && 'animate-spin')} />
      <span>{label(activity)}</span>
      {activity.fromServer && (
        <span className="w-1 h-1 rounded-full bg-current opacity-50" aria-hidden />
      )}
    </div>
  );
}
