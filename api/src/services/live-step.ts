/**
 * The live step: what an agent is doing right now, as one small object every surface draws the same way
 * (the agent page's line under the chat, the docked panel, the cards):
 *
 *   { agent_id, kind: 'tool' | 'thinking' | 'waiting' | 'idle', tool?, summary?, since }
 *
 * `tool` is the tool's own name ("Bash", "Read"); `summary` a short, redacted piece of its input (the command, the
 * file's name, the pattern), never more than SUMMARY_MAX characters. `since` is when this step began (ISO time).
 * The dashboard turns it into words ("Running a shell command: npm test", "Reading plan.md").
 *
 * ONE SOURCE PER SEAT, chosen by whether it runs headless:
 *  - a headless seat: its supervisor's event log (<data dir>/state/headless/<seat>/events.jsonl), read the same way
 *    the live text reads it (services/live-text.ts LogFollower). A tool starts with its tool_use block and ends with
 *    its result; a held request (a question or a permission) is waiting; the turn's result is idle.
 *  - a tmux seat: the pane probe (scripts/agent-status.py through services/agent-status.ts): its state and the tool on
 *    its screen. The probe sees no tool input, so the summary comes from the seat's transcript: the newest tool call
 *    without a result yet, when it is the same tool.
 *
 * Only the seat's own main conversation counts: a subagent's tools are its own (the seat is "running a subagent").
 */
import { basename, join } from 'path';
import { redactSecrets } from '../lib/redactSecrets.js';
import { BACKLOG_BYTES, LogFollower } from './live-text.js';

export type StepKind = 'tool' | 'thinking' | 'waiting' | 'idle';
export interface StepBody { kind: StepKind; tool?: string; summary?: string; since: string }
export interface AgentStep extends StepBody { agent_id: string }

export const SUMMARY_MAX = 80;

const oneLine = (s: string) => s.replace(/\s+/g, ' ').trim();
const str = (v: unknown) => (typeof v === 'string' ? v : '');

/** A short, safe piece of a tool's input for the line: secrets removed (lib/redactSecrets), one line, capped. Only
 *  the fields that say what the tool is doing are ever read; a tool this does not know gets no summary. */
export function stepSummary(tool: string, input: unknown): string | undefined {
  const i = (input && typeof input === 'object' ? input : {}) as Record<string, unknown>;
  let raw = '';
  switch (tool) {
    case 'Bash': raw = str(i.command).split('\n')[0]; break;
    case 'Read': case 'Write': case 'Edit': case 'MultiEdit': case 'NotebookEdit': {
      const p = str(i.file_path) || str(i.notebook_path) || str(i.path);
      raw = p ? basename(p) : '';
      break;
    }
    case 'Grep': case 'Glob': raw = str(i.pattern); break;
    case 'WebFetch': {
      const u = str(i.url);
      try { const url = new URL(u); raw = `${url.host}${url.pathname === '/' ? '' : url.pathname}`; } catch { raw = ''; }
      break;
    }
    case 'WebSearch': raw = str(i.query); break;
    case 'Task': case 'Agent': raw = str(i.description); break;
    case 'Skill': raw = str(i.skill) || str(i.name); break;
    default: raw = '';
  }
  const text = oneLine(redactSecrets(oneLine(raw)).text);
  if (!text) return undefined;
  return text.length > SUMMARY_MAX ? `${text.slice(0, SUMMARY_MAX - 1).trimEnd()}…` : text;
}

const same = (a: StepBody | null, b: StepBody | null) =>
  !!a && !!b && a.kind === b.kind && a.tool === b.tool && a.summary === b.summary;

/** A headless seat's step, over its supervisor's log records. Pure: no files; each record carries its own time. */
export class StepState {
  private step: StepBody | null = null;

  private set(next: StepBody | null): void {
    if (next && same(this.step, next)) return;           // the same step goes on: it keeps its start
    this.step = next;
  }

