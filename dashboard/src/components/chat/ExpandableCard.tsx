/**
 * ExpandableCard — toggle for ctrl+o expandable content.
 * Show details sends ctrl+o to expand. Hide details sends ctrl+o again to collapse.
 */
import { useState } from 'react';
import type { ExpandableEvent } from '../../lib/claude-code-protocol';

interface Props {
  event: ExpandableEvent;
  onToggle: () => void; // sends ctrl+o in both directions
}

export default function ExpandableCard({ event, onToggle }: Props) {
  const [expanded, setExpanded] = useState(false);

  const handleToggle = () => {
    setExpanded(!expanded);
    onToggle();
  };

  return (
    <div className="flex justify-start">
      <button
        onClick={handleToggle}
        className={
          expanded
            ? 'text-xs px-3 py-1.5 min-h-[44px] rounded-lg bg-neutral-700/50 text-neutral-400 hover:bg-neutral-700 transition-colors'
            : 'text-xs px-3 py-1.5 min-h-[44px] rounded-lg bg-blue-500/10 text-blue-400 hover:bg-blue-500/20 transition-colors'
        }
      >
        {expanded ? `Hide details: ${event.summary}` : `Show details: ${event.summary}`}
      </button>
    </div>
  );
}
