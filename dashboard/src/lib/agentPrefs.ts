/**
 * agentPrefs.ts — how you see each agent: Modern, Bubbles or Dev, saved for you on the server per agent
 * (GET/PUT /api/agents/:id/prefs), so the choice follows you across devices. One small store keyed by agent, so the
 * page, its top bar and the agent panel agree; a choice shows at once and is saved behind it. The last answer is
 * also kept in the browser, so a page opens straight into the right view instead of flashing the other one.
 */
import { create } from 'zustand';

export type ChatStyle = 'modern' | 'bubbles' | 'dev';
export const ARTURO_ID = 'arturo';

export interface StyleOption { id: ChatStyle; label: string; available: boolean; reason?: string }

const LABELS: Record<ChatStyle, string> = { modern: 'Modern', bubbles: 'Bubbles', dev: 'Dev' };
// Every page draws Modern and Bubbles through the kit's thread view; only Dev depends on the agent having a terminal.

/** The three styles, and which this agent can show today (with why not). */
export function styleOptions(agentId: string): StyleOption[] {
  const arturo = agentId === ARTURO_ID;
  return (['modern', 'bubbles', 'dev'] as const).map((id) => {
    const why = arturo
      ? id === 'dev' ? 'Arturo has no terminal' : undefined
      : undefined;
    return { id, label: LABELS[id], available: !why, ...(why ? { reason: why } : {}) };
  });
}

export function defaultStyle(agentId: string): ChatStyle {
  return agentId === ARTURO_ID ? 'modern' : 'bubbles';
}

/** The style this agent is drawn in: the saved one when it can show it today, else its default. */
export function effectiveStyle(agentId: string, saved: ChatStyle | null | undefined): ChatStyle {
  const ok = styleOptions(agentId).find((o) => o.id === saved)?.available;
  return ok && saved ? saved : defaultStyle(agentId);
}

export function styleLabel(style: ChatStyle): string {
  return LABELS[style];
}

const CACHE_KEY = 'orchestra.agentPrefs';

function readCache(): Record<string, ChatStyle> {
  try {
    const d = JSON.parse(localStorage.getItem(CACHE_KEY) || '{}');
    return d && typeof d === 'object' ? d : {};
  } catch { return {}; }
}

function writeCache(all: Record<string, ChatStyle>) {
  try { localStorage.setItem(CACHE_KEY, JSON.stringify(all)); } catch { /* storage blocked: the server copy still holds it */ }
}

interface PrefsStore {
  styles: Record<string, ChatStyle>;
  errors: Record<string, string | undefined>;
  set: (agentId: string, style: ChatStyle) => void;
  setError: (agentId: string, error: string | undefined) => void;
}

export const useAgentPrefsStore = create<PrefsStore>((set) => ({
  styles: readCache(),
  errors: {},
  set: (agentId, style) => set((s) => {
    const styles = { ...s.styles, [agentId]: style };
    writeCache(styles);
    return { styles };
  }),
  setError: (agentId, error) => set((s) => ({ errors: { ...s.errors, [agentId]: error } })),
}));

/** Read this agent's saved style from the server into the store. */
export async function loadPrefs(agentId: string, fetchFn: typeof fetch = fetch): Promise<void> {
  try {
    const r = await fetchFn(`/api/agents/${encodeURIComponent(agentId)}/prefs`);
    const d = await r.json().catch(() => null);
    if (r.ok && d?.ok && typeof d.style === 'string') useAgentPrefsStore.getState().set(agentId, d.style as ChatStyle);
  } catch { /* offline: the last answer this browser saw stands */ }
}

/** Choose a style: it shows at once; if the server refuses, the old one comes back with its reason. */
export async function chooseStyle(agentId: string, style: ChatStyle, fetchFn: typeof fetch = fetch): Promise<boolean> {
  const st = useAgentPrefsStore.getState();
  const before = st.styles[agentId];
  st.set(agentId, style);
  st.setError(agentId, undefined);
  try {
    const r = await fetchFn(`/api/agents/${encodeURIComponent(agentId)}/prefs`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ style }),
    });
    const d = await r.json().catch(() => ({}));
    if (r.ok && d.ok) return true;
    throw new Error(d.error || `HTTP ${r.status}`);
  } catch (e) {
    if (before) st.set(agentId, before);
    else useAgentPrefsStore.setState((s) => { const styles = { ...s.styles }; delete styles[agentId]; writeCache(styles); return { styles }; });
    st.setError(agentId, `Couldn't save the chat style: ${e instanceof Error ? e.message : 'unknown error'}`);
    return false;
  }
}

/** This agent's style as drawn now. */
export function useChatStyle(agentId: string): ChatStyle {
  const saved = useAgentPrefsStore((s) => s.styles[agentId]);
  return effectiveStyle(agentId, saved);
}
