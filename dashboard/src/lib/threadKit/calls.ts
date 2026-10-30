/**
 * calls.ts — the thread kit's voice calls: one reading of a call wherever a conversation shows one (Arturo's page and
 * pane, every agent's chat), for the kit's call card (components/kit/CallCard.tsx). It was three copies: the marker
 * grammar (lib/voiceCall.ts), the call-ended line (Arturo's page, his pane, his Copy) and the grouping of a call's
 * turns (lib/arturoCallGroups.ts); those files now re-export from here.
 *
 *  - The marker: `[voice-call: <call_id> <abs-json-path>]`, server-made (call_id ^vc_[A-Za-z0-9_-]{4,64}$). The card
 *    reads the call by id through the API, never by the path in the marker.
 *  - The call-ended line: "Call ended (vc_…)." with the marker, the line Arturo's thread stores when a call ends.
 *  - A thread's call turns (mode "voice") and tool runs, folded into one card, closed by that line.
 *  - A saved call record's turns (GET /api/voice/call): words as bubbles, tool runs as the kit's tool card.
 */
import { DEFAULT_ASSISTANT_NAME } from '../assistantName.ts';
import type { ToolCallView } from '../toolCalls.ts';

// ── The marker ──────────────────────────────────────────────────────────────────────────────────────────────────────

const MARKER_RE = /\[voice-call:\s+(vc_[A-Za-z0-9_-]{4,64})\s+(\S+)\]/;
const MARKER_RE_G = /\[voice-call:\s+(vc_[A-Za-z0-9_-]{4,64})\s+(\S+)\]/g;

export function hasVoiceCallMarker(text: string): boolean {
  return MARKER_RE.test(text);
}

export type VoiceSegment =
  | { kind: 'prose'; text: string }
  | { kind: 'voicecall'; callId: string };

/** A message split into ordered prose and call segments: the marker is removed where it sat (a card goes there) and
 *  the prose around it kept as written. No marker: one prose segment. */
export function parseVoiceSegments(text: string): VoiceSegment[] {
  if (!hasVoiceCallMarker(text)) return [{ kind: 'prose', text }];
  const segs: VoiceSegment[] = [];
  let last = 0;
  for (const m of text.matchAll(MARKER_RE_G)) {
    const start = m.index ?? 0;
    const before = text.slice(last, start).replace(/\n+$/, '');
    if (before.trim()) segs.push({ kind: 'prose', text: before });
    segs.push({ kind: 'voicecall', callId: m[1] });
    last = start + m[0].length;
  }
  const after = text.slice(last).replace(/^\n+/, '');
  if (after.trim()) segs.push({ kind: 'prose', text: after });
  return segs.length ? segs : [{ kind: 'prose', text }];
}

const CALL_ENDED_RE = /^\s*Call ended \(vc_[A-Za-z0-9_-]+\)\.?\s*$/;

/** The words a call's ending line leaves around its marker ("Call ended (vc_…)."): chrome, never shown or copied. */
export function isCallEndedLine(text: string): boolean {
  return CALL_ENDED_RE.test(text);
}

// ── A thread's calls ────────────────────────────────────────────────────────────────────────────────────────────────

export interface CallTurn { role: 'user' | 'arturo'; text: string; voice?: boolean; callEnd?: string; ts?: number; callTools?: boolean }

export type FeedItem<T> =
  | { kind: 'turn'; turn: T; index: number }
  | { kind: 'call'; key: string; callId: string | null; turns: T[]; live: boolean; startTs?: number; endTs?: number };

/** The call a turn ends: the thread's call_end tag, or (on this page, before a reload) the marker line itself. */
export function callEndOf(t: CallTurn): string | null {
  if (t.callEnd) return t.callEnd;
  if (t.role !== 'arturo' || !hasVoiceCallMarker(t.text)) return null;
  const segs = parseVoiceSegments(t.text);
  const call = segs.find((s) => s.kind === 'voicecall');
  // Only a line that is nothing but the call ending; words of the brain's own around a marker stay a turn.
  const onlyEnd = segs.every((s) => s.kind === 'voicecall' || isCallEndedLine(s.text) || !s.text.trim());
  return call && call.kind === 'voicecall' && onlyEnd ? call.callId : null;
}

/** The feed with each call folded into one card: its spoken turns and tool runs together, closed by the call-ended
 *  line; typed turns before and after stay as they are. During a call the turns so far are the live card, last. */
