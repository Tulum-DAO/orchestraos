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
 *  - a tmux seat: its transcript (stepFromTranscript), with the pane probe (scripts/agent-status.py through
 *    services/agent-status.ts) for what only the screen knows: idle, a held question or permission, a seat that is
 *    down. The probe sees no tool for a Claude Code pane (measured 2026-11-22: 75 of 75 steps "thinking" through three
 *    Bash calls); the session log has each call as it starts, with its own description.
 *
 * `described`: the summary is the call's own description (Bash's "description"), shown verbatim. `writing`: a headless
 * seat is writing its reply (a thinking step, so no new kind reaches a client that knows only four).
 *
 * Only the seat's own main conversation counts: a subagent's tools are its own (the seat is "running a subagent").
 */
import { basename, join } from 'path';
import { redactSecrets } from '../lib/redactSecrets.js';
import { BACKLOG_BYTES, LogFollower } from './live-text.js';

export type StepKind = 'tool' | 'thinking' | 'waiting' | 'idle';
export interface StepBody { kind: StepKind; tool?: string; summary?: string; described?: true; writing?: true; since: string }
export interface AgentStep extends StepBody { agent_id: string }

export const SUMMARY_MAX = 120;
/** How long a result just back (or a between-call line) outranks the probe's idle: after it the CLI always goes on, unless
 *  the person interrupted the call, which ends the turn with no reply. */
export const RESULT_GRACE_MS = 30_000;

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
  return capped(raw);
}

/** One line of words for the step line: secrets removed, whitespace folded, at most SUMMARY_MAX characters (cut at a
 *  word when one is near). */
function capped(raw: string): string | undefined {
  const text = oneLine(redactSecrets(oneLine(raw)).text);
  if (!text) return undefined;
  if (text.length <= SUMMARY_MAX) return text;
  const cut = text.slice(0, SUMMARY_MAX - 1);
  const space = cut.lastIndexOf(' ');
  return `${(space > SUMMARY_MAX * 0.6 ? cut.slice(0, space) : cut).trimEnd()}…`;
}

/** A call's step: its own description when it has one (verbatim), else the summary of its input. A subagent call's
 *  description is the subagent's task: its summary already, never marked described. */
function callStep(tool: string, input: unknown, since: string): StepBody {
  const i = (input && typeof input === 'object' ? input : {}) as Record<string, unknown>;
  const own = tool === 'Task' || tool === 'Agent' ? undefined : capped(str(i.description).split('\n')[0]);
  const summary = own ?? stepSummary(tool, input);
  return { kind: 'tool', tool, ...(summary ? { summary } : {}), ...(own ? { described: true as const } : {}), since };
}

/** The first line of the agent's words, as the line shows them: Markdown marks (bold, code, a heading, a bullet) are not
 *  words. */
