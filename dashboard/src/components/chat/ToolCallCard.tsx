/**
 * ToolCallCard — collapsible card for non-approval tool uses.
 * Shows tool name badge + truncated command, expandable details.
 */
import { useState } from 'react';
import { ChevronRight, ChevronDown } from 'lucide-react';
import type { ToolCallEvent } from '../../lib/claude-code-protocol';

interface Props {
  event: ToolCallEvent;
  onExpandToggle?: () => void;
}

function extractCommand(tool: string, raw: string): string {
  const match = raw.match(new RegExp(`${tool}\\((.+)\\)\\s*$`));
  if (match) return match[1];
  return raw.replace(/^[⏺●]?\s*/, '');
}

function truncate(s: string, max: number): string {
  return s.length > max ? s.slice(0, max) + '…' : s;
}

export default function ToolCallCard({ event, onExpandToggle }: Props) {
  const [open, setOpen] = useState(false);
  const cmd = extractCommand(event.tool, event.command);
  const hasDetails = event.results.length > 0 || event.expandable;

  return (
    <div className="flex justify-start">
      <div className="border border-neutral-800 rounded-lg max-w-[90%] bg-neutral-900/60 overflow-hidden">
        <button
          onClick={() => hasDetails && setOpen(!open)}
          className="flex items-center gap-2 w-full px-2.5 py-1.5 text-left hover:bg-neutral-800/40 transition-colors"
        >
          {hasDetails && (
            open
              ? <ChevronDown size={12} className="text-neutral-500 shrink-0" />
              : <ChevronRight size={12} className="text-neutral-500 shrink-0" />
          )}
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/15 text-blue-400 font-mono shrink-0">
            {event.tool}
          </span>
          <span className="text-[11px] text-neutral-400 truncate font-mono">
            {truncate(cmd, 80)}
          </span>
        </button>
        {open && (
          <div className="border-t border-neutral-800 px-2.5 py-1.5 space-y-0.5">
            <div className="text-[11px] text-neutral-500 font-mono break-all whitespace-pre-wrap">{cmd}</div>
            {event.results.map((r, idx) => (
              <div key={idx} className={/^Error|^Exit code|^FAIL/i.test(r)
                ? 'text-[11px] font-mono text-red-400/80 pl-2 break-all'
                : 'text-[11px] font-mono text-neutral-400 pl-2 break-all'
              }>
                <span className="text-neutral-600 mr-1">└</span>{r}
              </div>
            ))}
            {event.expandable && onExpandToggle && (
              <button onClick={onExpandToggle} className="text-[11px] text-blue-400/70 hover:text-blue-300 transition-colors pl-2">
                Show details: {event.expandable}
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
