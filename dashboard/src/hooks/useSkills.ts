import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { fetchSkills, assignSkill, unassignSkill } from '../lib/api';

export function useSkills() {
  return useQuery({ queryKey: ['skills'], queryFn: fetchSkills });
}

export function useAssignSkill() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ skillId, agentId }: { skillId: string; agentId: string }) =>
      assignSkill(skillId, agentId),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['skills'] }); },
  });
}

export function useUnassignSkill() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ skillId, agentId }: { skillId: string; agentId: string }) =>
      unassignSkill(skillId, agentId),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['skills'] }); },
  });
}
