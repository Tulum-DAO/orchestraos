/** Settings › (Server) Usage, as providers (lib/usageOverview.ts): a card per provider with its mark and the rail's ring,
 *  then each login with a bar per window and when it resets. Read through GET /api/usage (admin-only: anyone else gets
 *  its 404 and this renders nothing). Every card and line carries data-setting: settings.css hides a group without one.
 *  A provider with no reading yet is one muted line with the reason, never a bar at 0%.
 *
 *  With more than one account for a provider (hasAccountChoice), each account is its own card, and a card can carry a
 *  per-account action under its bars (the `accountAction` prop). This build passes none: the view is read-only and
 *  nothing here changes which login the agents use. */
import { useEffect, useState, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchUsage, USAGE_QUERY_KEY, UsageHidden } from '../../../lib/usageLine';
import {
  hasAccountChoice, usageOverview, type UsageAccountView, type UsageProviderView, type UsageTone, type UsageWindowView,
} from '../../../lib/usageOverview';
import { ringDash } from '../../../lib/modelPickerPanel';
import { ProviderMark } from '../../kit/ModelPicker';

const BAR: Record<UsageTone, string> = { ok: 'bg-emerald-500', warn: 'bg-amber-500', full: 'bg-red-500' };
const STROKE: Record<UsageTone, string> = { ok: 'stroke-emerald-500', warn: 'stroke-amber-500', full: 'stroke-red-500' };
const PCT: Record<UsageTone, string> = { ok: 'text-tone-200', warn: 'text-amber-400', full: 'text-red-400' };

/** The page's theme as drawn (the `dark` class on <html>), followed live: the brand marks have a light and a dark cut. */
function useDark(): boolean {
  const read = () => typeof document !== 'undefined' && document.documentElement.classList.contains('dark');
  const [dark, setDark] = useState(read);
  useEffect(() => {
    const mo = new MutationObserver(() => setDark(read()));
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
    return () => mo.disconnect();
  }, []);
  return dark;
}

function MarkWithRing({ p, dark }: { p: UsageProviderView; dark: boolean }) {
  const size = 28;
  const box = size + 16;
  const r = box / 2 - 2.5;
  const ring = p.ring ? ringDash(p.ring.pct, r) : null;
  return (
    <span className="relative inline-flex items-center justify-center shrink-0" style={{ width: box, height: box }}>
      {ring && (
        <svg width={box} height={box} viewBox={`0 0 ${box} ${box}`} className="absolute inset-0 -rotate-90" aria-hidden data-usage-ring={p.ring!.pct}>
          <circle cx={box / 2} cy={box / 2} r={r} fill="none" strokeWidth={3} className="stroke-tone-800" />
          <circle cx={box / 2} cy={box / 2} r={r} fill="none" strokeWidth={3} strokeLinecap="round"
                  strokeDasharray={`${ring.dash} ${ring.gap}`} className={p.ring!.stale ? 'stroke-tone-600' : STROKE[p.ring!.tone]} />
        </svg>
      )}
      <ProviderMark providerId={p.id} label={p.name} dark={dark} size={size} />
    </span>
  );
}

function WindowBar({ w }: { w: UsageWindowView }) {
  return (
    <div className="py-1.5" data-usage-window={w.label} data-tone={w.tone} data-stale={w.stale ? true : undefined}>
      <div className="flex items-baseline justify-between gap-3 text-px13">
        <span className="text-tone-200">{w.label}</span>
        <span className={`tabular-nums font-medium ${w.stale ? 'text-tone-500' : PCT[w.tone]}`}>{w.pct}% used</span>
      </div>
      <div className="mt-1.5 h-1.5 w-full rounded-full bg-tone-800 overflow-hidden" role="progressbar"
           aria-valuemin={0} aria-valuemax={100} aria-valuenow={w.pct} aria-label={`${w.label}: ${w.pct}% used`}>
        <div className={`h-full rounded-full ${w.stale ? 'bg-tone-600' : BAR[w.tone]}`} style={{ width: `${Math.max(w.pct, w.pct > 0 ? 2 : 0)}%` }} />
      </div>
      {(w.resets || w.stale) && (
        <div className="mt-1 text-px11 text-tone-500">{[w.resets, w.stale].filter(Boolean).join(' · ')}</div>
      )}
    </div>
  );
}

