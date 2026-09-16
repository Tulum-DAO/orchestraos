import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { fetchVoiceAgents, syncVoicePrompts } from '../lib/api';

export function useVoiceAgents() {
  return useQuery({ queryKey: ['voiceAgents'], queryFn: fetchVoiceAgents, refetchInterval: 30_000 });
}

export function useSyncPrompts() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: syncVoicePrompts, onSuccess: () => qc.invalidateQueries({ queryKey: ['voiceAgents'] }) });
}
