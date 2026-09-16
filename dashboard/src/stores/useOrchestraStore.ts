import { create } from 'zustand';

interface ActivityEvent { timestamp: string; agent: string; event: string; detail: string; task_id?: string; }

interface ActiveCall { pmId: string; agentId: string; }

interface OrchestraState {
  activity: ActivityEvent[];
  addActivity: (event: ActivityEvent) => void;
  activeCall: ActiveCall | null;
  startCall: (pmId: string, agentId: string) => void;
  endCall: () => void;
  // Legacy compat
  activeCallAgent: string | null;
  setActiveCallAgent: (agent: string | null) => void;
}

export const useOrchestraStore = create<OrchestraState>((set) => ({
  activity: [],
  addActivity: (event) => set((state) => ({ activity: [event, ...state.activity.slice(0, 499)] })),
  activeCall: null,
  startCall: (pmId, agentId) => set({ activeCall: { pmId, agentId }, activeCallAgent: pmId }),
  endCall: () => set({ activeCall: null, activeCallAgent: null }),
  activeCallAgent: null,
  setActiveCallAgent: (agent) => set({ activeCallAgent: agent }),
}));
