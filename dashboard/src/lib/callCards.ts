/**
 * callCards.ts — an agent's calls as the chat's call cards on the LIVE stream (a call made on an
 * agent's page shows in its chat, centred, with its length).
 *
 * The poll route (GET /api/agents/:id/transcript) already merges each ended call into the window as a `[voice-call:]`
 * user row (api lib/agentCalls.ts mergeCallMarkers), the row the chat draws as the card. The stream's snapshots and
 * deltas carry the transcript's own lines only, and once the stream is live its items replace the poll's, so the card
 * vanished. This lays the same rows in on read, from GET /api/agents/:id/calls (which carries each call's marker and
 * whether it was handed off: a handed-off call's card is its hand-off line, so it is never placed twice).
 */
export interface AgentCallRow {
  call_id: string;
  status: 'live' | 'ended';
  started_at: string;
  duration_s: number | null;
  rating: 'up' | 'down' | null;
  marker?: string;
  handed?: boolean;
}

export async function fetchCallCards(agentId: string, fetchFn: typeof fetch = fetch): Promise<AgentCallRow[]> {
  try {
    const r = await fetchFn(`/api/agents/${encodeURIComponent(agentId)}/calls`);
    if (!r.ok) return [];
    const j = await r.json().catch(() => null) as { calls?: AgentCallRow[] } | null;
    return Array.isArray(j?.calls) ? j!.calls : [];
  } catch {
    return [];
  }
}

const timeOf = (it: { ts?: unknown }): number | null => {
  const t = typeof it?.ts === 'string' ? Date.parse(it.ts) : NaN;
  return Number.isNaN(t) ? null : t;
};

/** The window with each ended, not handed-off call's card placed where the call started (after the last line at or
 *  before it). A card the window already holds (the poll path's) is left alone; a call older than the window's first
 *  line belongs to an older page and is not placed. */
export function withCallCards<T extends { kind: string; ts?: string }>(items: T[], calls: readonly AgentCallRow[]): T[] {
  const held = new Set(items.map((it) => (it as { call_id?: unknown }).call_id).filter((v): v is string => typeof v === 'string'));
  const first = items.map(timeOf).find((t) => t !== null) ?? null;
  const pick = calls
    .filter((c) => c.status === 'ended' && !c.handed && c.marker && !held.has(c.call_id))
    .filter((c) => { const t = Date.parse(c.started_at); return !Number.isNaN(t) && (first === null || t >= first); })
    .sort((a, b) => Date.parse(a.started_at) - Date.parse(b.started_at));
  if (!pick.length) return items;
  const out = items.slice();
  for (const c of pick) {
    const t = Date.parse(c.started_at);
    let at = 0;
    for (let i = 0; i < out.length; i++) { const ti = timeOf(out[i]); if (ti === null ? i === at : ti <= t) at = i + 1; }
    out.splice(at, 0, { kind: 'text', role: 'user', text: c.marker, ts: c.started_at, call_id: c.call_id,
      duration_s: c.duration_s, rating: c.rating, audience: 'user' } as unknown as T);
  }
  return out;
}
