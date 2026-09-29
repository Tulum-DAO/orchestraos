/**
 * arturoBrain.ts: which brain Arturo's NEXT turn should use (DEC-1790669162399904 spec v4 §1.5).
 *
 * Deliberately separate from stores/modelSelection.ts, which the Agent page shares. That older
 * picker persisted choices that never reached a brain. Honouring them now would silently
 * reroute existing users, so this reads ONLY its own versioned key, and `null` means the
 * install's default brain (nothing is sent).
 */
import type { ArturoReply } from './arturo';

export const ARTURO_BRAIN_KEY = 'orchestra.arturoBrain.v1';

export interface ArturoBrainChoice { provider: string; model: string; label: string }
type Storage = Pick<globalThis.Storage, 'getItem' | 'setItem' | 'removeItem'>;

export function loadArturoBrain(storage: Storage): ArturoBrainChoice | null {
  try {
    const raw = storage.getItem(ARTURO_BRAIN_KEY);
    if (!raw) return null;
    const v = JSON.parse(raw);
    if (!v || v.v !== 1 || typeof v.provider !== 'string' || !v.provider || typeof v.model !== 'string') return null;
    return { provider: v.provider, model: v.model, label: typeof v.label === 'string' && v.label ? v.label : v.provider };
  } catch {
    return null;
  }
}

export function saveArturoBrain(storage: Storage, choice: ArturoBrainChoice | null): void {
  try {
    if (!choice) storage.removeItem(ARTURO_BRAIN_KEY);
    else storage.setItem(ARTURO_BRAIN_KEY, JSON.stringify({ v: 1, ...choice }));
  } catch {
    /* private mode / quota: the choice just won't persist */
  }
}

/** What goes on the wire: {provider, model}, or nothing at all for the default brain. */
export function toWireBrain(choice: ArturoBrainChoice | null): { provider: string; model: string } | undefined {
  return choice ? { provider: choice.provider, model: choice.model } : undefined;
}

/** Reopening a thread restores the brain it last used; a thread on the default brain clears it.
 *  `labels` maps `${provider}:${model}` to a display label (from the runtime catalog). */
export function brainFromThread(thread: { last_brain?: { provider: string; model: string } | null } | null,
                                labels: Record<string, string>): ArturoBrainChoice | null {
  const b = thread?.last_brain;
  if (!b || !b.provider) return null;
  return { provider: b.provider, model: b.model || '', label: labels[`${b.provider}:${b.model || ''}`] || b.model || b.provider };
}

const PRETTY: Record<string, string> = { claude: 'Claude', gemini: 'Gemini', codex: 'Codex', api: 'The API brain' };
const pretty = (p?: string) => (p && PRETTY[p]) || p || 'That brain';

export interface TurnErrorView { message: string; action: 'connect' | 'retry' | 'pick'; provider?: string }

/** Honest wording for the refusals and failures a chosen brain can produce. null = not ours,
 *  so the caller's existing error handling stays in charge. */
export function describeTurnError(r: ArturoReply): TurnErrorView | null {
  if (r.ok) return null;
  const who = pretty(r.provider);
  if (r.error === 'provider_unavailable') {
    return { action: 'connect', provider: r.provider,
             message: `${who} isn't available on this machine: ${r.reason || 'it is not set up'}. Connect it to use it, or switch brains.` };
  }
  if (r.error === 'brain_failed' || r.error === 'empty_response') {
    const what = r.error === 'empty_response' ? 'came back empty' : "didn't answer";
    const ran = (r.tools_called || []).length
      ? ` These actions already ran, so check before retrying: ${(r.tools_called || []).join(', ')}.`
      : '';
    return { action: 'retry', provider: r.provider, message: `${who} ${what} that time.${ran}` };
  }
  if (r.error === 'unknown_model' || r.error === 'bad_brain') {
    return { action: 'pick', message: "That model isn't available. Pick another one." };
  }
  return null;
}
