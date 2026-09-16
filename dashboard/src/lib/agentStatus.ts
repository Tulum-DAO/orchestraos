/**
 * Canonical agent-state → display mapping, shared by every web surface (the
 * chat status pill AND the agent title/card dot) so they never drift, and
 * aligned with the iOS app's ConversationView/Shells mapping so web ↔ app
 * converge (orchestraos-app-dev field report 2026-08-09).
 *
 * SOURCE OF TRUTH for state = the /api/agents v2 detector `status` field
 * (agent-state-truth). The web serves RAW detector states (thinking,
 * waiting_permission, stranded_input, plus legacy self-reported free text);
 * the gateway serves already-mapped states. normalizeAgentState() accepts
 * EITHER vocab and collapses anything unrecognized to 'unknown'.
 */

export type LiveState =
  | 'working' | 'idle' | 'waiting' | 'stranded' | 'stalled'
  | 'stopped' | 'crashed' | 'offline' | 'unknown';

const STATE_ALIAS: Record<string, LiveState> = {
  // detector closed-enum aliases
  thinking: 'working',
  waiting_permission: 'waiting',
  stranded_input: 'stranded',
  // legacy self-reported (status_source absent) free-text aliases
  running: 'working',
  active: 'working',
  spawning: 'working',
  ready: 'idle',
};

export function normalizeAgentState(s?: string): LiveState {
  if (!s) return 'unknown';
  if (STATE_ALIAS[s]) return STATE_ALIAS[s];
  return (s in STATE_STYLE ? (s as LiveState) : 'unknown');
}

// Colors: working = ORANGE + pulse (in-flight) — the operator ruled 2026-08-10 the web
// keeps orange for working ("I want that, don't remove it"), overriding the
// app's brand-green-for-working mapping; iOS convergence awaits his scope
// confirmation. idle = approve-green (alive at prompt), waiting = amber
// (needs you), stopped/crashed = red, offline = gray.
// 2026-09-02 chip-color fix (orchestra-builder commission): stalled is the
// detector's 600s-quiet reclassification of a WORKING agent (long deep turns,
// e.g. rotation-autonomy-builder) — red read as crashed, so stalled = amber
// #FF9F0A (long-running, probably fine). stranded (unsent draft) = purple
// #BF5AF2 so it no longer collides with waiting's needs-you amber.
export const STATE_STYLE: Record<LiveState, { label: string; dot: string; text: string }> = {
  working:  { label: 'working',          dot: 'bg-orange-400 animate-pulse', text: 'text-orange-300' },
  idle:     { label: 'idle · at prompt', dot: 'bg-green-500',               text: 'text-green-400' },
  waiting:  { label: 'needs you',        dot: 'bg-amber-400 animate-pulse', text: 'text-amber-300' },
  stranded: { label: 'unsent draft',     dot: 'bg-[#BF5AF2]',               text: 'text-[#BF5AF2]' },
  stalled:  { label: 'stalled',          dot: 'bg-[#FF9F0A]',               text: 'text-[#FF9F0A]' },
  stopped:  { label: 'stopped',          dot: 'bg-red-500',                 text: 'text-red-400' },
  crashed:  { label: 'crashed',          dot: 'bg-red-500',                 text: 'text-red-400' },
  offline:  { label: 'offline',          dot: 'bg-neutral-600',             text: 'text-neutral-500' },
  unknown:  { label: '—',                dot: 'bg-neutral-600',             text: 'text-neutral-500' },
};
