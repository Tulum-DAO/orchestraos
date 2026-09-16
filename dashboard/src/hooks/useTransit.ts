import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { fetchTransitStatus, goDark, returnFromTransit } from '../lib/api';

export function useTransit() {
  return useQuery({ queryKey: ['transit'], queryFn: fetchTransitStatus, refetchInterval: 10_000 });
}

export function useGoDark() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: goDark, onSuccess: () => qc.invalidateQueries({ queryKey: ['transit'] }) });
}

export function useReturn() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: returnFromTransit, onSuccess: () => qc.invalidateQueries({ queryKey: ['transit'] }) });
}
