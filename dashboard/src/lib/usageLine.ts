/**
 * The agent rail's one-line plan-usage summary, from GET /api/usage (scripts/usage_meter.py).
 *
 * Pure so it can be tested without a browser. Rules carried over from the meter: a provider with no
 * reading shows its reason (never a number), and a stale reading says "as of <time>" so it is never
 * read as current.
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

export interface UsagePart {
  key: string;
  text: string;
  title: string;
  stale: boolean;
  high: boolean;
  missing: boolean;
}

const NAMES: Record<string, string> = { claude: 'Claude', codex: 'Codex', gemini: 'Gemini', grok: 'Grok' };
const WINDOWS: Record<string, string> = { '5h': '5 h', weekly: 'weekly' };

function clock(epochS: number, now: Date): string {
  const d = new Date(epochS * 1000);
  const sameDay = d.toDateString() === now.toDateString();
  const hm = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  return sameDay ? hm : `${d.toLocaleDateString([], { weekday: 'short' })} ${hm}`;
}

function isPct(v: unknown): v is number {
  return typeof v === 'number' && Number.isFinite(v) && v >= 0 && v <= 100;
}

/** One part per row with a number, then one per provider that has none (its reason in the title). */
export function usageParts(rows: unknown, now: Date = new Date()): UsagePart[] {
  if (!Array.isArray(rows)) return [];
  const parts: UsagePart[] = [];
  const multiAccount = new Set(rows.filter((r: any) => r?.provider === 'claude' && r?.account)
    .map((r: any) => r.account)).size > 1;
  for (const r of rows as UsageRow[]) {
    if (!r || typeof r.provider !== 'string') continue;
    const name = NAMES[r.provider] || r.provider;
    if (!isPct(r.used_pct)) {
      parts.push({ key: `${r.provider}:none`, text: `${name} —`, title: r.reason || `${name}: no usage reading`,
                   stale: false, high: false, missing: true });
      continue;
    }
    const who = multiAccount && r.account ? `${name} (${r.account})` : name;
    const win = (r.window && WINDOWS[r.window]) || r.window || '';
    const resets = typeof r.resets_at === 'number' && r.resets_at > 0 ? `, resets ${clock(r.resets_at, now)}` : '';
    const asOf = r.stale && typeof r.as_of === 'number' ? `, as of ${clock(r.as_of, now)}` : '';
    // A stale reading says its time IN the text, not only on hover: a phone has no hover, and a
    // dimmed number alone still reads as now.
    const seen = r.stale && typeof r.as_of === 'number' ? ` (as of ${clock(r.as_of, now)})` : '';
    const text = `${who} ${Math.round(r.used_pct)}% ${win}`.trim() + seen;
    parts.push({ key: `${r.provider}:${r.account || ''}:${r.window || ''}`, text,
                 title: `${who}: ${Math.round(r.used_pct)}% of the ${win} limit used${resets}${asOf}`,
                 stale: !!r.stale, high: r.used_pct >= 80, missing: false });
  }
  return parts;
}
