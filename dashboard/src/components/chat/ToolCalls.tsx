/**
 * ToolCalls — the one tool card, on Arturo's page and in every agent chat (lib/toolCalls.ts has the model).
 *
 * One call is one row; two or more fold under a header that says how long the turn worked and what it did
 * ("Worked for 1m 8s — edited 2 files, ran 3 commands"), open by a click. Each row: the tool's icon, its name, its
 * arguments (a file as a chip that copies its path), and whether it is running, done or failed. A row with something
 * behind it opens to the todo list, the full input and what came back.
 *
 * Rows adapted from beautifului.dev's ToolChips (MIT, © Shane Levine — see NOTICE), as Arturo's card was. Colours are
 * the --bui-* tokens (index.css), set for light and dark, so the card reads the same on either page.
 */
import { useState, type ReactNode } from 'react';
import {
  Bookmark, Bot, Check, ChevronDown, FileText, Globe, Image as ImageIcon, ListChecks, ListTree, LoaderCircle,
  Map as MapIcon, MessageSquare, Pencil, Search, Terminal, Wrench, X,
} from 'lucide-react';
import { shortPath } from '../../lib/transcript';
import { callDetail, callLabel, hasDetail, iconKey, runSummary, type IconKey, type ToolCallView } from '../../lib/toolCalls';
import type { ToolDensity } from '../../lib/toolCardDetail';

const ICONS: Record<IconKey, ReactNode> = {
  terminal: <Terminal size={13} />, read: <FileText size={13} />, edit: <Pencil size={13} />, search: <Search size={13} />,
  web: <Globe size={13} />, agent: <Bot size={13} />, message: <MessageSquare size={13} />, list: <ListTree size={13} />,
  todo: <ListChecks size={13} />, note: <Bookmark size={13} />, map: <MapIcon size={13} />, image: <ImageIcon size={13} />,
  tool: <Wrench size={13} />,
};
const EASE = 'cubic-bezier(0.23, 1, 0.32, 1)';
// A result longer than this scrolls inside its own box.
const RESULT_TALL = 1600;

function StatusMark({ status }: { status: ToolCallView['status'] }) {
  if (status === 'running') return <LoaderCircle size={13} className="shrink-0 animate-spin text-ink-3" aria-label="running" />;
  if (status === 'failed') return <X size={13} className="shrink-0 text-red" aria-label="failed" />;
  return <Check size={13} className="shrink-0 text-green" aria-label="done" />;
}

/** A file the call touched: its short path; a click copies the whole path. */
function PathChip({ path }: { path: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <span role="button" tabIndex={0} title={`${path} — click to copy`} data-path-chip
      onClick={(e) => {
        e.stopPropagation();
        navigator.clipboard?.writeText(path).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1200); }, () => {});
      }}
      onKeyDown={(e) => { if (e.key === 'Enter') (e.currentTarget as HTMLElement).click(); }}
      className="inline-flex h-5.5 min-w-0 flex-1 items-center gap-1 truncate rounded-chip bg-field px-1.5 font-mono text-[11.5px] text-ink-2 shadow-hairline hover:text-ink">
      {copied ? <Check size={11} className="shrink-0 text-green" /> : <FileText size={11} className="shrink-0" />}
      <span className="truncate">{copied ? 'Copied' : shortPath(path)}</span>
    </span>
  );
}

