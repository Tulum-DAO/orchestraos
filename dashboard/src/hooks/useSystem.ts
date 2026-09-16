import { useQuery } from '@tanstack/react-query';
import { fetchSystem } from '../lib/api';
export function useSystem() { return useQuery({ queryKey: ['system'], queryFn: fetchSystem, refetchInterval: 30_000 }); }
