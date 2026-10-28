// Pending bubbles per agent, shared by the composer that adds them (ChatInput) and the
// conversation that draws and retires them (TranscriptChatView). In memory only: a reload starts
// from the transcript, which is the truth.
import { create } from 'zustand';
import { afterIdle, visiblePending, type PendingSend } from '../lib/pendingSends';
import type { Row } from '../lib/heldEchoReconcile';

interface PendingSendsState {
  byAgent: Record<string, PendingSend[]>;
  /** The operator rows each open conversation last showed, for the send-time snapshot. */
  rows: Record<string, Row[]>;
  add: (p: PendingSend) => void;
  /** The conversation's rows changed: remember them, and retire the bubbles they confirm. */
  setRows: (agentId: string, rows: Row[]) => void;
  /** The agent's live state changed: the idle backstop. */
  onAgentState: (agentId: string, idle: boolean) => void;
}

let seq = 0;
export const newPendingId = () => `pending-${Date.now()}-${++seq}`;

export const usePendingSends = create<PendingSendsState>((set) => ({
  byAgent: {},
  rows: {},
  // A poll can bring the row in while the POST is still in flight; then the bubble is never added.
  add: (p) => set((s) => {
    const rows = s.rows[p.agentId];
    if (rows && visiblePending([p], rows).length === 0) return {};
    return { byAgent: { ...s.byAgent, [p.agentId]: [...(s.byAgent[p.agentId] ?? []), p] } };
  }),
  setRows: (agentId, rows) => set((s) => {
    const cur = s.byAgent[agentId] ?? [];
    const keep = cur.length ? visiblePending(cur, rows) : cur;
    return {
      rows: { ...s.rows, [agentId]: rows },
      ...(keep.length !== cur.length ? { byAgent: { ...s.byAgent, [agentId]: keep } } : {}),
    };
  }),
  onAgentState: (agentId, idle) => set((s) => {
    const cur = s.byAgent[agentId];
    if (!cur?.length) return {};
    return { byAgent: { ...s.byAgent, [agentId]: afterIdle(cur, idle, Date.now()) } };
  }),
}));

/** The ids of the operator rows on screen right now: rows that can never confirm a send made now. */
export function rowIdsAtSend(agentId: string): Set<string> | undefined {
  const rows = usePendingSends.getState().rows[agentId];
  return rows ? new Set(rows.map((r) => r.id)) : undefined;
}
