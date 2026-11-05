/**
 * openWaiting.ts — opening what waits on the operator, the same way from every place that lists it (the bell and
 * Overview's Needs You).
 *
 * An agent's waiting card opens where it is drawn: the agent's chat, focused on that card, when the chat draws it;
 * else the Approvals page focused on it (a card from before the agent's current generation, or a menu mirror the
 * chat shows as its live menu). Decided on fresh reads (a list can be seconds old). Pending questionnaires are
 * counted one way for both.
 */
import { focusDecision, fetchDecisions, resolveBellJump, approvalsFocusPath, type PendingCard } from './decisions.ts';

export type OpenResult = { opened: 'chat' | 'approvals'; id: string } | { opened: null; reason: 'gone' | 'error' };

export interface OpenDeps {
  navigate: (to: string) => void;
  /** The pending cards right now (default: GET /api/approvals/unified?status=pending). */
  readPending?: () => Promise<PendingCard[]>;
  /** The ids of the pending cards the agent's chat draws (default: its decisions). */
  readChatIds?: (agent: string) => Promise<string[]>;
  /** Asks the chat to bring a card into view when it opens (default: focusDecision). */
  focus?: (agent: string, id: string) => void;
}

async function pendingCards(): Promise<PendingCard[]> {
  const r = await fetch('/api/approvals/unified?status=pending');
  if (!r.ok) throw new Error(`approvals ${r.status}`);
  return ((await r.json()) as { items?: PendingCard[] }).items ?? [];
}

const chatIds = async (agent: string) => (await fetchDecisions(agent)).filter((d) => d.state === 'pending').map((d) => d.id);

/** Open this agent's oldest waiting card where it is drawn. `gone`: nothing of its is waiting any more. */
export async function openWaitingCard(agent: string, deps: OpenDeps): Promise<OpenResult> {
  try {
    const target = await resolveBellJump(agent, deps.readPending ?? pendingCards, deps.readChatIds ?? chatIds);
    if (!target) return { opened: null, reason: 'gone' };
    if (target.where === 'chat') {
      (deps.focus ?? focusDecision)(agent, target.id);
      deps.navigate(`/agent/${encodeURIComponent(agent)}`);
    } else {
      deps.navigate(approvalsFocusPath(target.id));
    }
    return { opened: target.where, id: target.id };
  } catch {
    return { opened: null, reason: 'error' };
  }
}

/** What to say when a click finds nothing to open. */
export function openNotice(agent: string, r: OpenResult): string | null {
  if (r.opened) return null;
  return r.reason === 'gone' ? `Nothing from ${agent} is waiting any more — it was answered elsewhere.` : "Couldn't load that card just now — try again.";
}

/** Questionnaires waiting for an answer: the route's own count, else the pending ones in its list. */
export function pendingQuestionnaires(data: { pending?: number; questionnaires?: { status?: string }[] } | null | undefined): number {
  return data?.pending || (data?.questionnaires ?? []).filter((q) => q.status === 'pending').length || 0;
}
