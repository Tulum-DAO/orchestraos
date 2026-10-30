/**
 * ResultCard — finished work in a thread (thread kit; rules in lib/threadKit/results.ts).
 *
 * One card per result: what it was, who did it, when and how long, the outcome, a short summary that opens to
 * the whole report, links and files it named, and (for Arturo's jobs) its steps. Consecutive results come as a
 * stack: one header ("3 results · 2 done · 1 failed"), newest first, each row opening to its card. A failure is
 * never folded away: its badge and error line show on the row itself. The card offers no actions.
 *
 * Skins: `modern` (Arturo's page, and any agent shown in Modern) sits flat in the thread at full width; `bubbles`
 * (agent pages) is a bordered card like the thread's other cards.
 */
import { useState } from 'react';
import { Bot, Check, ChevronDown, CircleSlash, Inbox, Link2, Sparkles, Terminal, X } from 'lucide-react';
import Markdown from './Markdown';
import ToolCalls from './ToolCard';
import ResultFiles from './ResultFiles';
import { fromPart } from '../../lib/toolCalls';
import { formatDuration } from '../../lib/toolGroups';
import { newestFirst, stackHeadline, type FinishedResult, type ResultOutcome, type ResultSource, type ResultView } from '../../lib/threadKit/results';

export type ResultSkin = 'modern' | 'bubbles';

const SOURCE_ICON: Record<ResultSource, typeof Bot> = { task: Terminal, subagent: Bot, job: Sparkles, handback: Inbox };
const SOURCE_WORD: Record<ResultSource, string> = { task: 'Background task', subagent: 'Subagent', job: 'Job', handback: 'Report' };
const OUTCOME_WORD: Record<ResultOutcome, string> = { done: 'Done', failed: 'Failed', cancelled: 'Cancelled', reported: 'Reported' };
/** A background command passed or failed; other work is done. */
const outcomeWord = (o: ResultOutcome, source?: ResultSource) => (source === 'task' && o === 'done' ? 'Passed' : OUTCOME_WORD[o]);

function when(at?: string, now = new Date()): string {
  if (!at) return '';
  const d = new Date(at);
  if (Number.isNaN(d.getTime())) return '';
  const time = d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  return d.toDateString() === now.toDateString() ? time : `${d.toLocaleDateString([], { month: 'short', day: 'numeric' })}, ${time}`;
}

export function Outcome({ outcome, source }: { outcome: ResultOutcome; source?: ResultSource }) {
  const Icon = outcome === 'failed' ? X : outcome === 'cancelled' ? CircleSlash : Check;
  const tone = outcome === 'failed' ? 'text-destructive border-destructive/40 bg-destructive/10'
    : outcome === 'cancelled' ? 'text-muted-foreground border-current/20'
      : 'text-success border-success/40 bg-success/10';
  return (
    <span data-outcome={outcome} className={`inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium ${tone}`}>
      <Icon size={11} aria-hidden /> {outcomeWord(outcome, source)}
    </span>
  );
}

function MetaLine({ r }: { r: FinishedResult }) {
  const bits = [SOURCE_WORD[r.source], r.by, when(r.at), r.durationMs !== undefined ? formatDuration(r.durationMs) : '']
    .filter(Boolean);
  return <div className="text-[12px] text-muted-foreground break-words">{bits.join(' · ')}</div>;
}

