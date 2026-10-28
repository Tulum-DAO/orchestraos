/**
 * arturoCallGroups.ts — Arturo's feed with each voice call folded into one card. A call's spoken turns
 * (thread mode "voice", or the captions a call commits on this page) and its tool runs sit together, closed
 * by the call-ended line; typed turns before and after stay in the feed as they are. During a call the
 * turns so far are the live card, last in the feed.
 */
import { hasVoiceCallMarker, parseVoiceSegments } from './voiceCall.ts';
import { DEFAULT_ASSISTANT_NAME } from './assistantName.ts';

export interface CallTurn { role: 'user' | 'arturo'; text: string; voice?: boolean; callEnd?: string; ts?: number; callTools?: boolean }

export type FeedItem<T> =
  | { kind: 'turn'; turn: T; index: number }
  | { kind: 'call'; key: string; callId: string | null; turns: T[]; live: boolean; startTs?: number; endTs?: number };

const CALL_ENDED_RE = /^\s*Call ended \(vc_[A-Za-z0-9_-]+\)\.?\s*$/;

/** The call a turn ends: the thread's call_end tag, or (on this page, before a reload) the marker line itself. */
export function callEndOf(t: CallTurn): string | null {
  if (t.callEnd) return t.callEnd;
  if (t.role !== 'arturo' || !hasVoiceCallMarker(t.text)) return null;
  const segs = parseVoiceSegments(t.text);
  const call = segs.find((s) => s.kind === 'voicecall');
  // Only a line that is nothing but the call ending; words of the brain's own around a marker stay a turn.
  const onlyEnd = segs.every((s) => s.kind === 'voicecall' || CALL_ENDED_RE.test(s.text) || !s.text.trim());
  return call && call.kind === 'voicecall' && onlyEnd ? call.callId : null;
}

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

/** A call's length the way a phone shows it: "00:15", "12:04", "1:02:03". */
export function fmtClock(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return '--:--';
  const s = Math.floor(seconds), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  const pad = (n: number) => String(n).padStart(2, '0');
  return h ? `${h}:${pad(m)}:${pad(r)}` : `${pad(m)}:${pad(r)}`;
}

/** The transcript as plain text, for the card's Copy. */
export function transcriptText(turns: { role: string; text: string }[], name = DEFAULT_ASSISTANT_NAME): string {
  return turns.filter((t) => t.text.trim()).map((t) => `${t.role === 'user' ? 'You' : name}: ${t.text.trim()}`).join('\n');
}
