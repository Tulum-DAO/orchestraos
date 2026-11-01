// The newest compaction notice per agent (lib/compaction.ts), as its open chat last showed it (TranscriptChatView),
// for "Compact now" in the slideout: it says how the compaction it asked for went without a request of its own.
import { create } from 'zustand';
import type { CompactionItem } from '../lib/compaction';

interface CompactionNoticesState {
  byAgent: Record<string, CompactionItem | null>;
  set: (agentId: string, notice: CompactionItem | null) => void;
}

export const useCompactionNotices = create<CompactionNoticesState>((set) => ({
  byAgent: {},
  set: (agentId, notice) => set((s) => (s.byAgent[agentId] === notice ? s : { byAgent: { ...s.byAgent, [agentId]: notice } })),
}));