function Row({ call }: { call: ToolCallView }) {
  const [open, setOpen] = useState(false);
  const canOpen = hasDetail(call);
  const d = canOpen ? callDetail(call) : null;
  return (
    <div style={{ animation: `fade-up 300ms ${EASE} both` }} data-tool-row={call.name}>
      <button type="button" aria-expanded={canOpen ? open : undefined} disabled={!canOpen}
        onClick={() => canOpen && setOpen((o) => !o)}
        className="group/row -mx-[3px] flex h-7 w-[calc(100%+6px)] min-w-0 items-center gap-2 rounded-control px-[3px] text-left transition-colors duration-100 enabled:hover:bg-hover-2 disabled:cursor-default">
        <span className="relative flex size-4 shrink-0 items-center justify-center text-ink-3">
          <span className={`flex transition-opacity duration-100 ${canOpen ? 'group-hover/row:opacity-0' : ''} ${open ? 'opacity-0' : ''}`}>
            {ICONS[iconKey(call.name)]}
          </span>
          {canOpen && (
            <ChevronDown size={12} strokeWidth={2.2}
              className={`absolute transition-[opacity,transform] duration-150 group-hover/row:opacity-100 ${open ? 'opacity-100' : 'opacity-0'}`}
              style={{ transform: open ? 'rotate(0deg)' : 'rotate(-90deg)' }} />
          )}
        </span>
        <span className="shrink-0 text-[12.5px] font-medium text-ink">{callLabel(call.name)}</span>
        {call.filePath ? <PathChip path={call.filePath} />
          : call.chip ? (
            <span className="inline-flex h-5.5 min-w-0 flex-1 items-center truncate rounded-chip bg-field px-1.5 font-mono text-[11.5px] text-ink-2 shadow-hairline">
              {call.chip}
            </span>
          ) : <span className="flex-1" />}
        <StatusMark status={call.status} />
      </button>
      {d && (
        <div className="grid transition-[grid-template-rows,opacity] duration-300"
          style={{ gridTemplateRows: open ? '1fr' : '0fr', opacity: open ? 1 : 0, transitionTimingFunction: EASE }}>
          <div className="min-h-0 overflow-hidden">
            <div className="mt-0.5 mb-1 ml-2 flex flex-col gap-1 border-l border-line py-0.5 pl-3.5">
              {d.todos.length > 0 && (
                <div className="flex flex-col gap-0.5" data-todos>
                  {d.todos.map((t, i) => (
                    <div key={i} className={`flex items-start gap-2 text-[12px] ${t.status === 'completed' ? 'text-ink-3 line-through' : 'text-ink'}`}>
                      <span role="img" aria-label={t.status} className={t.status === 'completed' ? 'text-green' : t.status === 'in_progress' ? 'text-amber-500' : 'text-ink-3'}>
                        {t.status === 'completed' ? '✓' : t.status === 'in_progress' ? '◐' : '○'}
                      </span>
                      {t.content}
                    </div>
                  ))}
                </div>
              )}
              {d.input && (
                <pre className="max-h-60 overflow-auto rounded-control bg-field px-2 py-1 font-mono text-[11px] text-ink-2 whitespace-pre">{d.input}</pre>
              )}
              {d.resultLines ? d.resultLines.map((line, i) => (
                <span key={i} className={`truncate font-mono text-[11.5px] leading-[1.6] ${call.status === 'failed' ? 'text-red' : 'text-ink-2'}`}>{line}</span>
              )) : d.resultText && (
                <pre className={`overflow-auto font-mono text-[11.5px] leading-[1.6] whitespace-pre ${call.status === 'failed' ? 'text-red' : 'text-ink-2'} ${d.resultText.length > RESULT_TALL ? 'max-h-72' : ''}`}>
                  {d.resultText}
                </pre>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/** What a folded run lists when opened: its calls, and anything said between them (an agent's thinking), in order. */
export type ToolRunEntry = { kind: 'call'; call: ToolCallView } | { kind: 'note'; key: string; node: ReactNode };

export default function ToolCalls({ calls, entries, interrupted = false, density = 'normal', className }: {
  calls: ToolCallView[];
  /** The calls with what came between them; without it, the calls alone. */
  entries?: ToolRunEntry[];
  /** The agent stopped before these finished: they read as stopped, not as still running. */
  interrupted?: boolean;
  density?: ToolDensity;
  className?: string;
}) {
  // Folded by default (the header is the turn's tool story); 'verbose' starts it open.
  const [open, setOpen] = useState(density === 'verbose');
  if (!calls.length) return null;
  const card = `w-full max-w-[min(92%,34rem)] rounded-lg border border-line px-2.5 py-1${className ? ` ${className}` : ''}`;
  if (calls.length === 1) {
    return <div className={card} data-testid="tool-calls"><Row call={calls[0]} /></div>;
  }
  const s = runSummary(calls, interrupted);
  return (
    <div className={`${card} ${s.tone === 'failed' ? 'border-red/40' : ''}`} data-testid="tool-calls">
      <button type="button" aria-expanded={open} onClick={() => setOpen((v) => !v)}
        className="-mx-1.5 flex w-[calc(100%+12px)] items-start gap-1.5 rounded-control px-1.5 py-1 text-left text-[12.5px] transition-colors duration-100 hover:bg-hover-2">
        <ChevronDown size={12} strokeWidth={2.2} className="mt-[3px] shrink-0 text-ink-3 transition-transform duration-200"
          style={{ transform: open ? 'rotate(0deg)' : 'rotate(-90deg)' }} />
        {/* Wraps rather than truncating: on a phone the clipped tail was the "(1 failed)". */}
        <span className="min-w-0 flex-1 break-words">
          <span className={`font-medium tabular-nums ${s.tone === 'running' ? 'text-orange-500' : s.tone === 'stopped' ? 'text-amber-500' : 'text-ink'}`}>{s.lead}</span>
          {s.headline && <span className="text-ink-2"> — {s.headline}</span>}
        </span>
      </button>
      <div className="grid transition-[grid-template-rows,opacity] duration-300"
        style={{ gridTemplateRows: open ? '1fr' : '0fr', opacity: open ? 1 : 0 }}>
        <div className="-mx-1 overflow-hidden px-1.5 pb-1">
          <div className="mt-1 flex flex-col gap-1">
            {(entries ?? calls.map((call): ToolRunEntry => ({ kind: 'call', call }))).map((e) =>
              e.kind === 'call' ? <Row key={e.call.id} call={e.call} /> : <div key={e.key}>{e.node}</div>)}
          </div>
        </div>
      </div>
    </div>
  );
}
