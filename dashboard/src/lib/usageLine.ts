/**
 * GET /api/usage (scripts/usage_meter.py): its row shape, the shared query, and the clock format the model
 * picker's usage rings use. The sidebar's plan-usage panel that also read it was removed.
 */
export interface UsageRow {
  provider: string;
  account?: string | null;
  window?: string | null;
  used_pct?: number | null;
  resets_at?: number | null;
  as_of?: number | null;
  stale?: boolean | null;
  reason?: string | null;
}

export function clock(epochS: number, now: Date): string {
  const d = new Date(epochS * 1000);
  const sameDay = d.toDateString() === now.toDateString();
  const hm = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  return sameDay ? hm : `${d.toLocaleDateString([], { weekday: 'short' })} ${hm}`;
}

/** GET /api/usage's rows. A caller the API will not show usage to gets a 404: UsageHidden, so a surface can show
 *  nothing at all rather than "no usage". Read by the model picker's rings. */
export class UsageHidden extends Error {}

export const USAGE_QUERY_KEY = ['usage'] as const;

export async function fetchUsage(): Promise<unknown> {
  const res = await fetch('/api/usage');
  if (res.status === 404) throw new UsageHidden();
  if (!res.ok) throw new Error(`/usage: ${res.status}`);
  return (await res.json())?.usage;
}
