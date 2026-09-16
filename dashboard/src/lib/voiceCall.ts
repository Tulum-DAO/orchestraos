/**
 * [voice-call:] marker grammar (shared chat grammar; spec 2026-08-09-arturo-
 * voice-mode-design §3 + agent-state-truth §7.2). The Arturo proxy injects
 * end-of-call summaries as USER composer input carrying a marker:
 *
 *   [voice-call: <call_id> <abs-json-path>]
 *
 * call_id ^vc_[A-Za-z0-9_-]{4,64}$, path under state/voice-calls/ — BOTH
 * server-generated (no user-controlled name field, unlike [attached:], so the
 * parse is simple + unquoted). The card fetches turns via the API/gateway
 * (GET /api/voice/call?call_id=), NOT by reading the path from the marker.
 *
 * Parsed in USER text rows; the marker LINE is stripped from the visible bubble;
 * one card per marker. Parse BEFORE pasted-text handling (iOS parses it before
 * [attached:] for the same reason — markers are chrome, not content).
 */

// Whole-line marker (server-generated, so it occupies its own line). We capture
// call_id; the path is intentionally ignored (fetch is by id via the gateway).
const MARKER_RE = /\[voice-call:\s+(vc_[A-Za-z0-9_-]{4,64})\s+(\S+)\]/;
const MARKER_RE_G = /\[voice-call:\s+(vc_[A-Za-z0-9_-]{4,64})\s+(\S+)\]/g;

export function hasVoiceCallMarker(text: string): boolean {
  return MARKER_RE.test(text);
}

export type VoiceSegment =
  | { kind: 'prose'; text: string }
  | { kind: 'voicecall'; callId: string };

/**
 * Split a user message into ordered prose / voice-call segments. The marker is
 * removed at its position (a card renders there); surrounding prose is kept
 * verbatim. Returns a single prose segment when no marker is present (fast path).
 */
export function parseVoiceSegments(text: string): VoiceSegment[] {
  if (!hasVoiceCallMarker(text)) return [{ kind: 'prose', text }];
  const segs: VoiceSegment[] = [];
  let last = 0;
  for (const m of text.matchAll(MARKER_RE_G)) {
    const start = m.index ?? 0;
    const before = text.slice(last, start).replace(/\n+$/,'');
    if (before.trim()) segs.push({ kind: 'prose', text: before });
    segs.push({ kind: 'voicecall', callId: m[1] });
    last = start + m[0].length;
  }
  const after = text.slice(last).replace(/^\n+/,'');
  if (after.trim()) segs.push({ kind: 'prose', text: after });
  return segs.length ? segs : [{ kind: 'prose', text }];
}

// ── header formatting helpers (shared by the card) ──────────────────────────

/** Duration "Xm Ys" / "Ys" from epoch-second start/end. */
export function fmtCallDuration(startedAt?: number, endedAt?: number): string {
  if (!startedAt || !endedAt || endedAt < startedAt) return '—';
  const s = Math.round(endedAt - startedAt);
  const m = Math.floor(s / 60);
  return m > 0 ? `${m}m ${s % 60}s` : `${s}s`;
}

/** A live call whose start is >4h old renders stale, not live (§7.3). */
export const STALE_LIVE_AGE_S = 4 * 3600;
export function isStaleLive(status?: string, startedAt?: number): boolean {
  if (status !== 'live' || !startedAt) return false;
  return (Date.now() / 1000) - startedAt > STALE_LIVE_AGE_S;
}
