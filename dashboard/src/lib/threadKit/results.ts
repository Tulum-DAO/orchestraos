/**
 * results.ts — finished work as cards in a thread (the thread kit's result card, components/kit/ResultCard.tsx).
 *
 * Four kinds of finished work already sit in durable records; this turns each into one shape the card draws:
 *   task      a background job of the agent: the harness's <task-notification> line in its transcript (at an idle
 *             prompt a user turn; mid-turn the server keeps it as a machine `task` line marked mid_turn)
 *   subagent  a Task/Agent tool call and the report it returned
 *   job       one of Arturo's background jobs: the thread turn with meta kind "job_result"
 *   handback  another agent's report: a msg_store message of type phase_report or handoff ("status" messages
 *             stay ordinary message lines)
 * Nothing new is stored: a reload rebuilds the same cards from the same records, so they keep their history.
 * Consecutive results in one stretch of the thread stack into one card.
 */
import type { RenderNode, ToolBlock } from '../transcript';
import type { ToolPart } from '../turnParts';
import { previewText } from '../decisions';

export type ResultSource = 'task' | 'subagent' | 'job' | 'handback';
export type ResultOutcome = 'done' | 'failed' | 'cancelled' | 'reported';

export interface FinishedResult {
  key: string;
  source: ResultSource;
  title: string;
  /** Who did the work: the agent, the subagent's type, Arturo, or the agent that reported. */
  by?: string;
  /** When it finished (ISO-8601). */
  at?: string;
  durationMs?: number;
  outcome: ResultOutcome;
  /** A few lines; the card shows more on request. */
  summary: string;
  /** The whole report, when it is longer than the summary. */
  detail?: string;
  /** Why it failed, shown without opening the card. */
  error?: string;
  links: string[];
  files: string[];
  /** The job's steps, as tool rows. */
  steps?: ToolPart[];
  /** The tool call that started it (a background job's <tool-use-id>, a subagent call's id): finds the turn it
   *  belongs to. */
  launchId?: string;
}

/** How a result sits in the thread (the agent's own reply is the message):
 *   open     a card, shown open: no reply covers it, a failure, or a turn the operator started with no reply yet
 *   receipt  under the agent's reply that covers it, one collapsed line that opens for detail
 *   folded   a routine job the agent started for itself that succeeded: inside the "Worked for…" strip
 *  A failure is always open. */
export type ResultView = 'open' | 'receipt' | 'folded';

/** A render node holding one result or a stack of them (consecutive results with the same view merged). */
export interface ResultNode { kind: 'result'; key: string; ts?: string; view?: ResultView; results: FinishedResult[] }

const SUMMARY_MAX = 280;

function tag(name: string, s: string): string | null {
  const m = s.match(new RegExp(`<${name}>([\\s\\S]*?)</${name}>`));
  return m ? m[1].trim() : null;
}

