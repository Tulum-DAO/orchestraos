/** The supervision state for Settings › Permissions, Settings › Safety gate and the agent drawer's Supervision page:
 *  GET /api/supervision and the grants (all of them, or those that reach one agent), loaded on mount and again after
 *  every write. A write's own answer (PUT default / agent / project returns the whole body) is shown at once. */
import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchGrants, fetchSupervision, type Grant, type Result, type Supervision } from '../../lib/supervision';

export function useSupervision(opts: { agent?: string; grants?: boolean } = {}) {
  const { agent, grants: wantGrants = true } = opts;
  const [sup, setSup] = useState<Supervision | null>(null);
  const [grants, setGrants] = useState<Grant[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  const load = useCallback(() => Promise.all([fetchSupervision(), wantGrants ? fetchGrants(agent ? { agent } : {}) : Promise.resolve(null)]), [agent, wantGrants]);
  const apply = useCallback(([s, g]: Awaited<ReturnType<typeof load>>) => {
    if (!alive.current) return;
    if (s.ok) { setSup(s.value); setError(null); } else setError(s.error);
    if (g) { if (g.ok) setGrants(g.value); else setError((e) => e ?? g.error); }
  }, []);
  const reload = useCallback(() => load().then(apply), [load, apply]);

  useEffect(() => { void load().then(apply); }, [load, apply]);

  /** Run a write; the whole body it returns (when it is one) is shown at once, then everything is read again. Resolves
   *  to the error text, or null when it was saved. */
  const write = useCallback(async <T,>(w: Promise<Result<T>>): Promise<string | null> => {
    const r = await w;
    if (!alive.current) return null;
    if (r.ok && r.value && typeof r.value === 'object' && 'agents' in (r.value as object)) setSup(r.value as unknown as Supervision);
    void reload();
    return r.ok ? null : r.error;
  }, [reload]);

  return { sup, grants, error, reload, write };
}
