/**
 * UsageLine: plan usage under the sidebar's user card, one line per provider ("Claude 10% 5 h · 23%
 * weekly", then "Codex 41% 5 h"), from GET /api/usage. Formatting and the never-guess rules live in lib/usageLine.ts.
 * A caller the API will not show usage to (404) sees nothing at all; a failed fetch says so in one
 * word rather than looking like "no usage".
 */
import { useQuery } from '@tanstack/react-query';
import clsx from 'clsx';
import { usageLines } from '../../lib/usageLine';

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
  if (error) return <p className="mx-2 px-2 text-[11px] text-neutral-500 dark:text-neutral-600" role="status">Usage unavailable</p>;
  const lines = usageLines(data);
  if (lines.length === 0) return null;
  return (
    <div className="mx-2 px-2 flex flex-col gap-0.5 text-[11px] text-muted-foreground" aria-label="Plan usage">
      {/* One provider per line, never wrapped: a long line is cut with an ellipsis (its full text is on hover). */}
      {lines.map((line) => (
        <p key={line.provider} className="whitespace-nowrap overflow-hidden text-ellipsis"
           title={line.parts.map((p) => p.title).join('\n')}>
          {line.parts.map((p, i) => (
            <span key={p.key} title={p.title}
                  className={clsx('tabular-nums', p.missing && 'text-neutral-500 dark:text-neutral-600', p.stale && 'opacity-50',
                                  p.high && !p.stale && 'text-amber-700 dark:text-amber-300')}>
              {i > 0 && <span className="text-neutral-300 dark:text-neutral-700 mx-1.5" aria-hidden>·</span>}
              {p.text}
            </span>
          ))}
        </p>
      ))}
    </div>
  );
}
