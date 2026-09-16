import { useQuery } from '@tanstack/react-query';
import { fetchExperiments } from '../lib/api';
export function useExperiments() {
  return useQuery({ queryKey: ['experiments'], queryFn: fetchExperiments, refetchInterval: 30_000 });
}
