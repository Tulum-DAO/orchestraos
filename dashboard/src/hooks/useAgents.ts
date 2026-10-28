import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchAgents } from '../lib/api';
import { overlayAgentsPayload } from '../lib/liveStatusOverlay';
import { useLiveSeats } from '../stores/liveStatus';

export function useAgents() {
  const q = useQuery({
    queryKey: ['agents'],
    queryFn: fetchAgents,
    // 3 s, like the Quest's /agents poll (gm msg_5f6f2483). Each poll is a cached read; a
    // detector scan only runs when a pane event has actually happened since the last one.
    // The poll stays the source for menus, unsent text and stopped seats; working vs idle comes
    // from the live status stream laid over it (lib/liveStatusOverlay.ts).
    refetchInterval: 3_000,
    staleTime: 2_000,
  });
  const live = useLiveSeats();
  const data = useMemo(() => (q.data ? overlayAgentsPayload(q.data, live) : q.data), [q.data, live]);
  return { ...q, data };
}
