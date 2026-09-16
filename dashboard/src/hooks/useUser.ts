import { useQuery } from '@tanstack/react-query';
import { fetchMe } from '../lib/api';

export function useUser() {
  return useQuery({
    queryKey: ['me'],
    queryFn: fetchMe,
    staleTime: 60_000,
    refetchInterval: 60_000,
  });
}

export function canSeeAgent(allowed: string | string[], agentId: string): boolean {
  if (allowed === '*') return true;
  if (Array.isArray(allowed)) return allowed.includes(agentId);
  // If allowed is a non-* string (client scope), server already filtered — show all
  if (typeof allowed === 'string' && allowed.length > 0) return true;
  return false;
}