function Body({ r, skin }: { r: FinishedResult; skin: ResultSkin }) {
  const [more, setMore] = useState(false);
  const [steps, setSteps] = useState(false);
  const md = skin === 'modern' ? 'modern' : 'bubbles';
  return (
    <div className="space-y-1.5 min-w-0">
      {r.error && r.outcome === 'failed' && (
        <div data-result-error className="text-[12.5px] text-destructive break-words">{r.error}</div>
      )}
      {r.summary && (r.error !== r.summary) && (
        <div data-result-summary className="min-w-0 break-words">
          <Markdown text={more && r.detail ? r.detail : r.summary} skin={md} />
        </div>
      )}
      {r.detail && (
        <button type="button" onClick={() => setMore((m) => !m)} aria-expanded={more}
          className="text-[12px] text-primary hover:underline">{more ? 'less' : 'more'}</button>
      )}
      {(r.links.length > 0 || r.files.length > 0) && (
        <div data-result-links className="flex flex-wrap gap-1.5">
          {r.links.map((u) => (
            <a key={u} href={u} target="_blank" rel="noreferrer"
              className="inline-flex max-w-full items-center gap-1 rounded-md border border-current/15 px-1.5 py-0.5 text-[12px] text-primary hover:underline">
              <Link2 size={11} aria-hidden /><span className="break-all">{u.replace(/^https?:\/\//, '')}</span>
            </a>
          ))}
          {/* each file opens from the library when it knows it (4g.5); else the plain path */}
          <ResultFiles files={r.files} agent={r.by} />
        </div>
      )}
      {r.steps && r.steps.length > 0 && (
        <div>
          <button type="button" onClick={() => setSteps((s) => !s)} aria-expanded={steps}
            className="inline-flex items-center gap-1 text-[12px] text-muted-foreground hover:text-foreground">
            <ChevronDown size={12} className={steps ? 'rotate-180' : ''} aria-hidden />
            {r.steps.length} step{r.steps.length === 1 ? '' : 's'}
          </button>
          {steps && <ToolCalls calls={r.steps.map(fromPart)} className="mt-1" skin={md} />}
        </div>
      )}
    </div>
  );
}

function Header({ r }: { r: FinishedResult }) {
  const Icon = SOURCE_ICON[r.source];
  return (
    <div className="flex items-start gap-2 min-w-0">
      <Icon size={15} className="mt-0.5 shrink-0 text-muted-foreground" aria-hidden />
      <div className="min-w-0 flex-1">
        <div className="font-semibold leading-snug break-words">{r.title}</div>
        <MetaLine r={r} />
      </div>
      <Outcome outcome={r.outcome} source={r.source} />
    </div>
  );
}

function shell(skin: ResultSkin): string {
  return skin === 'modern'
    ? 'w-full border-l-2 border-current/20 pl-3 py-1 text-[14px]'
    : 'w-full max-w-[min(42rem,100%)] rounded-xl border border-border bg-card text-card-foreground px-3.5 py-2.5 text-[13px] shadow-sm';
}

function Single({ r, skin }: { r: FinishedResult; skin: ResultSkin }) {
  return (
    <div data-result-card data-source={r.source} data-skin={skin} className={`${shell(skin)} space-y-2 min-w-0`}>
      <Header r={r} />
      <Body r={r} skin={skin} />
    </div>
  );
}

function StackRow({ r, skin }: { r: FinishedResult; skin: ResultSkin }) {
  const [open, setOpen] = useState(false);
  const Icon = SOURCE_ICON[r.source];
  return (
    <li data-result-row data-source={r.source} className="py-1.5 min-w-0">
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open}
        className="flex w-full items-start gap-2 text-left min-w-0">
        <Icon size={14} className="mt-0.5 shrink-0 text-muted-foreground" aria-hidden />
        <span className="min-w-0 flex-1">
          <span className="block font-medium leading-snug break-words">{r.title}</span>
          <MetaLine r={r} />
        </span>
        <Outcome outcome={r.outcome} source={r.source} />
        <ChevronDown size={14} className={`mt-0.5 shrink-0 text-muted-foreground ${open ? 'rotate-180' : ''}`} aria-hidden />
      </button>
      {/* A failure's reason shows on the row itself; the rest opens with the row. */}
      {!open && r.outcome === 'failed' && r.error && (
        <div className="mt-1 pl-6 text-[12.5px] text-destructive break-words">{r.error}</div>
      )}
      {open && <div className="mt-1.5 pl-6"><Body r={r} skin={skin} /></div>}
    </li>
  );
}

/** The collapsed line of a receipt (under the agent's reply) or a routine job folded into the strip: title, badge,
 *  time; it opens to the full card. A failure is never drawn collapsed. */
function Collapsed({ results, skin, view }: { results: FinishedResult[]; skin: ResultSkin; view: ResultView }) {
  const [open, setOpen] = useState(false);
  if (open) {
    return (
      <div className="space-y-1">
        <button type="button" onClick={() => setOpen(false)} aria-expanded
          className="inline-flex items-center gap-1 text-[12px] text-muted-foreground hover:text-foreground">
          <ChevronDown size={12} className="rotate-180" aria-hidden /> less
        </button>
        <ResultCard results={results} skin={skin} />
      </div>
    );
  }
  const r = results[0];
  const Icon = results.length === 1 ? SOURCE_ICON[r.source] : Check;
  const label = results.length === 1 ? r.title : stackHeadline(results);
  return (
    <div className="flex justify-start">
      <button type="button" data-result-collapsed data-view={view} onClick={() => setOpen(true)} aria-expanded={false}
        className="flex max-w-full min-w-0 items-center gap-2 rounded-lg border border-current/15 px-2.5 py-1 text-left text-[12.5px] text-muted-foreground hover:text-foreground">
        <Icon size={13} className="shrink-0" aria-hidden />
        <span className="min-w-0 break-words text-foreground">{label}</span>
        {results.length === 1 && <Outcome outcome={r.outcome} source={r.source} />}
        {results.length === 1 && when(r.at) && <span className="shrink-0">{when(r.at)}</span>}
        <ChevronDown size={12} className="shrink-0" aria-hidden />
      </button>
    </div>
  );
}

export default function ResultCard({ results, skin, view = 'open' }: { results: FinishedResult[]; skin: ResultSkin; view?: ResultView }) {
  if (results.length === 0) return null;
  if (view !== 'open' && !results.some((r) => r.outcome === 'failed')) return <Collapsed results={results} skin={skin} view={view} />;
  if (results.length === 1) return <div className="flex justify-start"><Single r={results[0]} skin={skin} /></div>;
  return (
    <div className="flex justify-start">
      <div data-result-stack data-skin={skin} className={`${shell(skin)} min-w-0`}>
        <div className="font-semibold leading-snug">{stackHeadline(results)}</div>
        <ul className="mt-1 divide-y divide-current/10">
          {newestFirst(results).map((r) => <StackRow key={r.key} r={r} skin={skin} />)}
        </ul>
      </div>
    </div>
  );
}
