/**
 * arturoThreadEdits.ts — renaming and deleting one of Arturo's chats, and keeping every list of them in step.
 *
 * A chat's title is shown in several places at once (the sidebar's Chats, the pane's Threads, the panel's latest
 * conversations). A rename or a delete made in one is published here, and every list applies it (applyThreadEdit),
 * so none of them shows a stale title or a chat that is gone.
 *
 * Rename is optimistic: the new title shows at once and is put back if the server refuses it. An empty name puts the
 * auto-title back (the server derives it again), so that one waits for the server's answer. Delete is not
 * optimistic: it removes every copy of the chat and cannot be undone, so it is published only once it happened.
 */
import { currentThread, setCurrentThread } from './arturoCurrent.ts';

/** services/arturo/thread_store.py TITLE_MAX: a title is one line of at most this many characters. */
export const THREAD_TITLE_MAX = 80;

/** A title as it is kept: one line, inner whitespace collapsed, trimmed. */
export function normalizeTitle(raw: string): string {
  return raw.split(/\s+/).filter(Boolean).join(' ');
}

/** Characters (as the server counts them), not UTF-16 units. */
export function titleTooLong(raw: string): boolean {
  return Array.from(normalizeTitle(raw)).length > THREAD_TITLE_MAX;
}

/** What a list shows for a chat with no title yet. */
export function threadLabel(t: { title?: string | null }): string {
  return t.title || 'Untitled';
}

export type ThreadEdit =
  | { kind: 'rename'; id: string; title: string; custom?: boolean }
  | { kind: 'delete'; id: string };

/** One list of chats with an edit applied. A list that does not hold the chat comes back as it was (same object). */
export function applyThreadEdit<T extends { id: string; title: string; title_custom?: boolean }>(list: T[], e: ThreadEdit): T[] {
  if (!list.some((t) => t.id === e.id)) return list;
  if (e.kind === 'delete') return list.filter((t) => t.id !== e.id);
  return list.map((t) => (t.id === e.id
    ? { ...t, title: e.title, ...(e.custom === undefined ? {} : { title_custom: e.custom }) } : t));
}

const listeners = new Set<(e: ThreadEdit) => void>();

/** Hear every rename and delete made in this tab. Returns the unsubscribe. */
export function subscribeThreadEdits(fn: (e: ThreadEdit) => void): () => void {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}

export function publishThreadEdit(e: ThreadEdit): void {
  for (const fn of [...listeners]) {
    try { fn(e); } catch { /* one list's failure never stops the others */ }
  }
}

async function errorOf(res: Response, fallback: string): Promise<string> {
  const json = await res.json().catch(() => ({} as Record<string, unknown>));
  return typeof json.error === 'string' && json.error ? json.error : `${fallback} (HTTP ${res.status})`;
}

export type RenameResult = { ok: true; title: string; custom: boolean } | { ok: false; error: string };

/** PATCH the title. '' puts the auto-title back; the answer is the title the server now shows. */
export async function renameThread(id: string, title: string): Promise<RenameResult> {
  if (titleTooLong(title)) return { ok: false, error: `A chat name can be at most ${THREAD_TITLE_MAX} characters` };
  try {
    const res = await fetch(`/api/arturo/threads/${encodeURIComponent(id)}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: normalizeTitle(title) }),
    });
    if (!res.ok) return { ok: false, error: await errorOf(res, "Couldn't rename the chat") };
    const json = await res.json().catch(() => ({}));
    const t = json?.thread;
    if (!t || typeof t.title !== 'string') return { ok: false, error: "Couldn't rename the chat" };
    return { ok: true, title: t.title, custom: t.title_custom === true };
  } catch {
    return { ok: false, error: "Couldn't reach the server" };
  }
}

// The latest rename asked for each chat: an older rename's answer (or its rollback) arriving after a newer one was
// asked never puts its title back over the newer one.
const latestRename = new Map<string, number>();
let renameSeq = 0;

/** Rename with every list following: the new title at once, the server's answer when it comes, the old title (and
 *  whether it was a chosen one) back, with the reason returned, when it is refused. Nothing to do (the same title)
 *  is null without asking. */
export async function commitRename(id: string, previous: string, next: string, previousCustom?: boolean): Promise<string | null> {
  const clean = normalizeTitle(next);
  if (clean === previous) return null;
  const mine = ++renameSeq;
  latestRename.set(id, mine);
  if (clean && !titleTooLong(clean)) publishThreadEdit({ kind: 'rename', id, title: clean, custom: true });
  const r = await renameThread(id, clean);
  const current = latestRename.get(id) === mine;
  if (current) latestRename.delete(id);
  if (r.ok) {
    if (current) publishThreadEdit({ kind: 'rename', id, title: r.title, custom: r.custom });
    return null;
  }
  if (current) publishThreadEdit({ kind: 'rename', id, title: previous, ...(previousCustom === undefined ? {} : { custom: previousCustom }) });
  return r.error;
}

/** A fact the delete dialog offers to forget: learned in that chat (its source is the chat). */
export interface LearnedFact { store: string; key: string; label: string; value: string }

/** The facts first learned in a chat, and whether it is the onboarding chat. null when it could not be read. */
export async function threadFacts(id: string): Promise<{ facts: LearnedFact[]; onboarding: boolean } | null> {
  try {
    const res = await fetch(`/api/arturo/threads/${encodeURIComponent(id)}?facts=1`);
    if (!res.ok) return null;
    const json = await res.json();
    if (!json || !Array.isArray(json.facts)) return null;
    const facts = (json.facts as unknown[]).filter((f): f is LearnedFact =>
      !!f && typeof f === 'object' && typeof (f as LearnedFact).value === 'string' && typeof (f as LearnedFact).label === 'string');
    return { facts, onboarding: json.onboarding === true };
  } catch {
    return null;
  }
}

export type DeleteResult = { ok: true; forgot: number } | { ok: false; error: string };

export async function deleteThread(id: string, forgetFacts: boolean): Promise<DeleteResult> {
  try {
    const res = await fetch(`/api/arturo/threads/${encodeURIComponent(id)}${forgetFacts ? '?forget_facts=1' : ''}`,
      { method: 'DELETE' });
    if (!res.ok) return { ok: false, error: await errorOf(res, "Couldn't delete the chat") };
    const json = await res.json().catch(() => ({}));
    return { ok: true, forgot: typeof json?.facts_forgotten === 'number' ? json.facts_forgotten : 0 };
  } catch {
    return { ok: false, error: "Couldn't reach the server" };
  }
}

/** Delete, then every list drops the chat; when it was the chat open here, a fresh one (`fresh()` mints its id)
 *  becomes current, so nothing stays on a chat that no longer exists. Returns the reason it failed, or null. */
export async function commitDelete(id: string, forgetFacts: boolean, fresh: () => string): Promise<string | null> {
  const r = await deleteThread(id, forgetFacts);
  if (!r.ok) return r.error;
  publishThreadEdit({ kind: 'delete', id });
  if (currentThread() === id) setCurrentThread(fresh());
  return null;
}
