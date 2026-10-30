/**
 * arturoResume.ts — leaving onboarding partway and coming back (DEC-1791511578959986).
 *
 * The operator, 2026-10-09: "Arturo should always know how far along in the onboarding a user is in
 * case they leave part way through and come back." The page opened with an empty feed, sent the
 * first-run opener again into the same thread, and the brain greeted them as new, with their history
 * hidden above. These are the page's decisions, kept here so they are tested (ArturoHome uses them).
 */
import { isPageOpener, type ChoiceCard } from './arturo';
import type { ThreadTurn } from './arturoThreads';
import type { TurnPart } from './turnParts';
import { jobResultParts, jobToolParts, type ArturoJob } from './arturoJobs';
import { jobResult, type FinishedResult } from './threadKit/results';
import { splitAttachmentPreamble } from './arturoUpload';
import { isCardEcho, storedAnswerText } from './choiceCard';

/** The proxy's page-context line (arturo-proxy.py _context_line) on line 1 of a stored message. The
 *  model saw it; the operator never typed it. The server strips it from thread reads too. */
const CONTEXT_LINE = /^\[Context: route=[^\n]*\]\n/;

export function stripContextLine(text: string): string {
  return (text || '').replace(CONTEXT_LINE, '');
}

export interface HydratedTurn { role: 'arturo' | 'user'; text: string; parts?: TurnPart[]; images?: string[];
  /** A finished background job (meta kind "job_result"): drawn as a result card (components/kit/ResultCard.tsx). */
  result?: FinishedResult;
  /** The choice card the reply showed, what was sent in answer (the next message), or that it was dismissed. */
  choices?: ChoiceCard; answered?: string; dismissed?: boolean;
  /** The answer came from the card's own record on the server, not from the message after it. */
  answeredOnServer?: boolean;
  /** A card's own answer: drawn on the answered card, so the feed draws no bubble for it. */
  cardEcho?: boolean;
  /** Spoken on a live call (the thread stores mode "voice"): the feed groups these into the call's card. */
  voice?: boolean;
  /** The call-ended line for this call id: closes the call's card. */
  callEnd?: string;
  /** When it was said (epoch seconds): the card's call length. */
  ts?: number }

/** A stored thread as the feed shows it: the page's own opener hidden, context lines stripped. */
export function hydrateTurns(turns: ThreadTurn[]): HydratedTurn[] {
  return turns
    .map((x) => {
      const parts = x.role === 'user' ? undefined : jobResultParts(x.meta, x.content);
      const result = x.role === 'user' ? null : jobResult(x.meta, x.content, {
        key: `job:${x.meta?.job_id || x.ts || ''}`, ts: x.ts,
        steps: x.meta?.steps?.length ? jobToolParts({ id: x.meta.job_id || 'job', status: 'done', steps: x.meta.steps } as ArturoJob) : undefined,
      });
      const sent = x.role === 'user' ? splitAttachmentPreamble(stripContextLine(x.content)) : null;
      return { role: x.role === 'user' ? 'user' as const : 'arturo' as const,
               text: sent ? sent.text : x.content, ...(parts ? { parts } : {}), ...(result ? { result } : {}),
               ...(sent?.images.length ? { images: sent.images } : {}),
               ...(x.mode === 'voice' ? { voice: true } : {}),
               ...(x.meta?.kind === 'call_end' && x.meta.call_id ? { callEnd: x.meta.call_id } : {}),
               ...(x.role !== 'user' && x.meta?.kind === 'card' && x.meta.choices
                 ? { choices: { ...x.meta.choices, card_id: x.meta.card_id }, ...(x.meta.dismissed ? { dismissed: true } : {}),
                     ...(x.meta.answer ? { answered: storedAnswerText(x.meta.choices.options, x.meta.answer), answeredOnServer: true } : {}) } : {}),
               ...(typeof x.ts === 'number' ? { ts: x.ts } : {}) };
    })
    .filter((x) => !(x.role === 'user' && isPageOpener(x.text)))
    // A card's answer is its own record on the server; a card from before those were kept is answered by the message
    // that followed it (a tap sends the option's words, or they typed instead).
    .map((x, i, all) => {
      if (!x.choices || x.dismissed || x.answeredOnServer) return x;
      const next = all.slice(i + 1).find((y) => y.role === 'user');
      return next ? { ...x, answered: next.text } : x;
    })
    // A card's own answer (a pick) is shown on the answered card, not again as a bubble: the first user message
    // after a card, when it names one of the card's options (lib/choiceCard isCardEcho).
    .map((x, i, all) => {
      if (x.role !== 'user') return x;
      const before = all.slice(0, i).reverse();
      const card = before.findIndex((y) => !!y.choices && !y.dismissed);
      const prevUser = before.findIndex((y) => y.role === 'user');
      if (card < 0 || (prevUser >= 0 && prevUser < card)) return x;
      return isCardEcho(before[card].choices!, x.text) ? { ...x, cardEcho: true } : x;
    });
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
