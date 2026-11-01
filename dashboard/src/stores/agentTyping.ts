// Whether an agent is writing its reply right now (its chat stream's live text, no tool running), per agent, from the
// chat view that has its stream open (TranscriptChatView), for its working row's "Typing…" and the melt into dots.
import { create } from 'zustand';

interface AgentTypingState {
  byAgent: Record<string, boolean | undefined>;
  set: (agentId: string, typing: boolean) => void;
}

export const useAgentTyping = create<AgentTypingState>((set) => ({
  byAgent: {},
  set: (agentId, typing) => set((s) => (!!s.byAgent[agentId] === typing ? s : { byAgent: { ...s.byAgent, [agentId]: typing } })),
}));
