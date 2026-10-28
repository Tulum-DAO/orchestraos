/**
 * liveStatus.ts — working/idle from telemetryd's live feed (GET /api/status/stream), laid over the
 * 3 s /api/agents poll.
 *
 * Why: the poll serves a cached detector scan, so a short turn could begin and end between two
 * readings and the page never showed "working", or showed it after the reply had landed. The
 * stream re-broadcasts telemetryd's realtime/status.json within a second of a change.
 *
 * It decides ONE thing, working vs idle. Its words are telemetryd's, not the detector's
 * (streaming / computing / idle / stalled / waiting_permission / offline_crashed), so they are
 * mapped here. Everything else keeps coming from the poll, which carries what the stream does not:
 * a pending menu, unsent text in the agent's box, a stopped or retired seat. So the overlay only
 * replaces a detector reading that is itself working or idle, never a menu, a draft or a stop.
 */

/** telemetryd word -> the dashboard's; anything else is not the stream's call. */
export function liveWord(status: string | undefined): 'working' | 'idle' | null {
  if (status === 'streaming' || status === 'computing') return 'working';
  if (status === 'idle') return 'idle';
  return null;
}

/** Detector readings the stream may replace: only the plain working/idle axis. */
const REPLACEABLE = new Set(['working', 'thinking', 'idle']);

export interface LiveSeat { status: string; at: number }

interface AgentLike { id?: string; tmux_session?: string; status?: string; pending_menu?: unknown }

/** Each agent's status with the live working/idle laid over, by agent id or tmux session. */
export function overlayLiveStatus<T extends AgentLike>(agents: readonly T[], live: Readonly<Record<string, LiveSeat>>): T[] {
  return agents.map((a) => {
    const seat = live[a.id ?? ''] ?? (a.tmux_session ? live[a.tmux_session] : undefined);
    const word = liveWord(seat?.status);
    if (!word || !REPLACEABLE.has(a.status ?? '') || a.pending_menu) return a;
    const status = word === 'working' && a.status === 'thinking' ? a.status : word;
    return status === a.status ? a : { ...a, status };
  });
}

/** The /api/agents payload, with the overlay applied to its agents list. */
export function overlayAgentsPayload<P>(payload: P, live: Readonly<Record<string, LiveSeat>>): P {
  const agents = (payload as { agents?: AgentLike[] } | undefined)?.agents;
  if (!Array.isArray(agents) || !Object.keys(live).length) return payload;
  return { ...(payload as object), agents: overlayLiveStatus(agents, live) } as P;
}
