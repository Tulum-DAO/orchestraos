/**
 * arturoThreads.ts — G20: switching between Arturo's conversations, and the page-context card.
 *
 * Two things the operator asked for (2026-09-18):
 *
 *  1. "swap between Arturo's previous conversations and pick up right where we left off".
 *     The thread list and every thread's turns come from the SERVER (/api/arturo/threads),
 *     not from localStorage, so the home, the pill and the phone see one thread space and a
 *     thread outlives a reload or a restart. localStorage keeps only "which thread was I in".
 *
 *  2. Arturo focuses on whatever page you are looking at BY DEFAULT — and that focus is a
 *     CARD in the chat that you can DELETE. Before this, the page context rode invisibly in
 *     front of the text: Arturo used it, but you could neither see it nor turn it off. Now it
 *     is an object in the thread: visible, and removing it is how you say "stop focusing on
 *     this page". The choice is per THREAD (asking about the approvals page in one thread
 *     should not mute context in another) and it persists, so it survives a reload.
 */
import type { ArturoContext } from './arturo';

export interface ThreadSummary { id: string; title: string; created?: number; updated: number; turns: number; snippet?: string }
export interface ThreadTurn { role: 'user' | 'assistant'; content: string; ts?: number }
export interface ThreadDetail extends ThreadSummary { turns: number; }
export interface LoadedThread { id: string; title: string; turns: ThreadTurn[] }
export interface ContextCard { kind: 'page-context'; label: string; context: ArturoContext }

/** The thread list, newest first. A dead hop shows NO threads rather than throwing into a
 *  render — the switcher degrades to "nothing to switch to", which is honest and harmless. */
export async function listThreads(limit = 50, offset = 0): Promise<ThreadSummary[]> {
  try {
    const res = await fetch(`/api/arturo/threads?limit=${limit}&offset=${offset}`);
    if (!res.ok) return [];
    const json = await res.json();
    return (json.threads || []) as ThreadSummary[];
  } catch { return []; }
}

/** One thread with its turns, for resuming it. null when it is gone or unreachable. */
export async function loadThread(id: string): Promise<LoadedThread | null> {
  try {
    const res = await fetch(`/api/arturo/threads/${encodeURIComponent(id)}`);
    if (!res.ok) return null;
    const json = await res.json();
    const t = json.thread;
    if (!t) return null;
    return { id: t.id, title: t.title || '', turns: (t.turns || []) as ThreadTurn[] };
  } catch { return null; }
}

const PRETTY: Record<string, string> = {
  approvals: 'Approvals', agents: 'Agents', agent: 'Agent', projects: 'Projects', tasks: 'Tasks',
  inbox: 'Inbox', roadmap: 'Roadmap', clients: 'Clients', people: 'People', activity: 'Activity',
  overview: 'Overview',
};

/** What the card SAYS. Plain English — it is a card in a chat, not a debug line, so
 *  "Approvals · apr_1a2b3c4d", never "route=/approvals entity=approvals:apr_1a2b3c4d". */
export function contextCardLabel(ctx?: ArturoContext | null): string {
  if (!ctx) return '';
  const kind = PRETTY[ctx.entityKind || ''] || (ctx.entityKind ? ctx.entityKind[0].toUpperCase() + ctx.entityKind.slice(1) : 'This page');
  return ctx.entityId ? `${kind} · ${ctx.entityId}` : kind;
}

export function contextCardFor(ctx?: ArturoContext | null): ContextCard | null {
  if (!ctx) return null;
  return { kind: 'page-context', label: contextCardLabel(ctx), context: ctx };
}

// The dismissal is a per-viewer, per-thread preference — exactly what localStorage is for.
// (The THREAD itself is server-side; only this choice is local.)
const DISMISS_KEY = 'orchestra.arturo.context.dismissed';

function dismissedSet(): Set<string> {
  try {
    const raw = localStorage.getItem(DISMISS_KEY);
    return new Set(raw ? (JSON.parse(raw) as string[]) : []);
  } catch { return new Set(); }
}

function writeDismissed(s: Set<string>): void {
  try { localStorage.setItem(DISMISS_KEY, JSON.stringify([...s].slice(-200))); } catch { /* private mode */ }
}

export function isContextDismissed(threadId: string): boolean {
  return dismissedSet().has(threadId);
}

/** The operator deleted the card: this thread stops carrying the page context. */
export function dismissContext(threadId: string): void {
  const s = dismissedSet();
  s.add(threadId);
  writeDismissed(s);
}

/** Re-attach it — deleting the card is not a one-way door. */
export function restoreContext(threadId: string): void {
  const s = dismissedSet();
  s.delete(threadId);
  writeDismissed(s);
}

/** The context this turn should carry: the page's, unless the card was deleted. Default ON. */
export function contextForTurn(threadId: string, ctx?: ArturoContext | null): ArturoContext | null {
  if (!ctx) return null;
  return isContextDismissed(threadId) ? null : ctx;
}
