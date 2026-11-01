/**
 * agentCalls.ts — an agent's own calls, as the API keeps them (GET /api/agents/:id/calls/:callId, PUT …/rating):
 * the turns (You / the agent), where its work during the call started (`work`: the thread row's uuid and how many
 * steps), and the thumbs. A server without the routes (404) is "not available yet": the card falls back to the
 * saved call record as before.
 */
export interface AgentCallTurn { role: 'user' | 'agent'; text: string; ts?: string }
export interface AgentCallWork { uuid: string; ts?: string; steps: number }
export interface AgentCall {
  call_id: string; agent: string; status: 'live' | 'ended'; started_at?: string; ended_at?: string; duration_s?: number;
  turns: AgentCallTurn[] | number; rating?: 'up' | 'down' | null; work?: AgentCallWork[];
}

/** One call with its transcript; null when the server does not have it (or the routes yet). */
export async function fetchAgentCall(agentId: string, callId: string, fetchFn: typeof fetch = fetch): Promise<AgentCall | null> {
  try {
    const r = await fetchFn(`/api/agents/${encodeURIComponent(agentId)}/calls/${encodeURIComponent(callId)}`);
    if (!r.ok) return null;
    const d = await r.json().catch(() => null) as { ok?: boolean; call?: AgentCall } | null;
    return d?.ok && d.call ? d.call : null;
  } catch { return null; }
}

/** Save the thumbs; false when it did not save (the card puts the old one back). */
export async function saveCallRating(agentId: string, callId: string, rating: 'up' | 'down' | null, fetchFn: typeof fetch = fetch): Promise<boolean> {
  try {
    const r = await fetchFn(`/api/agents/${encodeURIComponent(agentId)}/calls/${encodeURIComponent(callId)}/rating`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ rating }),
    });
    return r.ok;
  } catch { return false; }
}

/** "0:13" for a call's length. */
export function callLength(s?: number | null): string | null {
  if (typeof s !== 'number' || !(s >= 0)) return null;
  const n = Math.round(s);
  return `${Math.floor(n / 60)}:${String(n % 60).padStart(2, '0')}`;
}

/** The transcript with the work chips placed by time: each where its work started, after the turn before it. */
export function withWork(turns: AgentCallTurn[], work: AgentCallWork[] = []): ({ kind: 'turn'; turn: AgentCallTurn } | { kind: 'work'; work: AgentCallWork })[] {
  const t = (x?: string) => (x ? Date.parse(x) : NaN);
  const out: ({ kind: 'turn'; turn: AgentCallTurn } | { kind: 'work'; work: AgentCallWork })[] = turns.map((turn) => ({ kind: 'turn' as const, turn }));
  for (const w of [...work].sort((a, b) => (t(a.ts) || 0) - (t(b.ts) || 0))) {
    const wt = t(w.ts);
    let at = out.length;
    if (!Number.isNaN(wt)) {
      at = 0;
      for (let i = 0; i < out.length; i++) {
        const e = out[i];
        const et = e.kind === 'turn' ? t(e.turn.ts) : t(e.work.ts);
        if (Number.isNaN(et) || et <= wt) at = i + 1;
      }
    }
    out.splice(at, 0, { kind: 'work', work: w });
  }
  return out;
}

/** Bring the thread row where a call's work started into view (ThreadView marks each row with data-row-key). */
export function focusWork(uuid: string): boolean {
  const mark = document.querySelector(`[data-row-key^="${CSS.escape(uuid)}:"]`);
  const row = mark?.nextElementSibling as HTMLElement | null;
  if (!row) return false;
  row.scrollIntoView({ block: 'center', behavior: 'smooth' });
  row.animate?.([{ backgroundColor: 'rgba(59,130,246,0.18)' }, { backgroundColor: 'transparent' }], { duration: 1400 });
  return true;
}
