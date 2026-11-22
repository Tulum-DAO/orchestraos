/**
 * cardVerbs.ts — the answers a card that is not a question takes, and where each one is sent. One set of rules for
 * every surface that draws such a card (an agent's chat, the Approvals page, the Inbox), so they never disagree.
 *
 * - A task for you (kind 'human_task', filed by `approval.py human-task`) takes its own three verbs: Done, Can't and
 *   Snooze. They go to /approvals/unified/:id/answer as { answer: verb }; words typed on the card ride along as a note.
 *   The server takes only these three on such a card (watch_gateway.apply_answer: "human_task verbs are
 *   done|cant|snooze"), and Approve on one is refused with a 400.
 * - Every other card that is not a question is an approval: Approve, and Deny with a reason the agent sees.
 *
 * Questions (kind 'menu') are not answered here: they are the one question card (DecisionQuestion / MultiPartAnswer).
 */

export type VerbTone = 'go' | 'stop' | 'quiet';

export interface CardVerb {
  /** What is sent: 'approve' | 'deny' | 'done' | 'cant' | 'snooze'. */
  verb: string;
  label: string;
  tone: VerbTone;
  /** Deny: needs words (the reason the agent sees). */
  needsWords?: boolean;
  /** Done / Can't: words typed on the card go with it as a note. */
  takesWords?: boolean;
}

export type VerbSet = { mode: 'approval'; verbs: CardVerb[] } | { mode: 'task'; verbs: CardVerb[] };

/** The two shapes a card reaches the dashboard in (a chat Decision, an Approvals-page UnifiedApproval) share these. */
export interface VerbCard {
  kind?: string | null;
  options?: readonly unknown[] | null;
}

const APPROVAL_VERBS: CardVerb[] = [
  { verb: 'approve', label: 'Approve', tone: 'go' },
  { verb: 'deny', label: 'Deny', tone: 'stop', needsWords: true },
];

/** A task's verbs, in the order the server files them. Must match watch_gateway.apply_answer's human_task verbs. */
export const TASK_VERBS = ['done', 'cant', 'snooze'] as const;
const TASK_VERB: Record<(typeof TASK_VERBS)[number], CardVerb> = {
  done: { verb: 'done', label: 'Done', tone: 'go', takesWords: true },
  cant: { verb: 'cant', label: "Can't", tone: 'stop', takesWords: true },
  snooze: { verb: 'snooze', label: 'Snooze', tone: 'quiet' },
};
const isTaskVerb = (v: string): v is (typeof TASK_VERBS)[number] => (TASK_VERBS as readonly string[]).includes(v);

/** The verbs a card takes. A task's come from its own options (only the three the server takes, in its order;
 *  all three when it carries none); anything else that is not a question is Approve / Deny. */
export function cardVerbs(card: VerbCard): VerbSet {
  if (card.kind === 'human_task') {
    const own = (Array.isArray(card.options) ? card.options : [])
      .map((o) => String(o ?? '').trim().toLowerCase()).filter(isTaskVerb);
    const keys = own.length ? [...new Set(own)] : [...TASK_VERBS];
    return { mode: 'task', verbs: keys.map((k) => TASK_VERB[k]) };
  }
  return { mode: 'approval', verbs: APPROVAL_VERBS };
}

/** The lines a card reports to the cards-on-screen context (lib/screenCards.ts) as its options: "A. Approve". */
export function verbOptionLines(set: VerbSet): string[] {
  return set.verbs.map((v, i) => `${String.fromCharCode(65 + i)}. ${v.label}${v.needsWords ? ' (needs a reason)' : ''}`);
}

/** Which verb an answer handed back by the assistant names: its letter ("B", "b)"), its verb ("cant") or its label
 *  ("Can't"). Null when it names none of this card's verbs. */
export function verbFromPick(set: VerbSet, said: string): CardVerb | null {
  const s = String(said || '').trim().toLowerCase();
  if (!s) return null;
  const letter = s.match(/^([a-z])(?:[.)]|$)/)?.[1];
  if (letter) {
    const v = set.verbs[letter.charCodeAt(0) - 97];
    if (v) return v;
  }
  const plain = s.replace(/[^a-z]/g, '');
  return set.verbs.find((v) => v.verb === plain || v.label.toLowerCase().replace(/[^a-z]/g, '') === plain) ?? null;
}

export type VerbRequest = { path: string; body: Record<string, unknown> } | { error: string };

/** Where a verb goes and with what. Approve and Deny keep their own routes; a task's verbs go to the card's answer
 *  route as { answer }. A Deny without words is refused here (the server would refuse it too). */
export function verbRequest(id: string, verb: string, words = ''): VerbRequest {
  const base = `/approvals/unified/${encodeURIComponent(id)}`;
  const text = String(words || '').trim();
  if (verb === 'approve') return { path: `${base}/approve`, body: {} };
  if (verb === 'deny') return text ? { path: `${base}/deny`, body: { reason: text } } : { error: 'Add a reason to deny.' };
  if (isTaskVerb(verb)) {
    return { path: `${base}/answer`, body: { answer: verb, ...(text && TASK_VERB[verb].takesWords ? { answer_text: text } : {}) } };
  }
  return { error: `This card does not take "${verb}".` };
}

/** "API /approvals/…: 400" from lib/api's post, as the card shows it: "Could not send: 400". */
export function sendErrorText(e: unknown): string {
  return e instanceof Error ? e.message.replace(/^API [^:]+: /, 'Could not send: ') : 'Could not send';
}
