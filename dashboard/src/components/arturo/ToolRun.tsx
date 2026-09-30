/**
 * ToolRun — the tools a streamed Arturo turn ran, as a card between its paragraphs
 * (DEC-1790747153605131).
 *
 * Adapted from beautifului.dev's ToolChips (MIT, © Shane Levine — see NOTICE):
 * the collapsible run header, one compact row per call with its argument chip, and a row that
 * expands to show what the tool returned. What changed: rows come from live tool.call /
 * tool.result events instead of a scripted 700ms step timer; each row carries its state
 * (running · ok · failed); the file-diff chips are gone (no tool here produces diffs yet).
 * Styling uses the ported --bui-* tokens (index.css), which are dark inside the Arturo shell.
 */
import { useState, type ReactNode } from 'react';
import {
  Bookmark, Bot, Check, ChevronDown, FileText, Globe, ListTree, LoaderCircle, Map as MapIcon,
  MessageSquare, Search, Send, Terminal, Wrench, X,
} from 'lucide-react';
import type { ToolPart } from '../../lib/turnParts';
import { runHeader, toolChip, toolLabel, toolResultLines } from '../../lib/toolDisplay';

const ICONS: Record<string, ReactNode> = {
  list_agents: <ListTree size={13} />,
  query_roadmap: <MapIcon size={13} />,
  spawn_agent: <Bot size={13} />,
  kill_agent: <Bot size={13} />,
  send_telegram: <Send size={13} />,
  inject_message: <MessageSquare size={13} />,
  agent_message: <MessageSquare size={13} />,
  read_file: <FileText size={13} />,
  read_agent_conversation: <FileText size={13} />,
  get_agent_output: <FileText size={13} />,
  run_command: <Terminal size={13} />,
  research: <Globe size={13} />,
  knowledge: <Search size={13} />,
  remember_note: <Bookmark size={13} />,
};

const EASE = 'cubic-bezier(0.23, 1, 0.32, 1)';

function StatusMark({ status }: { status: ToolPart['status'] }) {
  if (status === 'running') {
    return <LoaderCircle size={13} className="shrink-0 animate-spin text-ink-3" aria-label="running" />;
  }
  if (status === 'failed') return <X size={13} className="shrink-0 text-red" aria-label="failed" />;
  return <Check size={13} className="shrink-0 text-green" aria-label="done" />;
}

export default function ToolRun({ tools, className }: { tools: ToolPart[]; className?: string }) {
  const [open, setOpen] = useState(true);
  const [openRows, setOpenRows] = useState<Set<string>>(new Set());
  const toggleRow = (id: string) => setOpenRows((cur) => {
    const next = new Set(cur);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  return (
    <div className={`w-full max-w-[34rem] pb-1${className ? ` ${className}` : ''}`} data-testid="tool-run">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="-mx-1.5 flex w-fit items-center gap-1.5 rounded-control px-1.5 py-1 text-[12.5px] text-ink-2 transition-colors duration-100 hover:bg-hover-2"
      >
        <ChevronDown size={12} strokeWidth={2.2} className="transition-transform duration-200"
          style={{ transform: open ? 'rotate(0deg)' : 'rotate(-90deg)' }} />
        <span className="tabular-nums">{runHeader(tools)}</span>
      </button>

      <div className="grid transition-[grid-template-rows,opacity] duration-300"
        style={{ gridTemplateRows: open ? '1fr' : '0fr', opacity: open ? 1 : 0 }}>
        {/* -mx-1 + px-1.5 keeps content at the same x while giving the row hover pills room
            inside this overflow-hidden clip box (as in ToolChips). */}
        <div className="-mx-1 overflow-hidden px-1.5 pb-1">
          <div className="mt-1.5 flex flex-col gap-1">
            {tools.map((t) => {
              const rowOpen = openRows.has(t.callId);
              const chip = toolChip(t.argsSummary);
              const detail = toolResultLines(t.summary || '');
              const canOpen = t.status !== 'running' && detail.length > 0;
              return (
                <div key={t.callId} style={{ animation: `fade-up 300ms ${EASE} both` }}>
                  <button
                    type="button"
                    aria-expanded={canOpen ? rowOpen : undefined}
                    disabled={!canOpen}
                    onClick={() => canOpen && toggleRow(t.callId)}
                    className="group/row -mx-[3px] flex h-7 w-[calc(100%+6px)] min-w-0 items-center gap-2 rounded-control px-[3px] text-left transition-colors duration-100 enabled:hover:bg-hover-2 disabled:cursor-default"
                  >
                    <span className="relative flex size-4 shrink-0 items-center justify-center text-ink-3">
                      <span className={`flex transition-opacity duration-100 ${canOpen ? 'group-hover/row:opacity-0' : ''} ${rowOpen ? 'opacity-0' : ''}`}>
                        {ICONS[t.name] ?? <Wrench size={13} />}
                      </span>
                      {canOpen && (
                        <ChevronDown size={12} strokeWidth={2.2}
                          className={`absolute transition-[opacity,transform] duration-150 group-hover/row:opacity-100 ${rowOpen ? 'opacity-100' : 'opacity-0'}`}
                          style={{ transform: rowOpen ? 'rotate(0deg)' : 'rotate(-90deg)' }} />
                      )}
                    </span>
                    <span className="shrink-0 text-[12.5px] font-medium text-ink">{toolLabel(t.name)}</span>
                    {chip ? (
                      <span className="inline-flex h-5.5 min-w-0 flex-1 items-center truncate rounded-chip bg-field px-1.5 font-mono text-[11.5px] text-ink-2 shadow-hairline">
                        {chip}
                      </span>
                    ) : <span className="flex-1" />}
                    <StatusMark status={t.status} />
                  </button>

                  <div className="grid transition-[grid-template-rows,opacity] duration-300"
                    style={{ gridTemplateRows: rowOpen ? '1fr' : '0fr', opacity: rowOpen ? 1 : 0, transitionTimingFunction: EASE }}>
                    <div className="min-h-0 overflow-hidden">
                      <div className="mt-0.5 mb-1 ml-2 flex flex-col gap-0.5 border-l border-line py-0.5 pl-3.5">
                        {detail.map((line, i) => (
                          <span key={i} className={`truncate font-mono text-[11.5px] leading-[1.6] ${t.status === 'failed' ? 'text-red' : 'text-ink-2'}`}>
                            {line}
                          </span>
                        ))}
                      </div>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
