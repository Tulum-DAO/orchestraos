/**
 * voiceReady.ts — whether a call can start, before anyone presses Call: GET /api/voice/live/ready answers {ok:true}
 * or {ok:false, reason} (whether a voice key exists and a call will start; the key itself is never sent; the same
 * answer for every agent). The composer's round button (lib/roundAction.ts) then offers Call only when it is ok, and
 * otherwise is a disabled Send with the reason. Asked when the composer opens and when the page regains focus; never
 * polled. No answer (a server without the route: 404, or a dropped request) is "unknown": Call stays, with the
 * failure fallback.
 */
export interface VoiceReady { ok: boolean; reason?: string }

/** The server's answer, or null when it gives none (an older server, a dropped request): the caller then decides
 *  as before (a call is offered, and a failed one says why). */
export async function fetchVoiceReady(fetchFn: typeof fetch = fetch): Promise<VoiceReady | null> {
  try {
    const r = await fetchFn(`/api/voice/live/ready`);   // the same answer for every agent
    if (!r.ok) return null;
    const d = await r.json().catch(() => null) as { ok?: unknown; reason?: unknown } | null;
    if (!d || typeof d.ok !== 'boolean') return null;
    return d.ok ? { ok: true } : { ok: false, reason: typeof d.reason === 'string' && d.reason.trim() ? d.reason.trim() : "A call can't start for this agent yet" };
  } catch {
    return null;
  }
}
