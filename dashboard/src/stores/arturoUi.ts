// Arturo's open/closed state, shared between the top-bar button (layouts/DashboardLayout, next to
// the bell) and the pane (components/arturo/ArturoPill). They live in different parts of the tree:
// the button rides the header on every page; the pane is mounted once at the layout.
import { create } from 'zustand';

interface ArturoUi {
  open: boolean;
  setOpen: (open: boolean) => void;
  /** Exchanges in the current thread, for the button's badge. Published by the pane. */
  exchanges: number;
  setExchanges: (n: number) => void;
}

export const useArturoUi = create<ArturoUi>((set) => ({
  open: false,
  setOpen: (open) => set({ open }),
  exchanges: 0,
  setExchanges: (exchanges) => set({ exchanges }),
}));
