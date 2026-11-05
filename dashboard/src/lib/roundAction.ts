/**
 * roundAction.ts — the composer's one round button, the way Arturo's has always worked, now in every composer (the
 * agent page, the docked agent panel, Arturo's pane):
 *   - something to send (text, or an attachment)  → Send (an arrow);
 *   - nothing to send while the agent is working, where a stop exists → Stop;
 *   - nothing to send otherwise → Call (starts that agent's call, what the phone button did); End during a call;
 *   - nothing to send and no call possible here → Send, disabled, with the reason as its tooltip. Never a dead Call.
 * Pure: the button (components/kit/RoundAction.tsx) draws what this decides.
 */
export type RoundMode = 'send' | 'stop' | 'call' | 'end' | 'idle';

export interface RoundInputs {
  /** Text or an attachment is waiting to go. */
  hasContent: boolean;
  /** A send is under way. */
  sending?: boolean;
  /** Sending is blocked here (the composer's gate). */
  blocked?: boolean;
  /** Stop is offered right now (the agent is mid-turn with no menu open). */
  canStop?: boolean;
  /** This composer's call: whether one is on, and whether one can start (with why not). Absent: no call here. */
  call?: { inCall: boolean; available: boolean; reason?: string };
}

export interface RoundView { mode: RoundMode; enabled: boolean; label: string; title: string }

export function roundView(i: RoundInputs, names: { agent: string } = { agent: 'the agent' }): RoundView {
  if (i.call?.inCall) return { mode: 'end', enabled: true, label: 'End call', title: `End the call with ${names.agent}` };
  if (i.hasContent) {
    const enabled = !i.sending && !i.blocked;
    return { mode: 'send', enabled, label: 'Send', title: 'Send (Enter)' };
  }
  if (i.canStop && !i.sending) return { mode: 'stop', enabled: true, label: 'Stop the agent', title: 'Stop — interrupt the agent’s turn (Esc)' };
  if (i.call?.available) return { mode: 'call', enabled: true, label: `Call ${names.agent}`, title: `Call ${names.agent}` };
  const why = i.call ? (i.call.reason || `A call to ${names.agent} isn't possible here`) : 'Type a message to send';
  return { mode: 'idle', enabled: false, label: 'Send', title: why };
}
