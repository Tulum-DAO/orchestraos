/**
 * ToolCard — the thread kit's one tool card, on Arturo's page and pane, in every agent chat, in call cards, job
 * results and the job tray (lib/toolCalls.ts has the model). It was components/chat/ToolCalls.tsx.
 *
 * One call is one row; two or more fold under a header that says how long the turn worked and what it did
 * ("Worked for 1m 8s — edited 2 files, ran 3 commands"), open by a click. Each row: the tool's icon, its name, its
 * arguments (a file as a chip that copies its path), and whether it is running, done or failed. A row with something
 * behind it opens to the todo list, the full input and what came back.
 *
 * A card id in a command or its output (`approval.py ack apr_…` and the JSON it prints) is never shown as the id
 * (lib/cardRefs.ts): in the row's one line, which is itself a button, it reads as the card's words and state; in
 * the opened detail it is the card's chip, which opens the card.
 *
 * Looks: 'card' (the default, above) or 'lines' (Bubbles' agent chat and Bubbles' thread parts:
 * "Collapse to a thin line that's still expandable (but displays other thin lines which are themselves expandable)
 * where applicable, and can expand to display a code div (which is always copyable)"): the run is ONE thin, quiet line
 * between the bubbles, the fold's own words (runSummary; while it works, the call it is on); opened, one thin line per
 * call (and what was said between them); a call's line opens to a code block with its input and what came back, its
 * Copy always shown (kit Markdown CodeFrame). One call skips the middle level. Same density, same live jobs.
 *
 * Skins (as the kit's Markdown): "bubbles" (agent chats), "modern" (Arturo's page), "pane" (his floating pane). The
 * card's own colours are the --bui-* tokens either way; the skin sets the chips' look.
 *
 * Rows adapted from beautifului.dev's ToolChips (MIT, © Shane Levine — see NOTICE), as Arturo's card was. Colours are
 * the --bui-* tokens (index.css), set for light and dark, so the card reads the same on either page.
 */
import { useContext, useEffect, useState, type ReactNode } from 'react';
import { LiveJobsContext } from './liveJobsContext';
import { failedLabel, jobElapsed, type AgentJob } from '../../lib/agentJobs';
import {
  Bookmark, Bot, Check, ChevronDown, ChevronRight, FileText, Globe, Image as ImageIcon, ListChecks, ListTree, LoaderCircle,
  Map as MapIcon, MessageSquare, Pencil, Search, Terminal, Wrench, X,
} from 'lucide-react';
import { shortPath } from '../../lib/transcript';
import { withRefs } from './RefText';
import { CodeFrame, type Skin } from './Markdown';
import {
  callCode, callDetail, callLabel, callLine, hasDetail, iconKey, liveCall, runSummary, runTimes, type IconKey, type ToolCallView,
} from '../../lib/toolCalls';
import { MESSAGE_TIME_TONE, messageTimeLabel } from '../../lib/messageTime';
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

/** A background job this call launched (an agent's thread, lib/agentJobs.ts): running with its clock, then done, or
 *  failed with how it ended. Replaces the call's own mark, which only says the launch returned. */
function JobLive({ job }: { job: AgentJob }) {
  const [now, setNow] = useState(() => Date.now());
  const running = job.status === 'running';
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [running]);
  if (running) {
    return (
      <span className="flex shrink-0 items-center gap-1.5" data-job-live="running">
        <span className="text-[11.5px] tabular-nums text-ink-3">{jobElapsed(job, now)}</span>
        <LoaderCircle size={13} className="animate-spin text-orange-500" aria-label="running in the background" />
      </span>
    );
  }
  if (job.status === 'failed') {
    return (
      <span className="flex min-w-0 shrink items-center gap-1.5" data-job-live="failed">
        <span className="truncate text-[11.5px] text-red">{failedLabel(job)}</span>
        <X size={13} className="shrink-0 text-red" aria-label="failed" />
      </span>
    );
  }
  return (
    <span className="flex shrink-0 items-center gap-1.5" data-job-live="done">
      <span className="text-[11.5px] tabular-nums text-ink-3">{jobElapsed(job, now)}</span>
      <Check size={13} className="text-green" aria-label="done" />
    </span>
  );
}

