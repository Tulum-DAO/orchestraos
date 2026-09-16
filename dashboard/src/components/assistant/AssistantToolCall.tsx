/**
 * AssistantToolCall — renders one tool call's lifecycle as the security
 * gate's decision (B2, Decision 2A). Driven directly off contract.ts /
 * timeline.ts types — NOT the tmux claude-code-protocol shape.
 *
 * Honest "tools running live": proposed → decided → executed | skipped.
 * A lethal-quadrant write (write_untrusted, D8) shows proposed-then-BLOCKED
 * with NO approve button — the block is terminal (overridable === false).
 * In read-only V1 writes surface as skipped(shadow); reads execute + show
 * results.
 */
import { useState } from 'react';
import { clsx } from 'clsx';
import { ChevronRight, ChevronDown, ShieldAlert, ShieldCheck, Ban, EyeOff } from 'lucide-react';
import type { ToolCallItem } from '../../lib/assistant/timeline';

interface Props {
  item: ToolCallItem;
}

function argsToCommand(tool: string, args: Record<string, unknown>): string {
  const inner = Object.entries(args)
    .map(([k, v]) => `${k}: ${typeof v === 'string' ? v : JSON.stringify(v)}`)
    .join(', ');
  return `${tool}(${inner})`;
}

/** One-word phase label + the color family for the card accent. */
function phaseMeta(item: ToolCallItem): {
  label: string;
  tone: 'blue' | 'green' | 'amber' | 'red' | 'neutral';
  icon: React.ReactNode;
} {
  if (item.phase === 'skipped') {
    if (item.skippedWhy === 'blocked_lethal') {
      return { label: 'blocked', tone: 'red', icon: <Ban size={12} /> };
    }
    if (item.skippedWhy === 'shadow') {
      return { label: 'proposed · not executed (shadow)', tone: 'neutral', icon: <EyeOff size={12} /> };
    }
    return { label: `skipped: ${item.skippedWhy}`, tone: 'amber', icon: <EyeOff size={12} /> };
  }
  if (item.phase === 'done') {
    return {
      label: item.ok === false ? 'error' : 'executed',
      tone: item.ok === false ? 'red' : 'green',
      icon: <ShieldCheck size={12} />,
    };
  }
  if (item.phase === 'decided') {
    if (item.decision === 'block') return { label: 'blocked', tone: 'red', icon: <Ban size={12} /> };
    if (item.decision === 'require_human') return { label: 'awaiting human', tone: 'amber', icon: <ShieldAlert size={12} /> };
    return { label: 'running…', tone: 'blue', icon: <ShieldCheck size={12} /> };
  }
  return { label: 'proposed…', tone: 'blue', icon: <ShieldAlert size={12} /> };
}

const TONE: Record<string, string> = {
  blue: 'bg-blue-500/15 text-blue-400',
  green: 'bg-green-500/15 text-green-400',
  amber: 'bg-amber-500/15 text-amber-400',
  red: 'bg-red-500/15 text-red-400',
  neutral: 'bg-neutral-700/50 text-neutral-400',
};

const QUADRANT_LABEL: Record<string, string> = {
  read_trusted: 'read · trusted',
  read_untrusted: 'read · untrusted',
  write_trusted: 'write · trusted',
  write_untrusted: 'write · untrusted (lethal)',
};

export default function AssistantToolCall({ item }: Props) {
  const [open, setOpen] = useState(false);
  const cmd = argsToCommand(item.tool, item.args);
  const meta = phaseMeta(item);
  const results = item.results ?? [];
  const hasDetails = results.length > 0 || !!item.rationale || !!item.reason;

  const isLethalBlock = item.skippedWhy === 'blocked_lethal' || (item.decision === 'block' && item.overridable === false);

  return (
    <div className="flex justify-start" data-testid="tool-call" data-phase={item.phase} data-skipped={item.skippedWhy ?? ''}>
      <div
        className={clsx(
          'border rounded-lg max-w-[90%] overflow-hidden',
          isLethalBlock ? 'border-red-800/60 bg-red-950/20' : 'border-neutral-800 bg-neutral-900/60',
        )}
      >
        <button
          onClick={() => hasDetails && setOpen(!open)}
          className="flex items-center gap-2 w-full px-2.5 py-1.5 text-left hover:bg-neutral-800/40 transition-colors"
        >
          {hasDetails &&
            (open ? (
              <ChevronDown size={12} className="text-neutral-500 shrink-0" />
            ) : (
              <ChevronRight size={12} className="text-neutral-500 shrink-0" />
            ))}
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-neutral-800 text-neutral-300 font-mono shrink-0">
            {item.tool}
          </span>
          <span className={clsx('text-[10px] px-1.5 py-0.5 rounded shrink-0 inline-flex items-center gap-1', TONE[meta.tone])}>
            {meta.icon}
            {meta.label}
          </span>
          <span className="text-[11px] text-neutral-500 truncate font-mono hidden sm:inline">{cmd}</span>
        </button>

        {/* Always-visible gate badges: quadrant + taint */}
        <div className="flex flex-wrap items-center gap-1 px-2.5 pb-1.5">
          <span className="text-[9px] px-1 py-0.5 rounded bg-neutral-800/80 text-neutral-500 font-mono">
            {QUADRANT_LABEL[item.quadrant] ?? item.quadrant}
          </span>
          <span
            className={clsx(
              'text-[9px] px-1 py-0.5 rounded font-mono',
              item.taint === 'untrusted' ? 'bg-amber-900/40 text-amber-500' : 'bg-neutral-800/80 text-neutral-500',
            )}
          >
            {item.taint}
          </span>
          {item.mode && (
            <span className="text-[9px] px-1 py-0.5 rounded bg-neutral-800/80 text-neutral-500 font-mono">{item.mode}</span>
          )}
        </div>

        {isLethalBlock && (
          <div className="px-2.5 pb-1.5 text-[10px] text-red-400/90">
            blocked: untrusted-context write (lethal quadrant) — not executable, no override (D8)
          </div>
        )}

        {open && hasDetails && (
          <div className="border-t border-neutral-800 px-2.5 py-1.5 space-y-0.5">
            <div className="text-[11px] text-neutral-500 font-mono break-all whitespace-pre-wrap">{cmd}</div>
            {item.rationale && <div className="text-[11px] text-neutral-500 italic pl-2">{item.rationale}</div>}
            {item.reason && <div className="text-[10px] text-neutral-600 font-mono pl-2">reason: {item.reason}</div>}
            {results.map((r, idx) => (
              <div
                key={idx}
                className={
                  /^Error|^Exit code|^FAIL/i.test(r)
                    ? 'text-[11px] font-mono text-red-400/80 pl-2 break-all'
                    : 'text-[11px] font-mono text-neutral-400 pl-2 break-all'
                }
              >
                <span className="text-neutral-600 mr-1">└</span>
                {r}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
