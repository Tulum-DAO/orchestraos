/**
 * turnParts.ts — a streamed Arturo turn as the sequence it happened in: text, a tool card,
 * more text (DEC-1790747153605131).
 *
 * The server emits text.delta, tool.call and tool.result frames in order; these reducers fold
 * them into parts. Pure and immutable, so the order and the pairing are tested without a DOM.
 */

export type ToolStatus = 'running' | 'ok' | 'failed';

export interface ToolPart {
  kind: 'tool';
  callId: string;
  name: string;
  argsSummary: string;
  status: ToolStatus;
  summary?: string;
}

export interface TextPart {
  kind: 'text';
  text: string;
}

export type TurnPart = TextPart | ToolPart;

export interface ToolCallEvent { call_id?: string; name: string; args_summary?: string }
export interface ToolResultEvent { call_id?: string; name: string; ok: boolean; summary?: string }

/** A delta grows the last part when it is text; after a tool card it opens a new text part. */
export function applyTextDelta(parts: TurnPart[], text: string): TurnPart[] {
  if (!text) return parts;
  const last = parts[parts.length - 1];
  if (last && last.kind === 'text') {
    return [...parts.slice(0, -1), { kind: 'text', text: last.text + text }];
  }
  return [...parts, { kind: 'text', text }];
}

/** A call opens a running card. An older server sends no call_id; the position stands in. */
export function applyToolCall(parts: TurnPart[], e: ToolCallEvent): TurnPart[] {
  return [...parts, {
    kind: 'tool',
    callId: e.call_id || `pos${parts.length}`,
    name: e.name,
    argsSummary: e.args_summary || '',
    status: 'running',
  }];
}

/**
 * A result closes ITS call: by call_id, or — from an older server without ids — the first
 * running call of that name. A result for a call never seen is dropped, never invented.
 */
export function applyToolResult(parts: TurnPart[], e: ToolResultEvent): TurnPart[] {
  const at = parts.findIndex((p) => p.kind === 'tool' && (e.call_id
    ? p.callId === e.call_id
    : p.status === 'running' && p.name === e.name));
  if (at < 0) return parts;
  const call = parts[at] as ToolPart;
  const next = [...parts];
  next[at] = { ...call, status: e.ok ? 'ok' : 'failed', summary: e.summary ?? '' };
  return next;
}

export type PartGroup = TextPart | { kind: 'tools'; tools: ToolPart[] };

/** Consecutive tool cards render as one run; text between them breaks the run. */
export function groupParts(parts: TurnPart[]): PartGroup[] {
  const out: PartGroup[] = [];
  for (const p of parts) {
    const last = out[out.length - 1];
    if (p.kind === 'tool') {
      if (last && last.kind === 'tools') last.tools.push(p);
      else out.push({ kind: 'tools', tools: [p] });
    } else {
      out.push(p);
    }
  }
  return out;
}

export function hasToolParts(parts?: TurnPart[] | null): boolean {
  return !!parts && parts.some((p) => p.kind === 'tool');
}

/** Back-to-back tool-only turns of a call as ONE turn, so they draw as one run, the way the live call keeps them (a
 *  thread read back holds each tool run as its own row). The first turn's key stays; anything else breaks the run. */
export function foldToolTurns<T extends { role: string; text: string; parts?: TurnPart[] }>(turns: T[]): T[] {
  const toolOnly = (t: T) => t.role !== 'user' && !t.text.trim() && !!t.parts?.length && t.parts.every((p) => p.kind === 'tool');
  const out: T[] = [];
  for (const t of turns) {
    const last = out[out.length - 1];
    if (last && toolOnly(last) && toolOnly(t)) {
      // one tool card keys its rows by call id: a clash (two stored rows without an id) gets the run's position
      const seen = new Set(last.parts!.map((p) => (p as ToolPart).callId));
      const more = t.parts!.map((p, i) => (seen.has((p as ToolPart).callId)
        ? { ...p, callId: `${(p as ToolPart).callId}#${last.parts!.length + i}` } : p));
      out[out.length - 1] = { ...last, parts: [...last.parts!, ...more] };
    } else out.push(t);
  }
  return out;
}

/** Arturo's tools that hand the work to a background job (services/arturo: async_task and the tools built on it). */
const JOB_TOOLS = new Set(['async_task', 'research', 'deep_query', 'deep_think', 'ask_gm', 'spawn_agent']);

/** Whether a turn's tool group gets the "Background job · open" chip: a job tool started from the turn, never a
 *  call's own tool runs (`callTools`: a saved call_tool row, or the live call's tool turn), which are no job in
 *  Arturo's tray. */
export function jobChip(turn: { callTools?: boolean }, tools: { name: string }[]): boolean {
  return !turn.callTools && tools.some((t) => JOB_TOOLS.has(t.name));
}
