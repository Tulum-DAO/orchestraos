import { create } from 'zustand';

interface AgentSettings {
  assistantName: string;
  accentVoice: string;
  transcriptMode: 'all-at-once' | 'as-spoken';
  micMode: 'hold' | 'toggle';
}

interface AgentSettingsStore extends AgentSettings {
  setAssistantName: (name: string) => void;
  setAccentVoice: (color: string) => void;
  setTranscriptMode: (mode: 'all-at-once' | 'as-spoken') => void;
  setMicMode: (mode: 'hold' | 'toggle') => void;
  loadFromStorage: () => void;
}

const STORAGE_KEY = 'orchestra.agentSettings';

const defaultSettings: AgentSettings = {
  assistantName: 'Arturo',
  accentVoice: '#000000',
  transcriptMode: 'all-at-once',
  micMode: 'hold',
};

export const useAgentSettings = create<AgentSettingsStore>((set) => {
  // Load from localStorage if available
  const stored = (() => {
    try {
      const item = localStorage.getItem(STORAGE_KEY);
      return item ? JSON.parse(item) : null;
    } catch {
      return null;
    }
  })();

  const initialState: AgentSettings = { ...defaultSettings, ...stored };

  return {
    ...initialState,
    setAssistantName: (name) => set((state) => {
      const updated = { ...state, assistantName: name };
      localStorage.setItem(STORAGE_KEY, JSON.stringify(updated));
      return updated;
    }),
    setAccentVoice: (color) => set((state) => {
      const updated = { ...state, accentVoice: color };
      document.documentElement.style.setProperty('--accent-voice', color);
      localStorage.setItem(STORAGE_KEY, JSON.stringify(updated));
      return updated;
    }),
    setTranscriptMode: (mode) => set((state) => {
      const updated = { ...state, transcriptMode: mode };
      localStorage.setItem(STORAGE_KEY, JSON.stringify(updated));
      return updated;
    }),
    setMicMode: (mode) => set((state) => {
      const updated = { ...state, micMode: mode };
      localStorage.setItem(STORAGE_KEY, JSON.stringify(updated));
      return updated;
    }),
    loadFromStorage: () => set(() => {
      try {
        const item = localStorage.getItem(STORAGE_KEY);
        if (item) {
          const settings = JSON.parse(item);
          if (settings.accentVoice) {
            document.documentElement.style.setProperty('--accent-voice', settings.accentVoice);
          }
          return settings;
        }
      } catch {
        // ignore
      }
      return {};
    }),
  };
});
