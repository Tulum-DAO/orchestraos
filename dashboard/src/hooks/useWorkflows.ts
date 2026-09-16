import { useQuery } from '@tanstack/react-query';
import { fetchWorkflows } from '../lib/api';
export function useWorkflows() {
  return useQuery({ queryKey: ['workflows'], queryFn: fetchWorkflows, refetchInterval: 60_000 });
}
