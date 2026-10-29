/**
 * arturoVoice.ts — Arturo's live voice as the server keeps it: which providers are set up, the voices it
 * offers, and the one the next call uses. The same server rows and the same validated PUT the phone's voice
 * picker uses (GET/PUT /api/arturo/ptt/*); provider keys never leave the server, only whether each is set up.
 *
 * Only GPT-Live (OpenAI) is offered here for now; other providers come with bring-your-own-key.
 */
export const OPENAI = 'openai';

export interface VoiceVendor { id: string; title: string; available: boolean; reason?: string }
export interface VoiceRow { id: string; title: string; subtitle?: string | null; recommended?: boolean; order: number; group?: string | null }
/** `scope`: whether it is the agent's own voice or the install's, which an agent with none of its own uses. */
export interface CurrentVoice { voice_id: string | null; title: string | null; scope?: 'agent' | 'install' }

const agentQ = (agent?: string) => (agent ? `&agent=${encodeURIComponent(agent)}` : '');

async function getJson<T>(url: string): Promise<T | null> {
  try {
    const r = await fetch(url);
    if (!r.ok) return null;
    return (await r.json()) as T;
  } catch { return null; }
}

/** The OpenAI provider as the server's own key check sees it; null when the server cannot be asked. */
export async function fetchOpenAiVendor(): Promise<VoiceVendor | null> {
  const d = await getJson<{ ok?: boolean; vendors?: VoiceVendor[] }>('/api/arturo/ptt/vendor');
  return d?.vendors?.find((v) => v.id === OPENAI) ?? null;
}

/** The server's GPT-Live voices, in the server's order. */
export async function fetchVoices(): Promise<VoiceRow[] | null> {
  const d = await getJson<{ ok?: boolean; voices?: VoiceRow[] }>(`/api/arturo/ptt/voices?vendor=${OPENAI}`);
  return d?.ok && Array.isArray(d.voices) ? [...d.voices].sort((a, b) => a.order - b.order) : null;
}

/** The voice the next call uses: with an agent, its own voice, else the install's. */
export async function fetchCurrentVoice(agent?: string): Promise<CurrentVoice | null> {
  const d = await getJson<{ ok?: boolean; voice_id?: string | null; title?: string | null; scope?: string }>(
    `/api/arturo/ptt/voice?vendor=${OPENAI}${agentQ(agent)}`);
  if (!d?.ok) return null;
  return { voice_id: d.voice_id ?? null, title: d.title ?? null,
           ...(d.scope === 'agent' || d.scope === 'install' ? { scope: d.scope } : {}) };
}

/** Pick a voice (an agent's own when `agent` is given); the server checks it is one of its own. Its reason
 *  comes back when it refuses. */
export async function putVoice(voiceId: string, agent?: string): Promise<{ ok: boolean; error?: string }> {
  try {
    const r = await fetch('/api/arturo/ptt/voice', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ vendor: OPENAI, voice_id: voiceId, ...(agent ? { agent } : {}) }),
    });
    const d = await r.json().catch(() => ({}));
    return r.ok && d.ok !== false ? { ok: true } : { ok: false, error: d.error || `HTTP ${r.status}` };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : 'network' };
  }
}

/** Put an agent back on the install's voice. */
export async function clearVoice(agent: string): Promise<{ ok: boolean; error?: string }> {
  try {
    const r = await fetch(`/api/arturo/ptt/voice?vendor=${OPENAI}${agentQ(agent)}`, { method: 'DELETE' });
    const d = await r.json().catch(() => ({}));
    return r.ok && d.ok !== false ? { ok: true } : { ok: false, error: d.error || `HTTP ${r.status}` };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : 'network' };
  }
}

/** Anyone may change any agent's voice for now (no per-agent ownership yet): the one place that will decide it. */
export function canChangeVoice(): boolean {
  return true;
}
export const ADMIN_ONLY_NOTE = "Only an admin can change Arturo's voice";

/** Show the pick at once, then keep it or put the old one back with the server's reason. */
export async function pickVoice(
  previous: string | null,
  next: string,
  put: (id: string) => Promise<{ ok: boolean; error?: string }>,
  show: (s: { current: string | null; error: string | null }) => void,
): Promise<boolean> {
  show({ current: next, error: null });
  const r = await put(next);
  if (r.ok) return true;
  show({ current: previous, error: `Couldn't change the voice: ${r.error || 'unknown error'}` });
  return false;
}

/** The engine's name, the way the root row and the provider row say it. */
export const GPT_LIVE = 'GPT-Live';

/** The root row's value: "GPT-Live · Marin"; the engine alone when the voice can't be read; "Not set up". */
export function voiceRowValue(vendor: VoiceVendor | null, current: CurrentVoice | null, liveFromHealth: boolean | null): string {
  const available = vendor ? vendor.available : liveFromHealth;
  if (available === null) return '';
  if (!available) return 'Not set up';
  const title = current?.title || (current?.voice_id ? current.voice_id[0].toUpperCase() + current.voice_id.slice(1) : null);
  return title ? `${GPT_LIVE} · ${title}` : GPT_LIVE;
}
