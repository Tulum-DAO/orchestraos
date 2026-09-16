/**
 * Activity-pill derivation (B3, Decision B — honest, forward-compatible).
 *
 * Jarvis on subscription auth emits NO native reasoning tokens, so the pill is
 * an HONEST activity indicator, never synthesized <thinking> theater:
 *
 *   idle → thinking → (running {tool}) → answering → idle
 *
 * Default: derive the phase from the converse stream LIFECYCLE. If the (blessed,
 * Track-A) `activity` event is present, it takes precedence and carries a label.
 * Pure reducer (no React) so it's unit-testable and reused by the store.
 */
import type { ConverseStreamEvent, ActivityPhase } from './contract';

/** UI phase — lifecycle superset of the server's ActivityPhase. */
export type UiActivityPhase = 'idle' | 'thinking' | 'running' | 'answering' | 'error' | ActivityPhase;

export interface ActivityState {
  phase: UiActivityPhase;
  /** Tool name while phase === 'running'. */
  tool?: string;
  /** Optional label (from a server activity event). */
  label?: string;
  /** True when the phase came from a server `activity` event (not lifecycle). */
  fromServer?: boolean;
}

export const initialActivity = (): ActivityState => ({ phase: 'idle' });

/** Internal marker the store dispatches when a turn is initiated (pre-network). */
type BeginMarker = { type: '_begin' };

/**
 * Fold one event (or the internal _begin marker) into the activity state.
 * Pure — returns a new state.
 */
export function deriveActivity(
  state: ActivityState,
  evt: ConverseStreamEvent | BeginMarker,
): ActivityState {
  switch (evt.type) {
    case '_begin':
      return { phase: 'thinking' };

    case 'start':
      // Keep whatever we have (thinking) — start just confirms the turn opened.
      return state.phase === 'idle' ? { phase: 'thinking' } : state;

    case 'activity':
      // Forward-compat: server-driven phase wins and carries a label.
      return { phase: evt.phase, label: evt.label, fromServer: true };

    case 'tool_call_proposed':
      return { phase: 'running', tool: evt.tool };

    case 'tool_call_decision':
      // Stay in 'running' until the call resolves.
      return state.phase === 'running' ? state : { phase: 'running', tool: state.tool };

    case 'tool_call_result':
    case 'tool_call_skipped':
      // Tool finished; return to thinking unless we were already answering.
      return state.phase === 'answering' ? state : { phase: 'thinking' };

    case 'delta':
      // First (and subsequent) answer text → answering.
      return state.phase === 'answering' ? state : { phase: 'answering' };

    case 'done':
      return { phase: 'idle' };

    case 'error':
      return { phase: 'error' };

    default:
      return state;
  }
}