export function groupCalls<T extends CallTurn>(turns: T[], live: boolean): FeedItem<T>[] {
  const items: FeedItem<T>[] = [];
  let run: { t: T; i: number }[] = [];
  const close = (callId: string | null, endTs: number | undefined, isLive: boolean) => {
    const first = run[0];
    items.push({ kind: 'call', key: callId ?? `call-${first ? first.i : 'live'}`, callId, turns: run.map((r) => r.t),
                 live: isLive, startTs: run.find((r) => typeof r.t.ts === 'number')?.t.ts, endTs });
    run = [];
  };
  turns.forEach((t, i) => {
    const end = callEndOf(t);
    if (end) { close(end, t.ts, false); return; }
    if (t.voice || t.callTools) { run.push({ t, i }); return; }
    if (run.length) close(null, run[run.length - 1].t.ts, false);   // a call with no ending line: still one card
    items.push({ kind: 'turn', turn: t, index: i });
  });
  if (run.length || live) close(null, undefined, live);
  return items;
}

// ── A saved call record ─────────────────────────────────────────────────────────────────────────────────────────────

/** One turn of a saved call (GET /api/voice/call): words, or a tool run. */
export interface RecordTurn { role: string; text?: string; ts?: number | string; tool?: string; input?: unknown; result?: string; status?: string }
export interface CallRecord { call_id: string; started_at?: number; ended_at?: number; status?: string; summary?: string; turns?: RecordTurn[] }

/** A record's tool run as the kit's tool card draws any other. */
export function fromRecordTool(t: RecordTurn, i: number): ToolCallView {
  let input: Record<string, unknown> | undefined;
  const raw = typeof t.input === 'string' ? (() => { try { return JSON.parse(t.input as string); } catch { return { input: t.input }; } })() : t.input;
  if (raw && typeof raw === 'object' && !Array.isArray(raw) && Object.keys(raw).length) input = raw as Record<string, unknown>;
  const chip = input ? Object.values(input).map((v) => (typeof v === 'string' ? v : JSON.stringify(v))).join(' ').replace(/\s+/g, ' ').slice(0, 120) : '';
  return {
    id: `call-tool-${i}`, name: t.tool || 'tool',
    status: t.status === 'error' || t.status === 'failed' ? 'failed' : t.status === 'done' || t.result !== undefined ? 'ok' : 'running',
    chip, ...(input ? { input } : {}), result: t.result ?? '', todos: [], arturo: false,
  };
}

/** A call's lines for its card: who spoke and what, and the tool runs in between (consecutive runs as one card). */
export type CallLine = { kind: 'say'; role: 'user' | 'arturo'; text: string } | { kind: 'tools'; calls: ToolCallView[] };

/** The record's own "(on the call) " prefix on the caller's words is the server's note, not what was said. */
const ON_CALL = /^\(on the call\)\s*/;

export function recordLines(turns: RecordTurn[] | undefined): CallLine[] {
  const out: CallLine[] = [];
  (turns ?? []).forEach((t, i) => {
    if (t.role === 'tool') {
      const call = fromRecordTool(t, i);
      const last = out[out.length - 1];
      if (last?.kind === 'tools') last.calls.push(call); else out.push({ kind: 'tools', calls: [call] });
      return;
    }
    const text = (t.text || '').replace(ON_CALL, '').trim();
    if ((t.role === 'user' || t.role === 'arturo') && text) out.push({ kind: 'say', role: t.role, text });
  });
  return out;
}

/** Talk turns and tool runs, for the agent chat's header ("4 turns · 1 tool run"). */
export function callCounts(turns: RecordTurn[] | undefined): { talk: number; tool: number } {
  const all = turns ?? [];
  const tool = all.filter((t) => t.role === 'tool').length;
  return { talk: all.length - tool, tool };
}

// ── Words for the header ────────────────────────────────────────────────────────────────────────────────────────────

/** A call's length the way a phone shows it: "00:15", "12:04", "1:02:03". */
export function fmtClock(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return '--:--';
  const s = Math.floor(seconds), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  const pad = (n: number) => String(n).padStart(2, '0');
  return h ? `${h}:${pad(m)}:${pad(r)}` : `${pad(m)}:${pad(r)}`;
}

/** Duration "Xm Ys" / "Ys" from epoch-second start/end. */
export function fmtCallDuration(startedAt?: number, endedAt?: number): string {
  if (!startedAt || !endedAt || endedAt < startedAt) return '—';
  const s = Math.round(endedAt - startedAt);
  const m = Math.floor(s / 60);
  return m > 0 ? `${m}m ${s % 60}s` : `${s}s`;
}

/** A live call whose start is more than 4h old reads as stale, not live. */
export const STALE_LIVE_AGE_S = 4 * 3600;
export function isStaleLive(status?: string, startedAt?: number, now = Date.now() / 1000): boolean {
  if (status !== 'live' || !startedAt) return false;
  return now - startedAt > STALE_LIVE_AGE_S;
}

/** The transcript as plain text, for the card's Copy. */
export function transcriptText(turns: { role: string; text: string }[], name = DEFAULT_ASSISTANT_NAME): string {
  return turns.filter((t) => t.text.trim()).map((t) => `${t.role === 'user' ? 'You' : name}: ${t.text.trim()}`).join('\n');
}
