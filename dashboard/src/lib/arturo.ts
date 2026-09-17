/**
 * arturo.ts — the browser's client for Arturo's text path (tracks T2/T4).
 *
 * POST /api/arturo/text   -> gateway /arturo/text -> :5071/text (one tool-enabled turn on
 *                            whichever brain the install has: api key / authed CLI / none)
 * GET  /api/arturo/health -> brain {kind, runtime, model}, mode voice|text-only
 * GET  /api/runtimes/available -> the runtime catalog probe (installed + authed per CLI)
 *
 * The context record rides in front of the text as one line so every page's pill can
 * hand Arturo "where the user is" without a second contract.
 */
export interface ArturoBrain { kind: 'api' | 'runtime' | 'none'; runtime?: string; cli?: string; model: string; reason?: string; provider?: string }
export interface ArturoHealth { ok: boolean; brain?: ArturoBrain; brain_mode?: string; mode?: 'voice' | 'text-only'; voice?: boolean; error?: string }
export interface ArturoReply { ok: boolean; reply_text?: string; conversation_id?: string; brain?: ArturoBrain; tools_called?: string[]; error?: string; detail?: unknown }
export interface ArturoContext { route: string; entityKind?: string; entityId?: string; hint?: string }
export interface RuntimeRow { id: string; label?: string; cli?: string; installed: boolean; authed: boolean | 'unverified'; auth_reason?: string | null }

export function contextLine(ctx?: ArturoContext | null): string {
  if (!ctx) return '';
  const bits = [`route=${ctx.route}`];
  if (ctx.entityKind) bits.push(`entity=${ctx.entityKind}${ctx.entityId ? ':' + ctx.entityId : ''}`);
  if (ctx.hint) bits.push(`hint=${ctx.hint}`);
  return `[Context: ${bits.join(' ')}]`;
}

export async function arturoText(text: string, conversationId: string, ctx?: ArturoContext | null): Promise<ArturoReply> {
  const line = contextLine(ctx);
  const body = { text: line ? `${line}\n${text}` : text, conversation_id: conversationId };
  try {
    const res = await fetch('/api/arturo/text', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    const json = await res.json().catch(() => ({}));
    if (!res.ok) return { ok: false, error: json.error || `HTTP ${res.status}`, detail: json.detail };
    return json as ArturoReply;
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : 'network' };
  }
}

export async function arturoHealth(): Promise<ArturoHealth> {
  try {
    const res = await fetch('/api/arturo/health');
    const json = await res.json().catch(() => ({}));
    return res.ok ? (json as ArturoHealth) : { ok: false, error: json.error || `HTTP ${res.status}` };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : 'network' };
  }
}

export async function runtimesAvailable(): Promise<RuntimeRow[]> {
  try {
    const res = await fetch('/api/runtimes/available');
    if (!res.ok) return [];
    const json = await res.json();
    return (json.providers || []) as RuntimeRow[];
  } catch { return []; }
}

/** Header/chip label for the brain: "Claude (CLI)", "gemini-2.5-flash", or "no brain". */
export function brainLabel(b?: ArturoBrain | null): string {
  if (!b) return '…';
  if (b.kind === 'none') return 'no brain';
  if (b.kind === 'runtime') {
    const m = b.model && !b.model.endsWith('-cli-default') ? b.model : (b.runtime || 'cli');
    return prettyModel(m);
  }
  return prettyModel(b.model);
}

export function prettyModel(id: string): string {
  const m = id.toLowerCase();
  if (m.includes('fable')) return 'Fable ' + (m.match(/(\d)-?(\d)?/)?.slice(1).filter(Boolean).join('.') || '');
  if (m.includes('opus')) return 'Opus ' + (m.match(/opus-(\d)(?:-(\d))?/)?.slice(1).filter(Boolean).join('.') || '');
  if (m.includes('sonnet')) return 'Sonnet ' + (m.match(/sonnet-(\d)(?:-(\d))?/)?.slice(1).filter(Boolean).join('.') || '');
  if (m.includes('haiku')) return 'Haiku ' + (m.match(/haiku-(\d)-(\d)/)?.slice(1).join('.') || '');
  if (m.startsWith('claude')) return 'Claude';
  if (m.startsWith('gemini')) return m.replace('gemini-', 'Gemini ').replace(/-/g, ' ');
  if (m.startsWith('gpt')) return m.toUpperCase().replace(/-/g, ' ');
  if (m === 'codex') return 'Codex';
  return id;
}

export function greeting(name?: string | null): string {
  const h = new Date().getHours();
  const part = h < 5 ? 'Night' : h < 12 ? 'Morning' : h < 18 ? 'Afternoon' : 'Evening';
  return name ? `${part}, ${name}` : `${part}`;
}

export function slugify(s: string): string {
  // seat name = the first three meaningful words, e.g. "write a limerick about tmux" -> write-limerick-tmux
  const stop = new Set(['a', 'an', 'the', 'to', 'and', 'of', 'for', 'in', 'on', 'it', 'about', 'with', 'my', 'me', 'please']);
  const words = s.toLowerCase().replace(/[^a-z0-9\s-]+/g, ' ').split(/\s+/).filter((w) => w && !stop.has(w)).slice(0, 3);
  return words.join('-').slice(0, 28) || 'first-agent';
}

export function newConversationId(prefix: string): string {
  return `${prefix}_${Math.random().toString(36).slice(2, 10)}`;
}

const KIND: Record<string, string> = {
  approvals: 'approvals', agents: 'agents', agent: 'agent', projects: 'projects', tasks: 'tasks',
  inbox: 'inbox', roadmaps: 'roadmap', clients: 'clients', people: 'people', activity: 'activity',
};

export function contextFromLocation(pathname: string, params: Record<string, string | undefined>, search: string): ArturoContext {
  const seg = pathname.split('/').filter(Boolean);
  const kind = KIND[seg[0] || ''] || (seg[0] || 'overview');
  const q = new URLSearchParams(search);
  const entityId = params.id || params.projectSlug || params.clientId || q.get('id') || q.get('focus') || seg[1] || undefined;
  return { route: pathname, entityKind: kind, entityId };
}

