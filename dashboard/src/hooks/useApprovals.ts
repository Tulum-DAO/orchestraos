import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { fetchApprovals, approveAction, denyAction } from '../lib/api';

export function useApprovals() {
  return useQuery({ queryKey: ['approvals'], queryFn: fetchApprovals, refetchInterval: 5000 });
}

export function useApprove() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (id: string) => approveAction(id), onSuccess: () => qc.invalidateQueries({ queryKey: ['approvals'] }) });
}

export function useDeny() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (id: string) => denyAction(id), onSuccess: () => qc.invalidateQueries({ queryKey: ['approvals'] }) });
}
