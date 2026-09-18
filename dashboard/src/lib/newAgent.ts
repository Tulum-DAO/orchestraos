/**
 * newAgent.ts — the client half of Overview's "New Agent" button.
 *
 * Signed in to a provider  -> POST /api/agents/new {name, task?, runtime?}  (a real seat)
 * Not signed in            -> POST /api/agents/login-shell                  (a bash tmux
 *                             session the browser attaches to with the terminal view)
 *
 * The signed-in question is answered by the runtime catalog, the same source
 * `orchestra doctor` and Arturo's brain use: a provider counts only when it is
 * installed AND authed === true ('unverified' is never treated as usable).
 */
export interface RuntimeRow {
  id: string; label?: string; cli: string;
  installed: boolean; authed: boolean | 'unverified'; auth_reason?: string | null;
}
export interface NewAgentResult {
  ok: boolean; id?: string; session?: string; runtime?: string;
  reason?: string; detail?: string; status?: number; runtimes?: RuntimeRow[];
}
export interface LoginShellResult {
  ok: boolean; session?: string; cli?: string; hint?: string; machine?: string; reason?: string; detail?: string;
}

export function authedRuntimes(rows: RuntimeRow[]): RuntimeRow[] {
  return (rows || []).filter((r) => r.installed && r.authed === true);
}

/** What the modal shows under the name field, e.g. "Runs on Claude". */
export function runtimeLabel(r: RuntimeRow | undefined): string {
  if (!r) return '';
  return r.label || r.id.charAt(0).toUpperCase() + r.id.slice(1);
}

/** Preview of the seat name the server will use (same slug rule as the API). */
export function previewName(raw: string): string {
  return String(raw || '').trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 64);
}

export function nameError(raw: string, taken: Set<string>): string | null {
  const n = previewName(raw);
  if (!raw.trim()) return null;                       // nothing typed yet: no error, just disabled
  if (!n) return 'Use letters or numbers — that name has none.';
  if (n.length > 64) return 'That name is too long.';
  if (['all', 'none', 'new', 'gm', 'arturo', 'self', 'system'].includes(n)) return `"${n}" is reserved.`;
  if (taken.has(n)) return `"${n}" already exists.`;
  return null;
}

async function postJson<T>(path: string, body: unknown): Promise<T & { status?: number }> {
  try {
    const res = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body ?? {}) });
    const json = await res.json().catch(() => ({}));
    return { ...(json as T), status: res.status };
  } catch (e) {
    return { ok: false, reason: 'network', detail: e instanceof Error ? e.message : 'network' } as T & { status?: number };
  }
}

/** Fresh probe (busts the API's 300s cache) so a login done seconds ago counts. */
export async function freshRuntimes(): Promise<RuntimeRow[]> {
  try {
    const res = await fetch('/api/runtimes/available/refresh', { method: 'POST' });
    if (res.ok) return ((await res.json()).providers || []) as RuntimeRow[];
  } catch { /* fall through to the cached read */ }
  try {
    const res = await fetch('/api/runtimes/available');
    if (res.ok) return ((await res.json()).providers || []) as RuntimeRow[];
  } catch { /* offline */ }
  return [];
}

export const createAgent = (name: string, task: string, runtime?: string) =>
  postJson<NewAgentResult>('/api/agents/new', { name, task, runtime });

export const openLoginShell = () => postJson<LoginShellResult>('/api/agents/login-shell', {});
