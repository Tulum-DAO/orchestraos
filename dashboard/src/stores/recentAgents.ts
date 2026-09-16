// Recent-agent recency + locked swipe deck (spec: docs/superpowers/specs/2026-07-17-devmode-multi-agent-ux.md)
// - recency: ordered by the operator's interaction (sends + dev-mode entry), persisted
// - deck: SNAPSHOT of recency taken when entering dev mode; swipes walk it
//   without reordering; re-locks when a new agent is entered
// - focusRequest: lets chips/swipes open a different agent's modal (each
//   AgentCard owns its own `focused` state, so switching is coordinated here)

import { create } from 'zustand';

const LS_KEY = 'orchestra.recentAgents.v1';

function loadRecency(): string[] {
  try {
    const raw = localStorage.getItem(LS_KEY);
    const arr = raw ? JSON.parse(raw) : [];
    return Array.isArray(arr) ? arr.filter((x) => typeof x === 'string') : [];
  } catch {
    return [];
  }
}

export interface FocusRequest {
  id: string;
  viaSwipe: boolean; // swipe navigation must NOT re-lock the deck (spec)
}

interface RecentAgentsState {
  recency: string[]; // most recent first
  deck: string[]; // locked snapshot: [current, prev, prev2, ...]
  deckIndex: number; // where we are in the deck (0 = head)
  focusRequest: FocusRequest | null; // agent another card should open (dev mode)
  bump: (id: string) => void;
  lockDeck: (currentId: string) => void;
  swipeDeeper: () => string | null; // right-edge swipe → returns agent to open
  swipeBack: () => string | null; // left-edge swipe → returns agent to open
  requestFocus: (id: string, viaSwipe?: boolean) => void;
  clearFocusRequest: () => void;
}

export const useRecentAgents = create<RecentAgentsState>((set, get) => ({
  recency: loadRecency(),
  deck: [],
  deckIndex: 0,
  focusRequest: null,

  bump: (id) =>
    set((s) => {
      const next = [id, ...s.recency.filter((x) => x !== id)].slice(0, 28); // chips show 20; headroom for filtering current
      try {
        localStorage.setItem(LS_KEY, JSON.stringify(next));
      } catch {}
      return { recency: next };
    }),

  // Called on dev-mode ENTRY (not on swipe navigation): snapshot the deck with
  // the entered agent at head, previous most-recent agents behind it.
  lockDeck: (currentId) => {
    const { recency } = get();
    const rest = recency.filter((x) => x !== currentId);
    set({ deck: [currentId, ...rest].slice(0, 8), deckIndex: 0 });
  },

  swipeDeeper: () => {
    const { deck, deckIndex } = get();
    if (deckIndex + 1 >= deck.length) return null;
    const idx = deckIndex + 1;
    set({ deckIndex: idx });
    return deck[idx];
  },

  swipeBack: () => {
    const { deck, deckIndex } = get();
    if (deckIndex <= 0) return null;
    const idx = deckIndex - 1;
    set({ deckIndex: idx });
    return deck[idx];
  },

  requestFocus: (id, viaSwipe = false) => set({ focusRequest: { id, viaSwipe } }),
  clearFocusRequest: () => set({ focusRequest: null }),
}));
