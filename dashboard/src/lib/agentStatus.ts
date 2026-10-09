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
  | 'working' | 'idle' | 'waiting' | 'stranded' | 'queued' | 'stalled'
  | 'stopped' | 'crashed' | 'offline' | 'retired' | 'unknown';

const STATE_ALIAS: Record<string, LiveState> = {
  // detector closed-enum aliases
  thinking: 'working',
  waiting_permission: 'waiting',
  stranded_input: 'stranded',
  // queued_input: the CLI accepted a submit while busy and ended the turn without running it;
  // the text is still at the prompt. Purple like stranded (the same "needs a re-send" class),
  // with its own label. Never working, never idle.
  queued_input: 'queued',
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
  // WORKING vs NEEDS-YOU MUST NOT LOOK ALIKE (Shaw: both rendered the same amber-brown dot, so
  // "the one state that should shout looks identical to 'busy, leave it alone'"). Working keeps
  // the operator's orange; needs-you gets a RING as well as a brighter fill, so the two differ by
  // SHAPE and not only by hue — which also survives a colour-blind reader and a dimmed screen.
  working:  { label: 'working',          dot: 'bg-orange-400 animate-pulse', text: 'text-orange-300' },
  idle:     { label: 'idle · at prompt', dot: 'bg-green-500',               text: 'text-green-400' },
  waiting:  { label: 'needs you',        dot: 'bg-amber-300 ring-2 ring-amber-300/40 animate-pulse', text: 'text-amber-200' },
  stranded: { label: 'unsent draft',     dot: 'bg-[#BF5AF2]',               text: 'text-[#BF5AF2]' },
  queued:   { label: 'queued, not running', dot: 'bg-[#BF5AF2]',            text: 'text-[#BF5AF2]' },
  stalled:  { label: 'stalled',          dot: 'bg-[#FF9F0A]',               text: 'text-[#FF9F0A]' },
  stopped:  { label: 'stopped',          dot: 'bg-red-500',                 text: 'text-red-400' },
  crashed:  { label: 'crashed',          dot: 'bg-red-500',                 text: 'text-red-400' },
  offline:  { label: 'offline',          dot: 'bg-neutral-600',             text: 'text-neutral-500' },
  // retired = intentionally decommissioned (the `seat-gN` row every lineage
  // rotation leaves behind), NOT a down agent. Dimmer than offline so that on
  // the rare surface which does show one it reads as an archived record rather
  // than something to go fix. Default views filter these out entirely.
  retired:  { label: 'retired',          dot: 'bg-neutral-700',             text: 'text-neutral-600' },
  unknown:  { label: '—',                dot: 'bg-neutral-600',             text: 'text-neutral-500' },
};

/** The status line for a `queued` seat. The turn ENDED and the message never ran, so it needs a
 *  re-send; it is not waiting in a queue that will drain on its own. */
export const QUEUED_LINE = 'A message is at its prompt that did not run — send it again.';

/**
 * May the chat surface offer to RESUME a seat in this state?
 *
 * Only for states that mean something FAILED. `retired` is not a failure: it is the
 * intentionally-decommissioned `seat-gN` row that every lineage rotation leaves behind, and
 * resurrecting one is a different act from restarting something that fell over — it would
 * put a generation somebody deliberately ended back on the fleet.
 */
export function offersResume(state?: string): boolean {
  const st = normalizeAgentState(state);
  return st === 'stopped' || st === 'crashed' || st === 'offline';
}
