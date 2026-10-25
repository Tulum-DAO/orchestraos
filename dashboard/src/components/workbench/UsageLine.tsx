/**
 * UsageLine: one compact line of plan usage at the top of the agent rail ("Codex 41% 5 h · Claude
 * 68% weekly"), from GET /api/usage. Formatting and the never-guess rules live in lib/usageLine.ts.
 * A caller the API will not show usage to (404) sees nothing at all; a failed fetch says so in one
 * word rather than looking like "no usage".
 */
import { useQuery } from '@tanstack/react-query';
import clsx from 'clsx';
import { usageParts } from '../../lib/usageLine';

class Hidden extends Error {}

async function fetchUsage(): Promise<unknown> {
  const res = await fetch('/api/usage');
  if (res.status === 404) throw new Hidden();
  if (!res.ok) throw new Error(`/usage: ${res.status}`);
  return (await res.json())?.usage;
}

export default function UsageLine() {
  const { data, error } = useQuery({
    queryKey: ['usage'], queryFn: fetchUsage, refetchInterval: 60_000, staleTime: 55_000,
    retry: (n, e) => !(e instanceof Hidden) && n < 2,
  });
  if (error instanceof Hidden) return null;
  if (error) return <p className="mx-2 px-2 text-[11px] text-neutral-600" role="status">Usage unavailable</p>;
  const parts = usageParts(data);
  if (parts.length === 0) return null;
  return (
    <p className="mx-2 px-2 flex flex-wrap gap-x-2 gap-y-0.5 text-[11px] text-neutral-400" aria-label="Plan usage">
      {parts.map((p, i) => (
        <span key={p.key} title={p.title}
              className={clsx('tabular-nums', p.missing && 'text-neutral-600', p.stale && 'opacity-50',
                              p.high && !p.stale && 'text-amber-300')}>
          {i > 0 && <span className="text-neutral-700 mr-2" aria-hidden>·</span>}
          {p.text}
        </span>
      ))}
    </p>
  );
}
