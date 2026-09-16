/**
 * ClientsPeopleTabs — title-as-switcher between the Clients lens and the People
 * lens (CRM P1, spec 2026-08-11-crm-pipeline-split §4.3). Ported from a sibling
 * product's LensTabs grammar: the tabs ARE the title (no separate <h1> row). Two real
 * routes (/clients, /people); a small shared scope (?tenant, ?q) is preserved
 * across the switch, per-lens filters are dropped.
 */
import { Link, useSearchParams } from 'react-router-dom';
import { clsx } from 'clsx';

const SHARED_SCOPE = ['tenant', 'q'] as const;

export function ClientsPeopleTabs({ active, count }: { active: 'clients' | 'people'; count?: number }) {
  const [sp] = useSearchParams();
  // Carry only the shared scope across the lens switch; per-lens filters (client
  // status/pm, people relationship) intentionally reset — they don't overlap.
  const shared = new URLSearchParams();
  for (const k of SHARED_SCOPE) { const v = sp.get(k); if (v) shared.set(k, v); }
  const qs = shared.toString() ? `?${shared.toString()}` : '';

  const tab = (to: 'clients' | 'people', label: string) => (
    <Link
      to={`/${to}${qs}`}
      role="tab"
      aria-selected={active === to}
      className={clsx(
        'text-2xl font-bold tracking-tight transition-colors',
        active === to ? 'text-white' : 'text-neutral-500 hover:text-neutral-300'
      )}
    >
      {label}
    </Link>
  );

  return (
    <nav role="tablist" aria-label="View mode" className="flex items-baseline gap-5">
      {tab('clients', 'Clients')}
      {tab('people', 'People')}
      {count != null && (
        <span className="self-center text-sm text-neutral-500 bg-neutral-800 rounded-full px-2.5 py-0.5">
          {count}
        </span>
      )}
    </nav>
  );
}
