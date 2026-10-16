/**
 * "2 messages waiting: it was active a moment ago. Deliver now" above an agent's composer.
 * Wording in lib/deliverNowView.ts; the rules (which waits Deliver now may skip, which it never
 * does) live in scripts/deliver_now.py and the router.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { bannerView, type HeldState } from '../../lib/deliverNowView';

export default function DeliverNowBanner({ agentId }: { agentId?: string }) {
  const qc = useQueryClient();
  const key = ['held', agentId];
  const { data } = useQuery({
    queryKey: key,
    enabled: !!agentId,
    refetchInterval: 10_000,
    queryFn: async (): Promise<HeldState> => {
      const r = await fetch(`/api/agents/${encodeURIComponent(agentId!)}/held`);
      if (!r.ok) throw new Error(`/held: ${r.status}`);
      return r.json();
    },
  });
  const press = useMutation({
    mutationFn: async () => {
      const r = await fetch(`/api/agents/${encodeURIComponent(agentId!)}/deliver-now`, { method: 'POST' });
      if (!r.ok) throw new Error(`/deliver-now: ${r.status}`);
      return r.json();
    },
    onSuccess: (body: HeldState) => qc.setQueryData(key, body),
  });
  const v = bannerView(data);
  if (!v) return null;
  return (
    <div className="mx-3 mb-2 flex items-center gap-2 rounded border border-amber-400/30 bg-amber-400/10 px-3 py-1.5 text-xs text-amber-200"
         role="status" data-testid="deliver-now-banner">
      <span className="flex-1 min-w-0">{v.text}</span>
      {v.canDeliver && (
        <button type="button" onClick={() => press.mutate()} disabled={press.isPending}
                className="shrink-0 rounded bg-amber-400/20 px-2 py-0.5 font-medium hover:bg-amber-400/30 disabled:opacity-50">
          {press.isPending ? 'Delivering…' : 'Deliver now'}
        </button>
      )}
      {press.isError && <span className="shrink-0 text-red-300">Could not deliver</span>}
    </div>
  );
}
