/**
 * arturoBrain store: Arturo's chosen brain for the next turn (null = the install's default).
 * A thin zustand wrapper over lib/arturoBrain.ts (where the tested logic lives). Separate from
 * modelSelection.ts on purpose; see that module's header.
 */
import { create } from 'zustand';
import { loadArturoBrain, saveArturoBrain, type ArturoBrainChoice } from '../lib/arturoBrain';

const storage = (): Pick<Storage, 'getItem' | 'setItem' | 'removeItem'> | null => {
  try { return typeof localStorage !== 'undefined' ? localStorage : null; } catch { return null; }
};

interface ArturoBrainStore {
  choice: ArturoBrainChoice | null;
  choose: (choice: ArturoBrainChoice | null) => void;
}

export const useArturoBrain = create<ArturoBrainStore>((set) => ({
  choice: (() => { const s = storage(); return s ? loadArturoBrain(s) : null; })(),
  choose: (choice) => {
    const s = storage();
    if (s) saveArturoBrain(s, choice);
    set({ choice });
  },
}));