function firstLine(text: string): string | undefined {
  const line = text.split('\n').map((l) => l.trim()).find(Boolean) ?? '';
  return capped(line.replace(/^(?:#{1,6}\s+|>\s*|[-*+]\s+|\d+[.)]\s+)/, '').replace(/\*\*|__|`/g, ''));
}

const same = (a: StepBody | null, b: StepBody | null) =>
  !!a && !!b && a.kind === b.kind && a.tool === b.tool && a.summary === b.summary && a.writing === b.writing;

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
        this.set(callStep(last.name, last.input, since));
        return;
      }
      case 'stream_event': {
        const e = ev.event || {};
        if (e.type === 'message_start') { if (this.step?.kind !== 'tool') this.set({ kind: 'thinking', since }); return; }
        if (e.type !== 'content_block_start') return;
        const b = e.content_block || {};
        // The tool's name is known as its block starts; its input (and so the summary) when the block is finished.
        if (b.type === 'tool_use' && typeof b.name === 'string') this.set({ kind: 'tool', tool: b.name, since });
        else if (b.type === 'thinking') this.set({ kind: 'thinking', since });
        else if (b.type === 'text') this.set({ kind: 'thinking', writing: true, since });
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

/** Why an assistant message stopped, when that ends the turn: anything but a call (tool_use) or a server-side pause.
 *  Measured 2026-11-24 over 57 live transcripts: 4813 replies with a call stopped tool_use, 869 text replies stopped
 *  end_turn or stop_sequence, none mixed. */
const TURN_ENDS = new Set(['end_turn', 'stop_sequence', 'max_tokens', 'refusal']);

function metaText(o: any): string {
  const c = o?.message?.content;
  if (typeof c === 'string') return c;
  return Array.isArray(c) ? c.filter((b: any) => b?.type === 'text').map((b: any) => str(b.text)).join('\n') : '';
}

/** How many of a log's newest raw lines the turn marks look at (turnEndFromLines, finalReplyUuids). */
export const TURN_SCAN_LINES = 200;
const CLAUDE_TYPES = new Set(['user', 'assistant', 'system', 'attachment']);

/** The uuids of final replies' lines among the newest raw lines: never a line's words, even once a blocking Stop hook has
 *  reopened the turn (gm 00:41:46Z: "Seen it: ...", then "Stop hook feedback", then calls). Pure. */
export function finalReplyUuids(lines: readonly string[], max = TURN_SCAN_LINES): Set<string> {
  const out = new Set<string>();
  for (let i = lines.length - 1, n = 0; i >= 0 && n < max; i--, n++) {
    if (!lines[i].includes('"assistant"')) continue;
    let o: any;
    try { o = JSON.parse(lines[i]); } catch { continue; }
    if (o?.type === 'assistant' && TURN_ENDS.has(o.message?.stop_reason) && typeof o.uuid === 'string') out.add(o.uuid);
  }
  return out;
}

/** The turn as the newest raw lines mark it: when it ended (else null), whether a line decided that at all (`decided`
 *  false: nothing but lines that say nothing about the turn, such as attachments or a subagent's own), and whether the
 *  lines are Claude Code's (`claude`: a line of its own types with a uuid). */
export function turnScan(lines: readonly string[], max = TURN_SCAN_LINES): { ended: string | null; decided: boolean; claude: boolean } {
  let claude = false;
  for (let i = lines.length - 1, n = 0; i >= 0 && n < max; i--, n++) {
    let o: any;
    try { o = JSON.parse(lines[i]); } catch { continue; }
    if (!o || typeof o !== 'object') continue;
    if (typeof o.uuid === 'string' && CLAUDE_TYPES.has(o.type)) claude = true;
    if (o.isSidechain === true) continue;
    const ts = str(o.timestamp);
    if (o.type === 'system') { if (o.subtype === 'turn_duration' && ts) return { ended: ts, decided: true, claude: true }; continue; }
    if (o.type === 'assistant') {
      const content = Array.isArray(o.message?.content) ? o.message.content : [];
      const text = content.some((b: any) => b?.type === 'text' && str(b.text).trim());
      return { ended: TURN_ENDS.has(o.message?.stop_reason) && text && ts ? ts : null, decided: true, claude: true };
    }
    if (o.type === 'user') {
      if (o.isMeta !== true) return { ended: null, decided: true, claude: true };                        // a result, or a message: the turn goes on
      if (/^\s*Stop hook feedback:/.test(metaText(o))) return { ended: null, decided: true, claude: true };  // a blocking Stop hook: the agent goes on
    }
  }
  return { ended: null, decided: false, claude };
}

/** When the turn in a Claude Code log ended, from its newest raw lines (gm 2026-11-24, A): the final reply's text line
 *  (its message stopped for a reason other than a call) or the CLI's own turn_duration line. Null while the turn goes
 *  on (a call, a result, a message received, a Stop-hook continuation, a reply whose text has not landed) or when the
 *  lines are not Claude Code's. The parsed items carry neither marker, and the wire items stay as they are. Pure. */
export function turnEndFromLines(lines: readonly string[], max = TURN_SCAN_LINES): string | null {
  return turnScan(lines, max).ended;
}

/** Between calls (gm 2026-11-24, B/C): the words that led into the call that just finished (its milestone line), else
 *  that call's own description (marked `described`, as a running call's is: clients shorten it to a label), else none.
 *  Never the reply: a final reply ends the turn (turnEndFromLines). */
function betweenCalls(items: readonly any[], call: number, since: string, finals: ReadonlySet<string>): StepBody {
  for (let j = call - 1; j >= 0; j--) {
    const it = items[j];
    if (it?.kind === 'tool_use' || (it?.kind === 'thinking' && !str(it.text).trim())) continue;   // its batch, empty thinking
    if (it?.kind === 'text' && it.role === 'assistant' && !finals.has(str(it.uuid))) {
      const summary = firstLine(str(it.text));
      if (summary) return { kind: 'thinking', summary, since };
    }
    break;
  }
  const own = callStep(str(items[call].tool), items[call].input, since);
  return own.described && own.summary ? { kind: 'thinking', summary: own.summary, described: true, since } : { kind: 'thinking', since };
}

/** What the transcript says the agent is doing, from its newest items: a call still running (its own description), the
 *  agent's latest words after the last result, between calls the call's milestone (betweenCalls), a message just
 *  received (thinking, no words). `since` is that item's own time, so the same step is the same frame. Null: nothing to
 *  go on. Pure. */
const NO_FINALS: ReadonlySet<string> = new Set();

export function transcriptStep(items: readonly any[], finals: ReadonlySet<string> = NO_FINALS): StepBody | null {
  const done = new Set<string>();
  let back: string | undefined;                                // the newest result's time
  for (let i = items.length - 1; i >= 0; i--) {
    const it = items[i];
    const ts = str(it?.ts);
    if (!ts) continue;
    if (it.kind === 'tool_result') { if (typeof it.tool_use_id === 'string') done.add(it.tool_use_id); back ??= ts; continue; }
    if (it.kind === 'tool_use') {
      if (!(typeof it.id === 'string' && done.has(it.id))) return callStep(str(it.tool), it.input, ts);
      return betweenCalls(items, i, back ?? ts, finals);
    }
    if (it.kind === 'text' && it.role === 'assistant') {
      if (back) return { kind: 'thinking', since: back };
      // A final reply is not what it is doing: here the turn was reopened (a blocking Stop hook), no words yet.
      if (finals.has(str(it.uuid))) return { kind: 'thinking', since: ts };
      const summary = firstLine(str(it.text));
      if (!summary) continue;
      return { kind: 'thinking', summary, since: ts };
    }
    if (it.kind === 'text' && it.role === 'user') return { kind: 'thinking', since: back ?? ts };
  }
  return back ? { kind: 'thinking', since: back } : null;
}

/** The newest line is a result just back or a between-call line, within RESULT_GRACE_MS: the turn goes on. */
function goesOn(items: readonly any[], now: number): boolean {
  for (let i = items.length - 1; i >= 0; i--) {
    const it = items[i];
    if (!str(it?.ts) || it.kind === 'thinking') continue;
    const fresh = now - Date.parse(it.ts) < RESULT_GRACE_MS;
    if (it.kind === 'tool_result') return fresh;
    return fresh && it.kind === 'text' && it.role === 'assistant' && it.narration === true;
  }
  return false;
}

/** A tmux seat's step: the transcript's, unless the probe knows better. The probe alone knows a seat is down (no step),
 *  idle, or held on a question or permission; it is a beat behind, so a transcript step NEWER than the probe's idle or
 *  waiting began is the agent working again. Pure. */
export function stepFromTranscript(items: readonly any[], d: { state?: string; tool?: string; state_age_s?: number } | undefined,
  now: number = Date.now(), ended: string | null = null, finals: ReadonlySet<string> = NO_FINALS): StepBody | null {
  const probe = stepFromProbe(d, openToolUse(items), now);
  if (!probe) return null;
  // The transcript's own turn end (turnEndFromLines) is authoritative (gm 2026-11-24, A): the row goes and Stop reverts
  // in the same tick, whatever the probe's beat says. Measured on gm 00:40Z: the probe read the pane idle while the reply
  // was written, the reply's line was newer than that idle, so its first line stayed up as "what it is doing". A held
  // permission or question still says waiting.
  if (ended) return probe.kind === 'waiting' ? probe : { kind: 'idle', since: ended };
  const t = transcriptStep(items, finals);
  if (!t) return probe;
  // A call with no result yet is running: never idle (the probe has read a working Claude Code pane as idle, its start
  // always "now": measured 2026-11-22 18:45Z). A held permission or question still says waiting.
  if (probe.kind === 'idle' && t.kind === 'tool') return t;
  // A result just back, or a between-call line (narration: a call always follows), is the agent working out its next
  // call: the probe has read that as idle for 2-3 s (measured 2026-11-22 18:55Z), which hid the row between calls.
  if (probe.kind === 'idle' && goesOn(items, now)) return t;
  if ((probe.kind === 'idle' || probe.kind === 'waiting') && !(Date.parse(t.since) > Date.parse(probe.since))) return probe;
  return t;
}

/** A tmux seat's step from its tailer's items and turn marks (transcript-tail.ts turn()): none when the marks cannot say
 *  how the turn stands (`known` false: the log's newest lines are too long to keep enough of them), never a guess;
 *  else stepFromTranscript. Pure. */
export function stepFromTurn(items: readonly any[], d: { state?: string; tool?: string; state_age_s?: number } | undefined,
  turn: { ended: string | null; finals: ReadonlySet<string>; known: boolean }, now: number = Date.now()): StepBody | null {
  if (!turn.known) return null;
  return stepFromTranscript(items, d, now, turn.ended, turn.finals);
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

/** The same step still going: same kind, tool and words. A probe's start drifts (now minus a cached age), so it is not
 *  a new step; a call's start is its own line's time, so a new call with the same words is one. */
export function sameStep(a: StepBody | null, b: StepBody | null): boolean {
  return same(a, b) && (b!.kind !== 'tool' || a!.since === b!.since);
}

/** Whether two steps differ for a client (what is sent on change). */
export const stepSig = (s: StepBody | null) => (s ? `${s.kind}|${s.tool ?? ''}|${s.summary ?? ''}|${s.writing ? 'w' : ''}|${s.since}` : '');

// The step each open chat stream last worked out (routes/transcript-stream.ts), so the agent list's row says what the
// chat's line says for that seat (its call's description) instead of the probe's word alone. Gone with the stream.
const streamSteps = new Map<string, StepBody | null>();

/** A chat stream's latest step for a seat (undefined: forget it, the stream closed). */
export function noteStreamStep(agentId: string, step: StepBody | null | undefined): void {
  if (step === undefined) streamSteps.delete(agentId); else streamSteps.set(agentId, step);
}

/** The step an open chat stream has for this seat; undefined when none is open. */
export function streamStep(agentId: string): StepBody | null | undefined {
  return streamSteps.get(agentId);
}

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
