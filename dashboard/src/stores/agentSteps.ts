// The live step per agent (lib/liveStep.ts), from the chat stream that is open for it (TranscriptChatView), for the
// line under that chat. In memory only; a page with no open stream reads the agent list's `step` instead.
import { create } from 'zustand';
import type { AgentStep } from '../lib/liveStep';

interface AgentStepsState {
  byAgent: Record<string, AgentStep | null>;
  set: (agentId: string, step: AgentStep | null) => void;
  /** The stream closed: what it said is no longer live. */
  drop: (agentId: string) => void;
}

export const useAgentSteps = create<AgentStepsState>((set) => ({
  byAgent: {},
  set: (agentId, step) => set((s) => ({ byAgent: { ...s.byAgent, [agentId]: step } })),
  drop: (agentId) => set((s) => { const next = { ...s.byAgent }; delete next[agentId]; return { byAgent: next }; }),
}));
