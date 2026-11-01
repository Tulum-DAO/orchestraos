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
// Same card-id shape as cardRefs.ts, kept local: importing cardRefs pulls the decisions graph into
// this module, and arturoThreads.test.mjs runs under plain Node type stripping, which cannot resolve
// that graph's extensionless imports.
const CARD_ID = /\b(?:apr|qnr)_[0-9a-f]{8}_\d{1,8}\b/;
const hasRefs = (text: string): boolean => CARD_ID.test(String(text ?? ''));

/** `gist`: the conversation's own summary (services/arturo: kept current as it grows and when a call ends). */
export interface ThreadSummary { id: string; title: string; created?: number; updated: number; turns: number; snippet?: string; gist?: string }
/** `meta.kind === 'job_result'`: posted by a background job (jobs.py), with the steps it ran. */
export interface ThreadTurnMeta { kind?: string; job_id?: string; job_kind?: string; title?: string; status?: string;
  steps?: import('./arturoJobs').JobStep[]; call_id?: string;
  /** A reply that showed a choice card (kind "card"): the card, its id, and whether it was closed unanswered. */
  card_id?: string; choices?: import('./arturo').ChoiceCard; dismissed?: boolean;
  /** The card's answer as the server keeps it (the turn that answered it said so): picks and words, and when. */
  answer?: { picks?: string[]; words?: string; at?: number } }
/** `seq`: the turn's number in its thread, present when the thread was read a page at a time (loadThreadPage). */
export interface ThreadTurn { role: 'user' | 'assistant'; content: string; ts?: number; mode?: string; meta?: ThreadTurnMeta; seq?: number }
export interface ThreadDetail extends ThreadSummary { turns: number; }
export interface LoadedThread { id: string; title: string; turns: ThreadTurn[]; last_brain?: { provider: string; model: string } | null }
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
    return threadFromJson(json.thread);
  } catch { return null; }
}

/** How many turns a page of a long thread holds (the newest on opening it, then each older page as the reader
 *  scrolls up). */
export const THREAD_PAGE_TURNS = 100;

/** A thread a page at a time: the newest turns (no `before`), or those just older than the turn numbered `before`.
 *  `hasOlder`/`next` page further up. A server that does not page (it answers the whole thread, with no has_older)
 *  is the whole thread with nothing older; a page that is not older than its cursor (a hop that dropped `before`)
 *  is refused, so the same turns are never drawn twice. null when the thread is gone or unreachable. */
export async function loadThreadPage(id: string, before?: number, limit = THREAD_PAGE_TURNS):
    Promise<{ thread: LoadedThread | null; hasOlder: boolean; next: number | null } | null> {
  try {
    const q = new URLSearchParams({ limit: String(limit), ...(before !== undefined ? { before: String(before) } : {}) });
    const res = await fetch(`/api/arturo/threads/${encodeURIComponent(id)}?${q}`);
    if (!res.ok) return null;
    const json = await res.json() as { thread?: ThreadJson | null; has_older?: boolean; oldest_seq?: number | null; error?: string };
    const thread = threadFromJson(json.thread);
    if (before !== undefined) {
      if (typeof json.has_older !== 'boolean' || json.error) return null;
      if (thread?.turns.some((t) => typeof t.seq !== 'number' || t.seq >= before)) return null;
    }
    const pages = typeof json.has_older === 'boolean';
    return { thread, hasOlder: pages && json.has_older === true && typeof json.oldest_seq === 'number',
             next: pages && typeof json.oldest_seq === 'number' ? json.oldest_seq : null };
  } catch { return null; }
}

/** One send's fate on the server (DEC-1791518421640932): `result` is its whole answer (cards included), given
 *  only to the caller that sent it. */
export type TurnFate =
  | { state: 'running' | 'lost' | 'unknown' }
  | { state: 'done'; result?: Record<string, any> };

/** Read a send's fate by its turn id. null = the read itself failed (a hop still starting): keep waiting. */
export async function loadTurn(conversationId: string, turnId: string): Promise<TurnFate | null> {
  try {
    const res = await fetch(`/api/arturo/threads/${encodeURIComponent(conversationId)}?turn=${encodeURIComponent(turnId)}`);
    if (!res.ok) return null;
    const json = await res.json();
    return (json && json.turn && typeof json.turn.state === 'string') ? json.turn as TurnFate : null;
  } catch { return null; }
}

/** The server's thread JSON as the client uses it. last_brain rides along (null = default brain)
 *  so reopening a thread restores the brain it last answered on (DEC-1790669162399904 §1.5). */
interface ThreadJson { id: string; title?: string; turns?: ThreadTurn[]; last_brain?: { provider: string; model: string } | null }

export function threadFromJson(t: ThreadJson | null | undefined): LoadedThread | null {
  if (!t) return null;
  return { id: t.id, title: t.title || '', turns: t.turns || [], last_brain: t.last_brain ?? null };
}

const PRETTY: Record<string, string> = {
  approvals: 'Approvals', agents: 'Agents', agent: 'Agent', projects: 'Projects', tasks: 'Tasks',
  inbox: 'Inbox', roadmap: 'Roadmap', clients: 'Clients', people: 'People', activity: 'Activity',
  overview: 'Overview',
};

/** What the card SAYS. Plain English — it is a card in a chat, not a debug line, never "route=/approvals
 *  entity=approvals:…". A card on screen is named by its question (`cards`: lib/screenCards screenLabel, "Approvals ·
 *  Which size?"), and a card id is never shown: a page focused on one says only the page. */
export function contextCardLabel(ctx?: ArturoContext | null, cards?: string | null): string {
  if (!ctx) return '';
  const kind = PRETTY[ctx.entityKind || ''] || (ctx.entityKind ? ctx.entityKind[0].toUpperCase() + ctx.entityKind.slice(1) : 'This page');
  if (cards) return `${kind} · ${cards}`;
  return ctx.entityId && !hasRefs(ctx.entityId) ? `${kind} · ${ctx.entityId}` : kind;
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

/** Close one of Arturo's choice cards without answering it (it stays in the thread, marked dismissed). Returns an
 *  error to show, or null. */
export async function dismissCard(conversationId: string, cardId: string): Promise<string | null> {
  try {
    const res = await fetch(`/api/arturo/threads/${encodeURIComponent(conversationId)}/cards/${encodeURIComponent(cardId)}/dismiss`,
      { method: 'POST' });
    const json = await res.json().catch(() => ({}));
    return res.ok && json.ok ? null : (json.error || `Couldn't dismiss (HTTP ${res.status})`);
  } catch {
    return "Couldn't reach the server";
  }
}
