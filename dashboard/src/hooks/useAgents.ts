import { useQuery } from '@tanstack/react-query';
import { fetchAgents } from '../lib/api';
export function useAgents() {
  return useQuery({
    queryKey: ['agents'],
    queryFn: fetchAgents,
    // 3 s, like the Quest's /agents poll (gm msg_5f6f2483). Each poll is a cached read; a
    // detector scan only runs when a pane event has actually happened since the last one.
    refetchInterval: 3_000,
    staleTime: 2_000,
  });
}
