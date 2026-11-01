/**
 * seatSettings.ts — an agent's compaction settings as its setup slideout shows them (GET/PUT /api/agents/:id/settings):
 * whether its conversation is compacted automatically ("Auto-compact", off unless turned on), the share of its context
 * window at which that happens, and whether its chat shows the compaction notice. Per agent, not per person. A change
 * applies from the agent's next turn; the server keeps who changed what. The person can also type /compact in the
 * chat at any time, whatever these say.
 *
 * "Compact at" is editable from 40 to 85 per cent; above it sits a safety limit (`guard_pct`) nobody can change here.
 */

export const COMPACT_MIN = 40;
export const COMPACT_MAX = 85;
export const COMPACT_DEFAULT = 65;

export interface SeatSettings {
  /** Compacted automatically when "Compact at" is reached. Off by default. */
  auto_compact: boolean;
  show_compaction: boolean;
  compact_at: number;
  /** The safety limit above "Compact at" (not editable). */
  guard_pct?: number;
  /** Whether this agent's conversation is compacted by us at all. Absent means it is (every agent but Arturo). */
  rotates?: boolean;
  /** Whether a person's /compact can run for it now (the server's gate; absent: the server does not say). */
  compact_now?: boolean;
}

/** A typed "Compact at": a whole number of per cent from 40 to 85, or the reason it is not, in words. */
export function checkCompactAt(input: string | number): { ok: true; value: number } | { ok: false; error: string } {
  const text = String(input).trim().replace(/%$/, '').trim();
  if (!text) return { ok: false, error: `Enter a number from ${COMPACT_MIN} to ${COMPACT_MAX}.` };
  if (!/^\d+$/.test(text)) return { ok: false, error: `Use a whole number from ${COMPACT_MIN} to ${COMPACT_MAX}.` };
  const n = Number(text);
  if (n < COMPACT_MIN || n > COMPACT_MAX) return { ok: false, error: `Compact at must be from ${COMPACT_MIN}% to ${COMPACT_MAX}%.` };
  return { ok: true, value: n };
}

/** One step of the stepper, kept inside the range. */
export function stepCompactAt(value: number, by: number): number {
  return Math.min(COMPACT_MAX, Math.max(COMPACT_MIN, Math.round(value) + by));
}

/** The server's answer as settings: its own values where it gives them, the built-in defaults otherwise. */
export function settingsFromJson(json: unknown): SeatSettings | null {
  const o = json as Record<string, unknown> | null;
  if (!o || typeof o !== 'object') return null;
  const s = (o.settings && typeof o.settings === 'object' ? o.settings : o) as Record<string, unknown>;
  if (typeof s.show_compaction !== 'boolean' && typeof s.compact_at !== 'number' && typeof s.auto_compact !== 'boolean') return null;
  return {
    auto_compact: s.auto_compact === true,
    show_compaction: typeof s.show_compaction === 'boolean' ? s.show_compaction : true,
    compact_at: typeof s.compact_at === 'number' ? s.compact_at : COMPACT_DEFAULT,
    ...(typeof s.guard_pct === 'number' ? { guard_pct: s.guard_pct } : {}),
    ...(typeof s.rotates === 'boolean' ? { rotates: s.rotates } : {}),
    ...(typeof s.compact_now === 'boolean' ? { compact_now: s.compact_now } : {}),
  };
}

/** Whether the slideout shows these controls: Arturo's conversation is not compacted by us yet, so his only when the
 *  server says it is; every other agent's unless the server says it is not. */
export function showsRotationSettings(isArturo: boolean, s: SeatSettings | null): boolean {
  return isArturo ? s?.rotates === true : s?.rotates !== false;
}

const url = (agentId: string) => `/api/agents/${encodeURIComponent(agentId)}/settings`;

export async function fetchSeatSettings(agentId: string, fetchFn: typeof fetch = fetch): Promise<SeatSettings> {
  let r: Response;
  try { r = await fetchFn(url(agentId)); } catch { throw new Error('Could not reach the server to read the settings.'); }
  if (!r.ok) throw new Error(r.status === 404 ? 'This agent has no settings here.' : `Could not read the settings (${r.status}).`);
  const s = settingsFromJson(await r.json());
  if (!s) throw new Error('The settings came back in a shape this page does not read.');
  return s;
}

/** Save a change: the settings as saved (null when the server answers without them), or its reason, in words, when
 *  it refuses. */
export type SettingsChange = Partial<Pick<SeatSettings, 'auto_compact' | 'show_compaction' | 'compact_at'>>;

export async function saveSeatSettings(agentId: string, change: SettingsChange,
  fetchFn: typeof fetch = fetch): Promise<SeatSettings | null> {
  let r: Response;
  try {
    r = await fetchFn(url(agentId), { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(change) });
  } catch {
    throw new Error('Could not reach the server. Nothing was changed.');
  }
  let body: unknown = null;
  try { body = await r.json(); } catch { /* an empty or non-JSON answer: its status says enough */ }
  if (!r.ok) {
    const why = (body as { error?: unknown } | null)?.error;
    throw new Error(typeof why === 'string' && why ? why : r.status === 403 ? 'You are not allowed to change this agent.' : `Could not save (${r.status}).`);
  }
  return settingsFromJson(body);
}
