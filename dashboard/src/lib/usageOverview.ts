/**
 * usageOverview.ts — Settings › Usage as providers, not rows, in the model picker rail's look.
 *
 * Each provider with a reading is one card: its mark with the SAME ring as the rail (lib/modelPickerPanel usageFor: the
 * limit nearest to running out, on the login the agents run on), then each of its logins with one bar per window and
 * when it resets, the way Claude's own Settings › Usage, Codex's usage page and Cursor's dashboard show it. Colour by
 * how close a window is to full (green, amber from 70%, red from 90%). A provider that reports nothing is one muted
 * line, never a bar at 0%. Pure, so every case is pinned by a test without a DOM.
 */
import type { UsageRow } from './usageLine.ts';
import { AGENTS_ACCOUNT, agentsAccountOf, usageFor } from './modelPickerPanel.ts';

export type UsageTone = 'ok' | 'warn' | 'full';

export interface UsageWindowView {
  key: string;
  /** "5-hour session", "Weekly", "Weekly · Opus" */
  label: string;
  pct: number;
  tone: UsageTone;
  /** "Resets in 2 h 14 m", "Resets Mon 4:00 PM", or "" when the provider gave no time */
  resets: string;
  /** The reading is old: "as of 3:05 PM" (the bar is greyed). */
  stale: string | null;
}

export interface UsageAccountView {
  id: string;
  /** "Main login" for the default (~/.claude), else the account's id */
  label: string;
  /** The agents run on this login. */
  agents: boolean;
  windows: UsageWindowView[];
}

export interface UsageProviderView {
  id: string;
  name: string;
  /** The rail's ring: share of the limit nearest to running out on the agents' login; null = no ring. */
  ring: { pct: number; tone: UsageTone; stale: boolean } | null;
  /** "10% of weekly used" (the rail's words) */
  summary: string;
  accounts: UsageAccountView[];
}

export interface UsageOverview {
  providers: UsageProviderView[];
  /** Providers with no reading: "Gemini", "Grok", with why. `pending`: the poller is on and the first reading is on its
   *  way (say "First reading in a moment" and how to turn polling off). */
  silent: { id: string; name: string; reason: string; pending: boolean }[];
}

const NAMES: Record<string, string> = { claude: 'Claude', codex: 'ChatGPT (Codex)', gemini: 'Gemini', grok: 'Grok' };
const WINDOW_LABEL: Record<string, string> = {
  '5h': '5-hour session', weekly: 'Weekly', 'weekly-opus': 'Weekly · Opus', 'weekly-sonnet': 'Weekly · Sonnet',
  'weekly-fable': 'Weekly · Fable',
};
/** Shortest window first, then weekly, then the model-specific weeks. */
const WINDOW_ORDER = ['5h', 'weekly', 'weekly-fable', 'weekly-opus', 'weekly-sonnet'];
const PROVIDER_ORDER = ['claude', 'codex', 'gemini', 'grok'];
const isEpoch = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v) && v > 0;
const isPct = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v) && v >= 0 && v <= 100;

export function toneFor(pct: number): UsageTone {
  return pct >= 90 ? 'full' : pct >= 70 ? 'warn' : 'ok';
}

const time = (d: Date) => d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });

/** "Resets in 2 h 14 m" within a day, else the day and time ("Resets Mon 4:00 PM"); "Resets now" once it is due. */
export function resetsText(resetsAt: number, now: Date): string {
  const s = Math.round(resetsAt - now.getTime() / 1000);
  if (s <= 60) return 'Resets now';
  if (s < 24 * 3600) {
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    return `Resets in ${h ? `${h} h ` : ''}${m} m`;
  }
  const d = new Date(resetsAt * 1000);
  return `Resets ${d.toLocaleDateString([], { weekday: 'short' })} ${time(d)}`;
}

/** More than one account for this provider: each gets its own card, and a per-account action (none in this build) has
 *  a choice to offer. One account: one card, nothing to choose. */
export function hasAccountChoice(p: Pick<UsageProviderView, 'accounts'>): boolean {
  return p.accounts.length > 1;
}

const order = (list: readonly string[], k: string) => { const i = list.indexOf(k); return i < 0 ? list.length : i; };

export function usageOverview(rows: unknown, now: Date = new Date(), agentsAccountIn?: string): UsageOverview {
  const all = Array.isArray(rows) ? (rows as UsageRow[]).filter((r) => r && typeof r.provider === 'string') : [];
  const ids = [...new Set(all.map((r) => r.provider))].sort((a, b) => order(PROVIDER_ORDER, a) - order(PROVIDER_ORDER, b) || a.localeCompare(b));
  const out: UsageOverview = { providers: [], silent: [] };
  for (const id of ids) {
    const name = NAMES[id] || id;
    const mine = all.filter((r) => r.provider === id);
    const read = mine.filter((r) => isPct(r.used_pct));
    if (!read.length) {
      out.silent.push({ id, name, reason: mine.find((r) => r.reason)?.reason || 'no usage reported', pending: mine.some((r) => r.pending === true) });
      continue;
    }
    const agentsAccount = agentsAccountIn ?? agentsAccountOf(read);
    const byAccount = new Map<string, UsageRow[]>();
    for (const r of read) { const k = r.account || agentsAccount; byAccount.set(k, [...(byAccount.get(k) ?? []), r]); }
    const accountIds = [...byAccount.keys()].sort((a, b) => (a === agentsAccount ? -1 : b === agentsAccount ? 1 : a.localeCompare(b)));
    const accounts: UsageAccountView[] = accountIds.map((acct) => ({
      id: acct,
      label: acct === AGENTS_ACCOUNT ? 'Main login' : acct,
      agents: acct === agentsAccount,
      windows: byAccount.get(acct)!
        .slice().sort((a, b) => order(WINDOW_ORDER, a.window ?? '') - order(WINDOW_ORDER, b.window ?? ''))
        .map((r, i) => {
          const pct = Math.round(r.used_pct as number);
          return {
            key: `${acct}|${r.window ?? ''}|${i}`,
            label: (r.window && WINDOW_LABEL[r.window]) || r.window || 'Limit',
            pct,
            tone: toneFor(pct),
            resets: isEpoch(r.resets_at) ? resetsText(r.resets_at, now) : '',
            stale: r.stale ? (isEpoch(r.as_of) ? `as of ${time(new Date(r.as_of * 1000))}` : 'as of an unknown time') : null,
          };
        }),
    }));
    const u = usageFor(read, id, now, agentsAccount);
    out.providers.push({
      id, name, accounts,
      ring: u ? { pct: u.pct, tone: toneFor(u.pct), stale: u.stale } : null,
      summary: u ? u.text : '',
    });
  }
  return out;
}
