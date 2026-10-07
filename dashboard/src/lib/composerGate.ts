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


// NOT the bare word 'queued': ChatInput reuses its refusal panel for a SUCCESSFUL queued/held send
// and sets state 'queued'/'held' there (review of #197). The gateway never sends bare 'queued'
// (it maps queued_input -> 'stranded'), so only the detector's own word belongs here.
const STRANDED_WORDS = new Set(['stranded', 'stranded_input', 'queued_input']);

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

/** The whole headline of ChatInput's send panel. That panel is reused for a SUCCESSFUL
 *  queued/held send (state 'queued'/'held'), which must never read "Not delivered" (gm msg_fbc3b9d0). */
export function sendPanelHeadline(r: { state?: string; activity?: string }): string {
  // panel 'queued' = the server's durable state; the mid-turn hold arrives as 'held' with
  // describeSendState's busy_working words already in activity (gm msg_d8732aa5).
  if (r.state === 'queued') return STATE_COPY.queuedDurable;
  if (r.state === 'held') return r.activity || 'Held — will deliver at the next turn boundary';
  return 'Not delivered — ' + refusalHeadline(r);
}

/** Does this send carry the pending photo? A forced retry normally carries text only (the photo of
 *  a queued/held send was already uploaded and cleared). A forced retry of a REFUSED send (panel
 *  state 'stranded') must carry the photo the refusal kept, or it is silently dropped (#198 review). */
export function shouldSendPhoto(hasPhoto: boolean, force: boolean, panelState?: string): boolean {
  return hasPhoto && (!force || panelState === 'stranded');
}

/** Did the message REACH the server, so the composer text should clear? Delivered, queued and held
 *  all did; a refusal (composer-hold) or a failure did not, and must keep the operator's text. */
export function clearsComposer(r: { ok?: boolean; queued?: boolean; held?: boolean; refused?: boolean }): boolean {
  if (r.refused) return false;
  return !!(r.ok || r.queued || r.held);
}

/** The text a send carries. A forced retry prefers the box's CURRENT text (the operator may have
 *  edited it after the refusal) and falls back to the refused attempt only when the box is empty. */
export function retryText(force: boolean, boxText: string, attemptText?: string): string {
  if (!force) return boxText;
  if (!boxText) return attemptText || '';
  // The inject path uploads a photo up front, and from then on it exists ONLY as a leading
  // `[IMAGE: path]` / `[FILE: path]` marker inside attemptText (pendingImage is already cleared).
  // Carry those markers onto the edited text, or the screenshot silently disappears (#198 review).
  const markers = (attemptText || '').match(/^(?:\[(?:IMAGE|FILE): [^\]]+\]\s*)+/);
  return markers ? `${markers[0].trim()} ${boxText}` : boxText;
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
  if (st === 'retired') {
    return { send: 'blocked', reason: STATE_COPY.retired };
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