/** An action shown under one account's bars. Asked only where hasAccountChoice(p) (more than one account); null or
 *  undefined shows nothing. */
export type UsageAccountAction = (p: UsageProviderView, a: UsageAccountView) => ReactNode;

/** One account: its name and the agents badge (only when there are several), a bar per window, then its action. */
function AccountCard({ a, many, action }: { a: UsageAccountView; many: boolean; action?: ReactNode }) {
  return (
    <div className={many ? 'rounded-lg bg-tone-900/60 border border-tone-800/70 px-3 py-2' : ''} data-usage-account={a.id}>
      {many && (
        <div className="flex items-center justify-between gap-2 mb-0.5">
          <span className="min-w-0 block text-px13 font-medium text-tone-100 truncate">{a.label}</span>
          {a.agents && <span className="text-px10 uppercase tracking-wide px-1.5 py-[1px] rounded-full border border-emerald-500/40 text-emerald-400" data-agents-badge>Agents use this</span>}
        </div>
      )}
      {a.windows.map((w) => <WindowBar key={w.key} w={w} />)}
      {!a.windows.length && <div className="py-1.5 text-px12 text-tone-500">No reading yet.</div>}
      {action != null && action !== false && <div className="mt-1.5" data-usage-account-action={a.id}>{action}</div>}
    </div>
  );
}

function ProviderCard({ p, dark, accountAction }: { p: UsageProviderView; dark: boolean; accountAction?: UsageAccountAction }) {
  const many = hasAccountChoice(p);
  return (
    <section className="rounded-xl border border-tone-800 bg-tone-900/40 px-4 py-3" data-setting={`usage:${p.id}`} data-usage-provider={p.id}>
      <header className="flex items-center gap-3">
        <MarkWithRing p={p} dark={dark} />
        <div className="min-w-0">
          <div className="text-px15 font-semibold text-tone-100">{p.name}</div>
          {p.summary && <div className="text-px12 text-tone-400">{p.summary}{many ? ' on the agents’ login' : ''}</div>}
        </div>
      </header>
      <div className={many ? 'mt-3 grid gap-3 sm:grid-cols-2' : 'mt-2'}>
        {p.accounts.map((a) => (
          <AccountCard key={a.id} a={a} many={many} action={many && accountAction ? accountAction(p, a) : null} />
        ))}
      </div>
    </section>
  );
}

export function UsageOverviewPanel({ accountAction }: { accountAction?: UsageAccountAction } = {}) {
  const dark = useDark();
  const { data, error, isLoading } = useQuery({
    queryKey: USAGE_QUERY_KEY, queryFn: fetchUsage, staleTime: 55_000, refetchInterval: 60_000, retry: false,
  });
  // A minute's tick keeps "Resets in 2 h 14 m" true between reads.
  const [now, setNow] = useState(() => new Date());
  useEffect(() => { const t = window.setInterval(() => setNow(new Date()), 60_000); return () => window.clearInterval(t); }, []);
  if (error instanceof UsageHidden) return null;
  const view = usageOverview(data, now);
  return (
    <section className="settings-group" data-usage-overview>
      {isLoading && <p className="panel-note" data-setting="usage">Reading plan usage…</p>}
      {error && !isLoading && <p className="panel-note" data-setting="usage" data-plan-usage-error>Plan usage is unavailable right now.</p>}
      {!isLoading && !error && !view.providers.length && !view.silent.length && <p className="panel-note" data-setting="usage">No provider reports plan usage.</p>}
      <div className="grid gap-3">
        {view.providers.map((p) => <ProviderCard key={p.id} p={p} dark={dark} accountAction={accountAction} />)}
      </div>
      {view.silent.length > 0 && (
        <ul className="mt-3 grid gap-1 text-px12 text-tone-500" data-usage-silent>
          {view.silent.map((x) => (
            <li key={x.id} data-setting={`usage:silent:${x.id}`} data-usage-silent-provider={x.id}>
              <span className="text-tone-300">{x.name}</span>: No reading yet · {x.reason}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
