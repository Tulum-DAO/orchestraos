/**
 * StatusIndicator — shows working/idle state.
 * Only the currently active status gets animated dots.
 * All previous/past statuses render with static dots.
 */
import type { StatusEvent } from '../../lib/claude-code-protocol';

interface Props {
  event: StatusEvent;
  isActive: boolean; // true = this is the current/last status event, animate it
}

export default function StatusIndicator({ event, isActive }: Props) {
  if (event.status === 'idle') return null;

  const detail = event.detail || 'Working...';

  return (
    <div className="flex justify-start">
      <div className="flex items-center gap-2 px-3 py-1.5">
        <span className="flex gap-0.5">
          {isActive ? (
            <>
              <span className="w-1.5 h-1.5 bg-neutral-500 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
              <span className="w-1.5 h-1.5 bg-neutral-500 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
              <span className="w-1.5 h-1.5 bg-neutral-500 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
            </>
          ) : (
            <>
              <span className="w-1.5 h-1.5 bg-neutral-600 rounded-full" />
              <span className="w-1.5 h-1.5 bg-neutral-600 rounded-full" />
              <span className="w-1.5 h-1.5 bg-neutral-600 rounded-full" />
            </>
          )}
        </span>
        <span className="text-[11px] text-neutral-500 italic">
          {detail}
        </span>
      </div>
    </div>
  );
}