/** Links and file paths named in a report, each once, a few at most. */
export function linksAndFiles(text: string): { links: string[]; files: string[] } {
  const links = [...new Set(text.match(/https?:\/\/[^\s)<>"'`\]]+/g) ?? [])]
    .map((u) => u.replace(/[.,;:]+$/, '')).slice(0, 5);
  const files = [...new Set(
    [...text.matchAll(/(?:^|[\s(`"'])((?:~|\/)[\w.~-]*(?:\/[\w.@+-]+)+\.[A-Za-z0-9]{1,8})(?=$|[\s)`"',;:])/gm)].map((m) => m[1]),
  )].filter((f) => !f.startsWith('//')).slice(0, 5);
  return { links, files };
}

function summarize(text: string): { summary: string; detail?: string } {
  const t = text.trim();
  if (!t) return { summary: '' };
  const summary = previewText(t, SUMMARY_MAX);
  return summary === t ? { summary } : { summary, detail: t };
}

const isoOf = (ts: unknown): string | undefined => {
  if (typeof ts === 'number' && Number.isFinite(ts)) return new Date(ts * 1000).toISOString();
  if (typeof ts === 'string' && ts && !Number.isNaN(Date.parse(ts))) return ts;
  return undefined;
};

/** A job named without quotes: the harness's line without its "Background command" and its ending. */
function plainTitle(line: string): string {
  return line.replace(/^\s*Background command\s+/i, '')
    .replace(/\s+(?:completed|finished|failed|was stopped|was killed)\b.*$/i, '').trim() || line;
}

/** Why a job failed, in plain words: "Exit code 3", else the harness's line. */
export function failureReason(line: string): string {
  const code = line.match(/exit code (\d+)/i);
  return code ? `Exit code ${code[1]}` : line;
}

/** A background job's notification line: what finished and how. Null when it is not one or says nothing. */
export function taskResult(text: string, opts: { key: string; ts?: string; by?: string }): FinishedResult | null {
  const t = text.trim();
  if (!t.startsWith('<task-notification>')) return null;
  const summaryLine = tag('summary', t);
  if (!summaryLine) return null;
  const status = (tag('status', t) || '').toLowerCase();
  const outcome: ResultOutcome = status === 'failed' ? 'failed'
    : status === 'killed' || status === 'stopped' || status === 'cancelled' ? 'cancelled' : 'done';
  // The harness names the job in quotes: Background command "npm test" completed (exit code 0).
  const named = summaryLine.match(/"([^"]{1,200})"/);
  // A background subagent reports the same way: Agent "Count lines" completed, its report in <result>.
  const isAgent = /^\s*(?:Agent|Subagent)\b/i.test(summaryLine);
  const result = isAgent ? cleanSubagentReport(tag('result', t) || '') : (tag('result', t) || '');
  // Plain words: the title is the job's own description and the badge says how it went, so the harness's line
  // ("Background command … completed (exit code 0)") is not repeated. An exit code shows only on a failure, as
  // its reason.
  const { summary, detail } = summarize(result);
  const ms = Number(tag('duration_ms', t));      // a subagent's notice: <usage>…<duration_ms>3874</duration_ms>
  const launchId = tag('tool-use-id', t);
  return {
    key: opts.key, source: isAgent ? 'subagent' : 'task', title: named ? named[1] : plainTitle(summaryLine),
    by: opts.by, at: isoOf(opts.ts), ...(Number.isFinite(ms) && ms > 0 ? { durationMs: ms } : {}), outcome,
    summary, ...(detail ? { detail } : {}),
    ...(outcome === 'failed' ? { error: failureReason(summaryLine) } : {}),
    ...(launchId ? { launchId } : {}),
    ...linksAndFiles(result),
  };
}

const SUBAGENT_TOOLS = new Set(['Task', 'Agent']);

/** A subagent's report, without the harness's own trailer (its id and token usage). */
export function cleanSubagentReport(text: string): string {
  return text
    .replace(/<usage>[\s\S]*?<\/usage>/g, '')
    .replace(/^\s*agentId:.*$/gm, '')
    .trim();
}

/** A finished Task/Agent call as a result. Null while it runs, or for any other tool. */
export function subagentResult(b: ToolBlock): FinishedResult | null {
  if (!SUBAGENT_TOOLS.has(b.tool) || b.result === undefined) return null;
  // Claude Code may run the subagent in the background: the call returns at once with this note, and the
  // report comes later as a <task-notification> (taskResult). The call itself stays a tool row.
  if (/^\s*Async agent launched/i.test(String(b.result))) return null;
  const input = b.input || {};
  const report = cleanSubagentReport(String(b.result || ''));
  const failed = !!b.isError;
  const { summary, detail } = summarize(report || (failed ? 'The subagent failed.' : 'Finished with no report.'));
  const start = b.ts ? Date.parse(b.ts) : NaN;
  const end = b.resultTs ? Date.parse(b.resultTs) : NaN;
  const desc = typeof input.description === 'string' && input.description.trim() ? input.description.trim() : '';
  const kind = typeof input.subagent_type === 'string' && input.subagent_type.trim() ? input.subagent_type.trim() : '';
  return {
    key: b.key, source: 'subagent', title: desc || kind || 'Subagent', ...(kind ? { by: kind } : {}),
    at: isoOf(b.resultTs ?? b.ts), ...(Number.isFinite(start) && Number.isFinite(end) && end >= start ? { durationMs: end - start } : {}),
    outcome: failed ? 'failed' : 'done', summary, ...(detail ? { detail } : {}),
    ...(failed ? { error: (report.split('\n').find((l) => l.trim()) || 'The subagent failed.').slice(0, 240) } : {}),
    ...(b.id ? { launchId: b.id } : {}),
    ...linksAndFiles(report),
  };
}

export interface JobMeta { kind?: string; job_id?: string; job_kind?: string; title?: string; status?: string }

/** One of Arturo's finished jobs (its thread turn) as a result. Null for any other turn. */
export function jobResult(meta: JobMeta | undefined, text: string, opts: { key: string; ts?: number; by?: string; steps?: ToolPart[] }): FinishedResult | null {
  if (meta?.kind !== 'job_result') return null;
  const title = (meta.title || '').trim() || 'Background job';
  const status = (meta.status || 'done').toLowerCase();
  const outcome: ResultOutcome = status === 'done' ? 'done' : status === 'cancelled' ? 'cancelled' : 'failed';
  // The posted text opens with "<title>: done." (or "didn't finish"); the card's header already says that.
  const lines = text.trim().split('\n');
  const body = lines[0]?.startsWith(`${title}:`) ? lines.slice(1).join('\n').trim() : text.trim();
  const { summary, detail } = summarize(body || lines[0] || '');
  return {
    key: opts.key, source: 'job', title, by: opts.by, at: isoOf(opts.ts), outcome, summary, ...(detail ? { detail } : {}),
    ...(outcome === 'failed' ? { error: (lines[0] || 'The job did not finish.').slice(0, 240) } : {}),
    ...(opts.steps?.length ? { steps: opts.steps } : {}),
    ...linksAndFiles(body),
  };
}

/** msg_store types that are an agent handing work back. "status" stays an ordinary message line. */
export const HANDBACK_TYPES = new Set(['phase_report', 'handoff']);
export const isHandback = (type: string | null | undefined) => !!type && HANDBACK_TYPES.has(type);

export interface HandbackMessage { id: string; from_agent: string; subject: string | null; body: string | null; created_at: string | null; type?: string | null }

/** Another agent's report as a result. Null unless its type is a hand-back. */
export function handbackResult(m: HandbackMessage, by?: string): FinishedResult | null {
  if (!isHandback(m.type)) return null;
  const body = (m.body || '').trim();
  const { summary, detail } = summarize(body || m.subject || '');
  return {
    key: m.id, source: 'handback', title: (m.subject || '').trim() || 'Report', by: by || m.from_agent,
    at: isoOf(m.created_at), outcome: 'reported', summary, ...(detail ? { detail } : {}), ...linksAndFiles(body),
  };
}

/** The stack's one-line header: "3 results · 2 done · 1 failed". */
export function stackHeadline(results: FinishedResult[]): string {
  const n = results.length;
  const count = (o: ResultOutcome) => results.filter((r) => r.outcome === o).length;
  const parts = [`${n} result${n === 1 ? '' : 's'}`];
  for (const [o, word] of [['done', 'done'], ['reported', 'reported'], ['failed', 'failed'], ['cancelled', 'cancelled']] as const) {
    const c = count(o);
    if (c) parts.push(`${c} ${word}`);
  }
  return parts.join(' · ');
}

/** Newest first, for a stack. */
export function newestFirst(results: FinishedResult[]): FinishedResult[] {
  return results.map((r, i) => ({ r, i, t: r.at ? Date.parse(r.at) : NaN }))
    .sort((a, b) => (Number.isFinite(b.t) && Number.isFinite(a.t) ? b.t - a.t : b.i - a.i))
    .map((x) => x.r);
}

/** The result a render node stands for, if it is finished work. */
export function nodeResult(n: RenderNode, by?: string): FinishedResult | null {
  if (n.kind === 'user' && (n.machine === 'task' || n.text.trimStart().startsWith('<task-notification>'))) {
    return taskResult(n.text, { key: n.key, ts: n.ts, by });
  }
  if (n.kind === 'tool') return subagentResult(n);
  return null;
}

/** A user line the operator wrote (not a machine line, not the spawn brief). */
const isPerson = (n: RenderNode) => n.kind === 'user' && !n.isSystem && !n.machine;
/** A user line that starts a turn: the operator's, or a machine line that is not a mid-turn job notice. */
const opensTurn = (n: RenderNode) => n.kind === 'user' && !(n.machine === 'task' && n.midTurn);

/** Turn finished work in an agent's thread into result nodes, each placed by its view (ResultView), consecutive
 *  results with the same view stacked. Everything else is kept as it is, in order. `by` names the agent whose
 *  thread this is (its background jobs are its own work). */
export function markResults(nodes: RenderNode[], by?: string): RenderNode[] {
  // Which turns the operator started: by position, and by the tool call that launched a job.
  const operatorTurnAt: boolean[] = [];
  const launchedInOperatorTurn = new Map<string, boolean>();
  let operatorTurn = false;
  nodes.forEach((n, i) => {
    if (opensTurn(n)) operatorTurn = isPerson(n);
    operatorTurnAt[i] = operatorTurn;
    if (n.kind === 'tool' && n.id) launchedInOperatorTurn.set(n.id, operatorTurn);
  });
  const views: { r: FinishedResult; view: ResultView; under?: number }[] = [];
  const resultAt = new Map<number, number>();
  nodes.forEach((n, i) => {
    const r = nodeResult(n, by);
    if (!r) return;
    // A notice's job belongs to the turn that started it; a synchronous subagent call, to the turn it sits in.
    const started = r.launchId && launchedInOperatorTurn.has(r.launchId) ? launchedInOperatorTurn.get(r.launchId)
      : n.kind === 'tool' ? operatorTurnAt[i] : undefined;
    let view: ResultView = 'open';
    let under: number | undefined;
    if (r.outcome === 'failed') view = 'open';
    else if ((r.source === 'task' || r.source === 'subagent') && r.outcome === 'done' && started === false) view = 'folded';
    else {
      // Covered by the agent's next reply, before the operator says anything else: the reply is the message.
      for (let j = i + 1; j < nodes.length; j++) {
        if (isPerson(nodes[j])) break;
        if (nodes[j].kind === 'assistant') { under = j; break; }
      }
      if (under !== undefined) view = 'receipt';
    }
    resultAt.set(i, views.length);
    views.push({ r, view, under });
  });
  const out: RenderNode[] = [];
  const push = (r: FinishedResult, view: ResultView, key: string, ts?: string) => {
    const prev = out[out.length - 1];
    if (prev && prev.kind === 'result' && prev.view === view) prev.results.push(r);
    else out.push({ kind: 'result', key: `result:${key}`, view, ...(ts ? { ts } : {}), results: [r] });
  };
  nodes.forEach((n, i) => {
    const vi = resultAt.get(i);
    if (vi !== undefined) {
      const v = views[vi];
      if (v.view !== 'receipt') push(v.r, v.view, n.key, n.ts);
      return;
    }
    out.push(n);
    // Receipts for the results this reply covers, right under it.
    for (const v of views) if (v.view === 'receipt' && v.under === i) push(v.r, 'receipt', `${n.key}:${v.r.key}`, n.ts);
  });
  return out;
}

/** Stack consecutive results in a list of feed items. For each index: undefined (not a result), null (folded into
 *  the stack an earlier item opened), or the run of results that item opens. */
export function stackRuns<T>(items: T[], resultOf: (x: T) => FinishedResult | undefined): (FinishedResult[] | null | undefined)[] {
  const out: (FinishedResult[] | null | undefined)[] = [];
  let open: FinishedResult[] | null = null;
  for (const x of items) {
    const r = resultOf(x);
    if (!r) { open = null; out.push(undefined); continue; }
    if (open) { open.push(r); out.push(null); continue; }
    open = [r];
    out.push(open);
  }
  return out;
}
