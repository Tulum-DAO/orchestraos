import { useQuery } from '@tanstack/react-query';
import { fetchAgents } from '../lib/api';
export function useAgents() {
  return useQuery({
    queryKey: ['agents'],
    queryFn: fetchAgents,
    refetchInterval: 10_000, // Auto-refresh every 10s — heartbeat-backed, no SSH delay
    staleTime: 5_000, // Consider data stale after 5s
  });
}
