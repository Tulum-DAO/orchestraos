import { useQuery } from '@tanstack/react-query';
import { fetchCapabilities } from '../lib/api';

/** GET /api/capabilities, cached for a minute. `data` is undefined while loading or on error. */
export function useCapabilities() {
  return useQuery({ queryKey: ['capabilities'], queryFn: fetchCapabilities, staleTime: 60_000, retry: false });
}
