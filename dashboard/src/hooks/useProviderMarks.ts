/**
 * The ONE provider mark set (config/providers.json `logo_svg`, served by /api/runtimes/available):
 * the small logos every agent chip draws, the same marks the Arturo brain chip uses.
 */
import { useQuery } from '@tanstack/react-query';

export type ProviderMarks = Record<string, { svg: string; label: string }>;

export function useProviderMarks(): ProviderMarks {
  const { data } = useQuery({
    queryKey: ['provider-marks'],
    queryFn: async (): Promise<ProviderMarks> => {
      const r = await fetch('/api/runtimes/available');
      if (!r.ok) throw new Error(`/runtimes/available: ${r.status}`);
      const j = await r.json();
      const m: ProviderMarks = {};
      for (const p of (j?.providers ?? []) as { id: string; label?: string; logo_svg?: string }[]) {
        if (p.logo_svg) m[p.id] = { svg: p.logo_svg, label: p.label || p.id };
      }
      return m;
    },
    staleTime: 10 * 60_000,
  });
  return data ?? {};
}
