/**
 * When may the composer send?
 *
 * Shaw, 2026-10-06: a seat running a sub-agent reported `working`, so chat refused to send —
 * while its CLI sat there accepting and QUEUEING the very same message ("ctrl+x ctrl+s to
 * send now" was on screen on pm-intentmagic at the time). Measured on the live fleet: 17
 * seats had delegated work in flight and three of them were in exactly that state.
 *
 * gm's ruling: gate on whether THE COMPOSER ACCEPTS INPUT, not on `state`, and not on
 * whether a sub-agent is running. Claude Code queues natively mid-turn, so the protection
 * worth keeping is only the two cases where typing actually does damage or nothing at all:
 *
 *   - a menu or permission prompt is on screen — a keystroke there ANSWERS IT, and the
 *     operator's message would be read as a choice they never made.
 *   - the pane is not running — nothing is listening, so the message is silently lost.
 *
 * `working` is NOT one of those. It never was: the agent is at a prompt that queues.
 */
import { normalizeAgentState } from './agentStatus';
import { STATE_COPY } from './stateCopy.ts';

export { STATE_COPY };

export type ComposerGate =
  | { send: 'enabled'; queued: false }
  | { send: 'enabled'; queued: true; reason: string }
  | { send: 'blocked'; reason: string };


const STRANDED_WORDS = new Set(['stranded', 'stranded_input', 'queued', 'queued_input']);

/** The words for a gateway refusal, from its `state`; null means "no state-specific words". */
export function refusalCopy(state?: string): string | null {
  // Match the RAW words: the gateway maps stranded_input AND queued_input to "stranded", and
  // not every tree's normalizeAgentState knows "stranded"/"queued" (it would fall to unknown).
  const raw = (state || '').trim().toLowerCase();
  if (STRANDED_WORDS.has(raw)) return STATE_COPY.stranded;
  if (normalizeAgentState(state) === 'waiting') return STATE_COPY.waiting;
  return null;
}

/** The "Not delivered — …" headline for a refused send: the state's words first, then whatever
 *  the gateway said. Lives here, not in the component, so the wiring is testable. */
export function refusalHeadline(r: { state?: string; activity?: string }): string {
  return refusalCopy(r.state) || r.activity || r.state || 'agent busy';
}

/** States where nothing is listening, so a message would go nowhere. */
const NOT_RUNNING = new Set(['stopped', 'crashed', 'offline', 'retired']);

export function composerGate(args: {
  state?: string;
  pendingMenu?: unknown;
  /** Delegated agents in flight. Informational only — it must NOT gate the send. */
  subagents?: number;
}): ComposerGate {
  const st = normalizeAgentState(args.state);

  // A keypress answers the menu. This is the one case where sending is actively harmful.
  if (args.pendingMenu) {
    return { send: 'blocked', reason: 'Answer the question on screen first — a message now would be read as your choice.' };
  }
  if (st === 'waiting') {
    return { send: 'blocked', reason: STATE_COPY.waiting };
  }
  if (NOT_RUNNING.has(st)) {
    return { send: 'blocked', reason: 'It is not running — a message would go nowhere.' };
  }

  // Mid-turn is FINE. The CLI queues it and consumes it when the turn ends; say so plainly
  // rather than refusing, which is the bug this function exists to fix.
  if (st === 'working') {
    return { send: 'enabled', queued: true, reason: 'Will be queued — the agent is busy and will read it when this turn ends.' };
  }
  // stalled is a live prompt that queues too, but it is NOT "will read it when this turn ends":
  // nothing has moved for ten minutes. The agreed words say so.
  if (st === 'stalled') {
    return { send: 'enabled', queued: true, reason: STATE_COPY.stalledBefore };
  }

  // idle, stranded, unknown: send normally. `unknown` sends rather than blocks, because
  // refusing on absent information is how a reachable agent becomes unreachable.
  return { send: 'enabled', queued: false };
}

/** "3 agents running" — the delegated-work affordance. Empty when there is nothing to say. */
export function delegatedWorkLabel(subagents?: number): string | null {
  if (!subagents || subagents < 1) return null;
  return `${subagents} agent${subagents === 1 ? '' : 's'} running`;
}
