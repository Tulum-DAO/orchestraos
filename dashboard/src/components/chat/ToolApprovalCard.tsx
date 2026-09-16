/**
 * ToolApprovalCard — renders a tool permission request with action buttons.
 */
import { useState } from 'react';
import { clsx } from 'clsx';
import type { ToolRequestEvent } from '../../lib/claude-code-protocol';

interface Props {
  event: ToolRequestEvent;
  onAction: (keystroke: string) => void;
}

export default function ToolApprovalCard({ event, onAction }: Props) {
  const [acted, setActed] = useState<string | null>(null);

  const handleAction = (keystroke: string, label: string) => {
    if (acted) return;
    setActed(label);
    onAction(keystroke);
  };

  return (
    <div className="flex justify-start">
      <div className="border border-neutral-700 rounded-xl px-3 py-2.5 max-w-[90%] space-y-2 bg-neutral-900">
        <div className="flex items-start gap-2">
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-400 font-mono shrink-0">
            {event.tool}
          </span>
          <span className="text-xs text-neutral-300 break-all">{event.description}</span>
        </div>

        {acted ? (
          <div className="text-[11px] text-neutral-500 italic">{acted}</div>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {event.actions.map(action => (
              <button
                key={action.keystroke}
                onClick={() => handleAction(action.keystroke, action.label)}
                className={clsx(
                  'text-xs px-3 py-1.5 min-h-[44px] rounded-lg font-medium transition-colors',
                  action.style === 'primary' && 'bg-green-500/15 text-green-400 hover:bg-green-500/25',
                  action.style === 'danger' && 'bg-red-500/15 text-red-400 hover:bg-red-500/25',
                  action.style === 'secondary' && 'bg-neutral-700 text-neutral-300 hover:bg-neutral-600',
                )}
              >
                {action.label}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