  apply(rec: any): void {
    if (!rec || typeof rec !== 'object') return;
    const since = typeof rec.wall === 'string' ? rec.wall : new Date().toISOString();
    if (rec.kind === 'sup') {
      if (rec.what === 'exit') { this.set({ kind: 'idle', since }); return; }
      if (rec.what !== 'state') return;
      if (rec.state === 'idle') this.set({ kind: 'idle', since });
      else if (rec.state === 'waiting') this.set({ kind: 'waiting', since });
      // A turn opened: thinking until something more precise arrives (a tool, a held request).
      else if (rec.state === 'working') { if (!this.step || this.step.kind === 'idle' || this.step.kind === 'waiting') this.set({ kind: 'thinking', since }); }
      else this.set(null);                                // restarting, failed, stopping: no step to show
      return;
    }
    if (rec.kind !== 'out') return;
    const ev = rec.ev;
    if (!ev || typeof ev !== 'object' || ev.parent_tool_use_id != null) return;
    switch (ev.type) {
      case 'result': this.set({ kind: 'idle', since }); return;
      case 'control_request':
        if (ev.request?.subtype === 'can_use_tool') this.set({ kind: 'waiting', since });
        return;
      case 'user': {
        // A tool's result: the agent goes back to working out what next.
        const content = ev.message?.content;
        if (Array.isArray(content) && content.some((b: any) => b?.type === 'tool_result')) this.set({ kind: 'thinking', since });
        return;
      }
      case 'assistant': {
        const content = ev.message?.content;
        const uses = Array.isArray(content) ? content.filter((b: any) => b?.type === 'tool_use' && typeof b.name === 'string') : [];
        if (!uses.length) return;
        const last = uses[uses.length - 1];
        const summary = stepSummary(last.name, last.input);
        this.set({ kind: 'tool', tool: last.name, ...(summary ? { summary } : {}), since });
        return;
      }
      case 'stream_event': {
        const e = ev.event || {};
        if (e.type === 'message_start') { if (this.step?.kind !== 'tool') this.set({ kind: 'thinking', since }); return; }
        if (e.type !== 'content_block_start') return;
        const b = e.content_block || {};
        // The tool's name is known as its block starts; its input (and so the summary) when the block is finished.
        if (b.type === 'tool_use' && typeof b.name === 'string') this.set({ kind: 'tool', tool: b.name, since });
        else if (b.type === 'thinking' || b.type === 'text') this.set({ kind: 'thinking', since });
        return;
      }
    }
  }

  current(): StepBody | null { return this.step; }
  clear(): void { this.step = null; }
}

// The event lines that can matter for the step; everything else is not parsed.
const INTERESTING = /"(stream_event|assistant|user|result|control_request|state|exit)"/;

/** One headless seat's step: follow its log, keep the state. */
export class LiveStep {
  readonly state = new StepState();
  private follower: LogFollower;
  constructor(path: string, backlog = BACKLOG_BYTES) { this.follower = new LogFollower(path, backlog); }

  /** Read what is new; the step now (null: none to show). */
  read(): StepBody | null {
    const { lines, reset } = this.follower.read();
    if (reset) this.state.clear();
    for (const l of lines) {
      if (!INTERESTING.test(l)) continue;
      try { this.state.apply(JSON.parse(l)); } catch { /* a torn or bad line */ }
    }
    return this.state.current();
  }
}

/** A tmux seat's step, from the pane probe's verdict, with the summary from the transcript's open tool call when it
 *  is the tool the screen shows. Null for a state that is no step (stopped, offline, unknown). Pure. */
export function stepFromProbe(d: { state?: string; tool?: string; state_age_s?: number } | undefined,
  open: { tool: string; input: unknown } | null, now: number = Date.now()): StepBody | null {
  if (!d || !d.state) return null;
  const since = new Date(now - Math.max(0, Number(d.state_age_s) || 0) * 1000).toISOString();
  const tool = str(d.tool) && d.tool !== 'unknown' ? d.tool! : '';
  switch (d.state) {
    case 'working': case 'stalled': {
      if (!tool) return { kind: 'thinking', since };
      const summary = open && open.tool === tool ? stepSummary(tool, open.input) : undefined;
      return { kind: 'tool', tool, ...(summary ? { summary } : {}), since };
    }
    case 'thinking': return { kind: 'thinking', since };
    case 'waiting': case 'waiting_permission': case 'waiting_input': return { kind: 'waiting', since };
    case 'idle': return { kind: 'idle', since };
    default: return null;
  }
}

/** The newest tool call in a transcript's items that has no result yet (the one running now), else null. */
export function openToolUse(items: readonly any[]): { tool: string; input: unknown } | null {
  const done = new Set<string>();
  for (let i = items.length - 1; i >= 0; i--) {
    const it = items[i];
    if (it?.kind === 'tool_result' && typeof it.tool_use_id === 'string') done.add(it.tool_use_id);
    else if (it?.kind === 'tool_use') return typeof it.id === 'string' && done.has(it.id) ? null : { tool: str(it.tool), input: it.input };
    else if (it?.kind === 'text' && it.role === 'assistant') return null;   // the reply came after the last call
  }
  return null;
}

/** Whether two steps differ for a client (what is sent on change). */
export const stepSig = (s: StepBody | null) => (s ? `${s.kind}|${s.tool ?? ''}|${s.summary ?? ''}|${s.since}` : '');

// One follower per headless seat, shared by everything that asks (the list route, every open chat stream): its log
// is read once, incrementally, whoever asks.
const followers = new Map<string, LiveStep>();

/** A headless seat's step now, from its supervisor log under `root` (the data dir). */
export function headlessStep(agentId: string, root: string): StepBody | null {
  let f = followers.get(agentId);
  if (!f) { f = new LiveStep(join(root, 'state', 'headless', agentId, 'events.jsonl')); followers.set(agentId, f); }
  try { return f.read(); } catch { return null; }
}

/** Forget a seat's follower (tests; a seat that left headless starts afresh when it comes back). */
export function dropHeadlessStep(agentId?: string): void {
  if (agentId === undefined) followers.clear(); else followers.delete(agentId);
}
