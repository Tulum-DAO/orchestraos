/**
 * arturoResume.ts — leaving onboarding partway and coming back (DEC-1791511578959986).
 *
 * The operator, 2026-10-09: "Arturo should always know how far along in the onboarding a user is in
 * case they leave part way through and come back." The page opened with an empty feed, sent the
 * first-run opener again into the same thread, and the brain greeted them as new, with their history
 * hidden above. These are the page's decisions, kept here so they are tested (ArturoHome uses them).
 */
import { isPageOpener } from './arturo';
import type { ThreadTurn } from './arturoThreads';

/** The proxy's page-context line (arturo-proxy.py _context_line) on line 1 of a stored message. The
 *  model saw it; the operator never typed it. The server strips it from thread reads too. */
const CONTEXT_LINE = /^\[Context: route=[^\n]*\]\n/;

export function stripContextLine(text: string): string {
  return (text || '').replace(CONTEXT_LINE, '');
}

export interface HydratedTurn { role: 'arturo' | 'user'; text: string }

/** A stored thread as the feed shows it: the page's own opener hidden, context lines stripped. */
export function hydrateTurns(turns: ThreadTurn[]): HydratedTurn[] {
  return turns
    .map((x) => ({ role: x.role === 'user' ? 'user' as const : 'arturo' as const,
                   text: x.role === 'user' ? stripContextLine(x.content) : x.content }))
    .filter((x) => !(x.role === 'user' && isPageOpener(x.text)));
}

/** The onboarding thread: the server's (every browser and device resumes the same one), else this
 *  browser's current conversation (a server that has not pinned one yet). */
export function onboardingConversation(serverId: string | null | undefined, current: string): string {
  return serverId || current;
}

/** Only turns in the onboarding thread run under the onboarding playbook: resuming another thread from
 *  the sidebar neither ends onboarding nor puts its instructions on an unrelated conversation. */
export function carriesOnboardingMarker(step: string, conversationId: string, onboardingConv: string): boolean {
  return step === 'onboarding' && (!onboardingConv || conversationId === onboardingConv);
}

interface Bubble { id: number; role: 'arturo' | 'user'; text: string; pending?: boolean; choices?: unknown; pairCard?: unknown }

/** A returning opener's reply, into its pending bubble. When the bubble before it is Arturo's unanswered
 *  QUESTION (it ends in "?"), that stale question is replaced, so the next step shows exactly once. A
 *  report ("Noted: iPhone.", "iPhone is paired.") or anything already answered stays, card or no
 *  card. An empty reply leaves no bubble. */
export function mergeResumeReply<B extends Bubble>(turns: B[], pendingId: number,
  reply: { text: string; choices?: unknown; pairCard?: unknown }): B[] {
  const at = turns.findIndex((t) => t.id === pendingId);
  if (at < 0) return turns;
  if (!(reply.text || '').trim() && !reply.choices && !reply.pairCard) return turns.filter((t) => t.id !== pendingId);
  const prev = at > 0 ? turns[at - 1] : undefined;
  const stale = !!prev && prev.role === 'arturo' && !prev.pending && /\?\s*$/.test(prev.text || '');
  const filled = { ...turns[at], pending: false, text: reply.text, choices: reply.choices, pairCard: reply.pairCard };
  return turns.flatMap((t, i) => (stale && i === at - 1) ? [] : i === at ? [filled] : [t]);
}

/** Another turn in this conversation is still running (a second tab, a phone): wait and send again. */
export function isBusy(r: { ok?: boolean; status?: number; error?: string }): boolean {
  return !r.ok && r.status === 409 && r.error === 'busy';
}
