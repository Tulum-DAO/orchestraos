import { useQuery } from '@tanstack/react-query';
import { fetchVoiceAgents } from '../lib/api';

export function useVoiceAgents() {
  return useQuery({ queryKey: ['voiceAgents'], queryFn: fetchVoiceAgents, refetchInterval: 30_000 });
}