/** The job a call launched, when this is an agent's thread and the server named the call. */
function useJobOf(call: ToolCallView): AgentJob | undefined {
  const jobs = useContext(LiveJobsContext);
  return call.toolUseId ? jobs?.get(call.toolUseId) : undefined;
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

function Row({ call, skin }: { call: ToolCallView; skin: Skin }) {
  const [open, setOpen] = useState(false);
  const job = useJobOf(call);
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
              {withRefs(call.chip, skin, 'chip', true)}
            </span>
          ) : <span className="flex-1" />}
        {job ? <JobLive job={job} /> : <StatusMark status={call.status} />}
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
                <pre className="max-h-60 overflow-auto rounded-control bg-field px-2 py-1 font-mono text-[11px] text-ink-2 whitespace-pre">{withRefs(d.input, skin, 'in')}</pre>
              )}
              {d.resultLines ? d.resultLines.map((line, i) => (
                <span key={i} className={`truncate font-mono text-[11.5px] leading-[1.6] ${call.status === 'failed' ? 'text-red' : 'text-ink-2'}`}>{withRefs(line, skin, `l${i}`)}</span>
              )) : d.resultText && (
                <pre className={`overflow-auto font-mono text-[11.5px] leading-[1.6] whitespace-pre ${call.status === 'failed' ? 'text-red' : 'text-ink-2'} ${d.resultText.length > RESULT_TALL ? 'max-h-72' : ''}`}>
                  {withRefs(d.resultText, skin, 'out')}
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

export default function ToolCard({ calls, entries, interrupted = false, density = 'normal', className, skin = 'bubbles', look = 'card' }: {
  calls: ToolCallView[];
  skin?: Skin;
  /** 'card' (above) or Bubbles' thin 'lines' (ToolLines, below). */
  look?: 'card' | 'lines';
  /** The calls with what came between them; without it, the calls alone. */
  entries?: ToolRunEntry[];
  /** The agent stopped before these finished: they read as stopped, not as still running. */
  interrupted?: boolean;
  density?: ToolDensity;
  className?: string;
}) {
  // Folded by default (the header is the turn's tool story); 'verbose' starts it open.
  const [open, setOpen] = useState(density === 'verbose');
  // Background work this run launched, said on the folded header too (each row shows its own).
  const jobs = useContext(LiveJobsContext);
  const launched = jobs ? calls.flatMap((c) => (c.toolUseId && jobs.get(c.toolUseId) ? [jobs.get(c.toolUseId)!] : [])) : [];
  const bgRunning = launched.filter((j) => j.status === 'running').length;
  const bgFailed = launched.filter((j) => j.status === 'failed').length;
  if (look === 'lines') return <ToolLines calls={calls} entries={entries} interrupted={interrupted} density={density} skin={skin} className={className} />;
  if (!calls.length) return null;
  const card = `w-full max-w-[min(92%,34rem)] rounded-lg border border-line px-2.5 py-1${className ? ` ${className}` : ''}`;
  if (calls.length === 1) {
    return <div className={card} data-testid="tool-calls" data-skin={skin}><Row call={calls[0]} skin={skin} /></div>;
  }
  const s = runSummary(calls, interrupted);
  return (
    <div className={`${card} ${s.tone === 'failed' ? 'border-red/40' : ''}`} data-testid="tool-calls" data-skin={skin}>
      <button type="button" aria-expanded={open} onClick={() => setOpen((v) => !v)}
        className="-mx-1.5 flex w-[calc(100%+12px)] items-start gap-1.5 rounded-control px-1.5 py-1 text-left text-[12.5px] transition-colors duration-100 hover:bg-hover-2">
        <ChevronDown size={12} strokeWidth={2.2} className="mt-[3px] shrink-0 text-ink-3 transition-transform duration-200"
          style={{ transform: open ? 'rotate(0deg)' : 'rotate(-90deg)' }} />
        {/* Wraps rather than truncating: on a phone the clipped tail was the "(1 failed)". */}
        <span className="min-w-0 flex-1 break-words">
          <span className={`font-medium tabular-nums ${s.tone === 'running' ? 'text-orange-500' : s.tone === 'stopped' ? 'text-amber-500' : 'text-ink'}`}>{s.lead}</span>
          {s.headline && <span className="text-ink-2"> — {s.headline}</span>}
          {bgRunning > 0 && <span className="text-orange-500" data-job-live-head="running"> · {bgRunning} running in the background</span>}
          {bgFailed > 0 && <span className="text-red" data-job-live-head="failed"> · {bgFailed} failed in the background</span>}
        </span>
      </button>
      <div className="grid transition-[grid-template-rows,opacity] duration-300"
        style={{ gridTemplateRows: open ? '1fr' : '0fr', opacity: open ? 1 : 0 }}>
        <div className="-mx-1 overflow-hidden px-1.5 pb-1">
          <div className="mt-1 flex flex-col gap-1">
            {(entries ?? calls.map((call): ToolRunEntry => ({ kind: 'call', call }))).map((e) =>
              e.kind === 'call' ? <Row key={e.call.id} call={e.call} skin={skin} /> : <div key={e.key}>{e.node}</div>)}
          </div>
        </div>
      </div>
    </div>
  );
}

/* --- Bubbles: thin lines ------------------------------------------------------------------------------------------ */

/** What a line opens to: grown in over 200ms (rows 0fr to 1fr), drawn the first time it opens and kept after, so a
 *  closed run of fifty calls costs nothing and a close still eases out; closed, nothing in it takes the focus. */
function Reveal({ open, children }: { open: boolean; children: ReactNode }) {
  const [seen, setSeen] = useState(open);
  if (open && !seen) setSeen(true);
  return (
    <div className="grid transition-[grid-template-rows,opacity] duration-200 ease-out motion-reduce:transition-none"
      style={{ gridTemplateRows: open ? '1fr' : '0fr', opacity: open ? 1 : 0 }} inert={!open}>
      <div className="min-h-0 overflow-hidden">{seen && children}</div>
    </div>
  );
}

/** One thin, quiet line: muted small words, the chevron after them; a button that says whether it is open. `wrap`:
 *  the run's own line wraps rather than clipping its tail ("(1 failed)" on a phone); a call's line is one line. */
function LineButton({ open, canOpen, onToggle, lead, tail, time, wrap = false, children }: {
  open: boolean; canOpen: boolean; onToggle: () => void; lead?: ReactNode; tail?: ReactNode;
  /** When it happened (LineTime), just after the chevron: its own slot, never in the words the label truncates. */
  time?: ReactNode; wrap?: boolean; children: ReactNode;
}) {
  const chevron = canOpen && (
    <ChevronRight size={12} strokeWidth={2.2} aria-hidden
      className="ml-0.5 inline-block shrink-0 align-[-2px] opacity-70 transition-transform duration-200 motion-reduce:transition-none"
      style={{ transform: open ? 'rotate(90deg)' : 'none' }} />
  );
  return (
    <button type="button" aria-expanded={canOpen ? open : undefined} disabled={!canOpen} onClick={onToggle}
      className="relative flex w-full min-w-0 items-center gap-1.5 rounded-control py-0.5 text-left text-[11.5px] leading-5 text-muted-foreground transition-colors duration-200 motion-reduce:transition-none enabled:hover:text-foreground disabled:cursor-default">
      {lead}
      {/* a line that wraps carries its time inline, so it follows the chevron on whichever row that ends up */}
      {wrap ? <span className="min-w-0 break-words">{children}{chevron}{time && <span className="ml-2 inline-block">{time}</span>}</span>
        : <><span className="min-w-0 truncate">{children}</span>{chevron}</>}
      {/* right after the chevron (8px), never at the column's far edge: the time hugs its own line; the words before it
          truncate, never the time */}
      {(tail || (time && !wrap)) && <span className="ml-0.5 flex shrink-0 items-center gap-2">{tail}{!wrap && time}</span>}
    </button>
  );
}

/** When a line's call or run happened, as the time under a bubble says it (lib/messageTime: same words, same muted
 *  look): one time, or "10:12–10:17" for a run over a minute. A screen reader hears ", at 10:12" or
 *  ", from 10:12 to 10:17" after the line's words. No time: nothing. */
function LineTime({ start, end }: { start?: string; end?: string }) {
  const a = messageTimeLabel(start);
  if (!a) return null;
  const b = end ? messageTimeLabel(end) : null;
  return (
    <time dateTime={start} title={b ? `${a.title} – ${b.title}` : a.title} className={`${MESSAGE_TIME_TONE} tabular-nums whitespace-nowrap`}>
      <span className="sr-only">{end ? ', from ' : ', at '}</span>{a.label}
      {b && <><span aria-hidden>–</span><span className="sr-only"> to </span>{b.label}</>}
    </time>
  );
}

const Spinner = () => <LoaderCircle size={11} className="shrink-0 animate-spin motion-reduce:animate-none" aria-label="running" />;

/** A call's code block: its input as written, then what came back (red when it failed); Copy takes both. */
function CallCode({ call, skin }: { call: ToolCallView; skin: Skin }) {
  const code = callCode(call);
  const keep = { onTouchStart: (e: { stopPropagation: () => void }) => e.stopPropagation() };
  return (
    <CodeFrame className="mt-0.5 mb-1.5" label={callLabel(call.name)} copyText={code.copy} copyLabel="Copy call">
      {code.input && (
        <pre className="kit-code-scroll max-h-48 overflow-auto px-3 py-2 font-mono text-[11.5px] leading-relaxed text-ink whitespace-pre" {...keep}>
          {withRefs(code.input, skin, 'in')}
        </pre>
      )}
      {code.output && (
        <pre className={`kit-code-scroll max-h-72 overflow-auto border-t border-line-strong px-3 py-2 font-mono text-[11.5px] leading-relaxed whitespace-pre ${call.status === 'failed' ? 'text-red' : 'text-ink-2'}`} {...keep}>
          {withRefs(code.output, skin, 'out')}
        </pre>
      )}
    </CodeFrame>
  );
}

/** One call as a thin line ("Bash · D=/home/orchestra/…"), opening to its code block. Still running: its spinner (or the
 *  background job it launched, live), nothing to open yet. `top`: the run is this one call (no middle level). */
function CallLine({ call, skin, top = false, className }: { call: ToolCallView; skin: Skin; top?: boolean; className?: string }) {
  const [open, setOpen] = useState(false);
  const job = useJobOf(call);
  const code = callCode(call);
  const canOpen = call.status !== 'running' && !!(code.input || code.output);
  const { label, detail } = callLine(call);
  const line = (
    <>
      <LineButton open={open} canOpen={canOpen} onToggle={() => setOpen((o) => !o)}
        lead={!job && call.status === 'running' ? <Spinner /> : call.status === 'failed' ? <X size={11} className="shrink-0 text-red" aria-label="failed" /> : undefined}
        tail={job && <JobLive job={job} />} time={<LineTime start={call.ts} />}>
        <span className="text-foreground/75">{label}</span>
        {detail && <> · {withRefs(detail, skin, 'chip', true)}</>}
      </LineButton>
      {/* the code block is as wide as a wide bubble (85% of the column) on a wide screen; a phone gives code all of it */}
      {canOpen && <Reveal open={open}><div className="sm:max-w-[85%]"><CallCode call={call} skin={skin} /></div></Reveal>}
    </>
  );
  return top
    ? <div className={`w-full min-w-0${className ? ` ${className}` : ''}`} data-testid="tool-calls" data-look="lines" data-skin={skin} data-tool-line={call.name}>{line}</div>
    : <div className="min-w-0" data-tool-line={call.name}>{line}</div>;
}

/** Bubbles: a turn's tool run as one thin line ("Worked for 1m 8s — ran 3 commands ›"; while it works, the call it
 *  is on, live), opening to one thin line per call and what was said between them. */
function ToolLines({ calls, entries, interrupted, density, skin, className }: {
  calls: ToolCallView[]; entries?: ToolRunEntry[]; interrupted: boolean; density: ToolDensity; skin: Skin; className?: string;
}) {
  // Folded by default (the line is the turn's tool story); 'verbose' starts it open, as the card does.
  const [open, setOpen] = useState(density === 'verbose');
  const jobs = useContext(LiveJobsContext);
  if (!calls.length) return null;
  if (calls.length === 1) return <CallLine call={calls[0]} skin={skin} top className={className} />;
  const s = runSummary(calls, interrupted);
  const live = liveCall(calls, interrupted);
  const step = live ? callLine(live) : null;
  const launched = jobs ? calls.flatMap((c) => (c.toolUseId && jobs.get(c.toolUseId) ? [jobs.get(c.toolUseId)!] : [])) : [];
  const bgRunning = launched.filter((j) => j.status === 'running').length;
  const bgFailed = launched.filter((j) => j.status === 'failed').length;
  const tone = s.tone === 'failed' ? 'text-red' : s.tone === 'stopped' ? 'text-orange' : '';
  const when = runTimes(calls);
  return (
    <div className={`w-full min-w-0${className ? ` ${className}` : ''}`} data-testid="tool-calls" data-look="lines" data-skin={skin}>
      <LineButton open={open} canOpen onToggle={() => setOpen((v) => !v)} wrap lead={live ? <Spinner /> : undefined}
        time={when && <LineTime start={when.start} end={when.end} />}>
        <span className={`tabular-nums ${tone}`}>{s.lead}</span>
        {step ? <> · <span className="text-foreground/75">{step.label}</span>{step.detail && <> · {withRefs(step.detail, skin, 'step', true)}</>}</>
          : s.headline && <> — {s.headline}</>}
        {bgRunning > 0 && <span className="text-orange" data-job-live-head="running"> · {bgRunning} running in the background</span>}
        {bgFailed > 0 && <span className="text-red" data-job-live-head="failed"> · {bgFailed} failed in the background</span>}
      </LineButton>
      <Reveal open={open}>
        <div className="ml-[5px] mt-0.5 flex flex-col border-l border-border pl-3">
          {(entries ?? calls.map((call): ToolRunEntry => ({ kind: 'call', call }))).map((e) =>
            e.kind === 'call' ? <CallLine key={e.call.id} call={e.call} skin={skin} /> : <div key={e.key} className="py-0.5">{e.node}</div>)}
        </div>
      </Reveal>
    </div>
  );
}
