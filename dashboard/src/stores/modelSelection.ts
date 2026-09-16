/**
 * modelSelection.ts — B2 model/provider selection store.
 *
 * Persists the user's provider/model choice to localStorage (manual
 * load/save, matching the existing agentSettings.ts pattern rather than
 * pulling in zustand's persist middleware). Read by:
 *   - AgentChip (label: providerId/modelId)
 *   - the video-upload capability gate (capabilities.video)
 *   - ModelSelectorSheet (to highlight the current selection on open)
 */
import { create } from 'zustand';
import type { ProviderCapabilities } from '../lib/modelSelectorFilter';

interface ModelSelectionState {
  providerId: string | null;
  modelId: string | null;
  modelLabel: string | null;
  capabilities: ProviderCapabilities | null;
}

interface ModelSelectionStore extends ModelSelectionState {
  select: (args: { providerId: string; modelId: string; modelLabel: string; capabilities: ProviderCapabilities }) => void;
  clear: () => void;
}

const STORAGE_KEY = 'orchestra.modelSelection';

function loadInitial(): ModelSelectionState {
  try {
    const item = localStorage.getItem(STORAGE_KEY);
    if (item) return JSON.parse(item) as ModelSelectionState;
  } catch {
    // localStorage unavailable (SSR/private mode) — fall through to default.
  }
  return { providerId: null, modelId: null, modelLabel: null, capabilities: null };
}

function persist(state: ModelSelectionState) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    // Best-effort only — selection still works for the current session.
  }
}

export const useModelSelection = create<ModelSelectionStore>((set) => ({
  ...loadInitial(),
  select: ({ providerId, modelId, modelLabel, capabilities }) => {
    const next: ModelSelectionState = { providerId, modelId, modelLabel, capabilities };
    persist(next);
    set(next);
  },
  clear: () => {
    const next: ModelSelectionState = { providerId: null, modelId: null, modelLabel: null, capabilities: null };
    persist(next);
    set(next);
  },
}));
