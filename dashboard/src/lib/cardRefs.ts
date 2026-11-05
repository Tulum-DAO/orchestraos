/**
 * cardRefs.ts — a card id in a message is never shown to a person. Any reference to a card (an approval or decision
 * `apr_…`, a questionnaire `qnr_…`) in an agent's reply, a mail line, a receipt, an old message or Arturo's context
 * card is drawn as a chip instead (components/kit/ObjectChip.tsx): the kind's icon, the question's first words and the
 * card's live state ("Which size? · Answered: Circle", "Deploy the drawer? · Waiting"). Clicking it opens the card.
 *
 * This file holds the rules, pure so they are tested without a browser:
 *  - finding references in text (splitRefs), never inside a word, and the ids' own shapes only;
 *  - one summary of a card (CardSummary) from whichever feed it came from (a decision row, a waiting approvals row,
 *    a questionnaire's detail);
 *  - the chip's words (chipLabel), and the card in words for a surface that can only speak or print text: a call,
 *    Telegram, a watch notification (describeCard: the question, its state, the answer or what is asked; never an id).
 *
 * The chip is an object chip: a reference names a kind of object and its id. Only cards exist today; agents, files,
 * jobs and messages are the kinds it is shaped to take later.
 */
import type { Decision } from './decisions.ts';
import type { BatchRecord, MenuPart } from './multiPart.ts';
import { batchAnswerLines } from './drawerHistory.ts';

/** What a reference can point at. Only cards today; the rest are the kinds the chip is shaped for. */
export type RefType = 'card' | 'agent' | 'file' | 'job' | 'message';
export interface ObjectRef { type: RefType; id: string }

/** The two card ids an agent can write: an approval or decision card, and a questionnaire. Their real shape
 *  (scripts/approval_schema.py, questionnaire_schema.py): a prefix, 8 hex digits, then up to 8 digits. */
const CARD_ID = /\b(?:apr|qnr)_[0-9a-f]{8}_\d{1,8}\b/g;
/** The same, with the backticks of an id quoted as code in plain text (`apr_…`) taken with it. */
const CARD_REF = /(`?)\b((?:apr|qnr)_[0-9a-f]{8}_\d{1,8})\b\1/g;
const CARD_ID_ONE = /^(?:apr|qnr)_[0-9a-f]{8}_\d{1,8}$/;

export function isCardId(s: string): boolean {
  return CARD_ID_ONE.test(String(s ?? '').trim());
}

export type RefPiece = { t: 'text'; text: string } | { t: 'ref'; ref: ObjectRef };

/** Text with each card id taken out as a reference, in order (an id quoted as code loses its backticks too). Text
 *  with none comes back as one piece. */
export function splitRefs(text: string): RefPiece[] {
  const s = String(text ?? '');
  const out: RefPiece[] = [];
  let last = 0;
  CARD_REF.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = CARD_REF.exec(s)) !== null) {
    if (m.index > last) out.push({ t: 'text', text: s.slice(last, m.index) });
    out.push({ t: 'ref', ref: { type: 'card', id: m[2] } });
    last = m.index + m[0].length;
  }
  if (last < s.length || !out.length) out.push({ t: 'text', text: s.slice(last) });
  return out;
}

export function hasRefs(text: string): boolean {
  CARD_ID.lastIndex = 0;
  return CARD_ID.test(String(text ?? ''));
}

/** Any card id left in words meant for a person or a voice, said as "a card". */
export function scrubIds(text: string): string {
  return String(text ?? '').replace(CARD_ID, 'a card');
}

// ---- One summary of a card -----------------------------------------------------------------------------------------

export type RefCardKind = 'decision' | 'approval' | 'multipart' | 'questionnaire' | 'menu' | 'permission' | 'choice';
export type RefCardState = 'waiting' | 'answered' | 'dismissed' | 'expired' | 'withdrawn' | 'replaced';

export interface CardSummary {
  id: string;
  kind: RefCardKind;
  /** Who asked: the agent. */
  from: string | null;
  state: RefCardState;
  question: string;
  /** Answered: what was chosen or said, in one line (each part's answer, joined by "; "). */
  answer: string | null;
  /** A card of several parts, answered: each part's question and answer, for saying it in words. */
  answerParts?: { question: string; text: string }[];
  /** What it asks: the option labels of its first (or only) part. */
  options: string[];
  /** Questions after the first on a card of several. */
  more: number;
}

const STATE_OF: Record<string, RefCardState> = {
  pending: 'waiting', answered: 'answered', dismissed: 'dismissed', expired: 'expired', withdrawn: 'withdrawn',
  superseded: 'replaced', approved: 'answered', rejected: 'answered', resolved: 'answered', submitted: 'answered',
  discarded: 'dismissed', completed: 'answered',
};

export function refState(s: string | null | undefined): RefCardState {
  return STATE_OF[String(s || '').toLowerCase()] ?? 'answered';
}

const BUILT_IN = /^\s*(type something\.?|chat about this\.?)\s*$/i;
const optionLabels = (opts: { label?: string }[] | undefined | null) =>
  (opts ?? []).map((o) => String(o?.label ?? '').trim()).filter((l) => l && !BUILT_IN.test(l));

/** What an approval or decision card's answer was, in one line. */
function decisionAnswerText(d: Pick<Decision, 'answer' | 'answer_text' | 'option_n' | 'menu'>, kind: RefCardKind): string | null {
  const a = (d.answer || '').toLowerCase();
  const words = (d.answer_text || '').trim();
  if (kind === 'approval') {
    const verb = a === 'approve' || a === 'accept' ? 'Approved' : a === 'deny' || a === 'reject' ? 'Denied' : '';
    if (verb) return words ? `${verb} — ${words}` : verb;
  }
  const opt = d.option_n ? (d.menu?.options || []).find((o) => o.n === d.option_n) : undefined;
  const label = opt?.label && !/write-in|other|type something/i.test(opt.label) ? opt.label : '';
  if (label && words) return `${label} — ${words}`;
  return label || words || (a === 'approve' ? 'Approved' : a === 'deny' ? 'Denied' : null);
}

/** A card row from GET /api/approvals/unified/:id/card (the decisions feed's shape, any state). */
export function summaryFromCard(d: Decision & { terminal?: boolean; batch?: BatchRecord | null }): CardSummary {
  const batch = d.batch && d.batch.parts?.length ? d.batch : null;
  const menuParts = (d.menu as { parts?: MenuPart[] } | null)?.parts;
  const parts = batch?.parts ?? (Array.isArray(menuParts) ? menuParts : null);
  const kind: RefCardKind = parts && parts.length > 1 ? 'multipart' : d.kind === 'menu' ? 'decision' : 'approval';
  const state = refState(d.state);
  const lines = state === 'answered' && batch ? batchAnswerLines(batch) : null;
  const answer = state !== 'answered' ? null : lines ? lines.map((l) => l.text).join('; ') : decisionAnswerText(d, kind);
  const question = (parts?.[0]?.question || d.question || d.menu?.question || d.summary || '').trim() || 'A question';
  return {
    id: d.id, kind, from: d.from_agent || null, state, question, answer: answer || null,
    ...(lines && lines.length > 1 ? { answerParts: lines.map((l) => ({ question: l.question || '', text: l.text })) } : {}),
    options: parts ? optionLabels(parts[0]?.options) : optionLabels(d.menu?.options),
    more: parts && parts.length > 1 ? parts.length - 1 : 0,
  };
}

/** A waiting row of GET /api/approvals/unified (what the Approvals page draws). */
export interface UnifiedRow {
  id: string; status: string; title: string; kind?: string | null; agent_id?: string | null;
  menu?: { options?: { label?: string }[]; question?: string; multipart?: boolean; fail_safe?: boolean } | null;
}

export function summaryFromUnified(r: UnifiedRow): CardSummary {
  const multi = !!(r.menu?.multipart || r.menu?.fail_safe);
  const kind: RefCardKind = multi ? 'multipart' : r.kind === 'menu' ? 'decision' : 'approval';
  return {
    id: r.id, kind, from: r.agent_id || null, state: refState(r.status),
    question: (r.menu?.question || r.title || '').trim() || 'A question', answer: null,
    options: multi ? [] : optionLabels(r.menu?.options), more: 0,
  };
}

/** A questionnaire's detail (GET /api/questionnaires/:id). */
export interface QuestionnaireDetail {
  id: string; title?: string; status: string; from_agent?: string;
  questions?: { prompt: string; menu?: { options?: { n?: string; label?: string }[] }; answer?: { option_n: string | null; answer_text: string | null; state?: string } | null }[];
}

export function summaryFromQuestionnaire(q: QuestionnaireDetail): CardSummary {
  const qs = q.questions ?? [];
  const state = refState(q.status);
  const answer = state !== 'answered' ? null : qs.map((x) => {
    const a = x.answer;
    if (!a || a.state === 'skipped') return null;
    const label = a.option_n ? (x.menu?.options ?? []).find((o) => o.n === a.option_n)?.label : undefined;
    const words = (a.answer_text || '').trim();
    return label && words ? `${label} — ${words}` : label || words || null;
  }).filter(Boolean).join('; ');
  return {
    id: q.id, kind: 'questionnaire', from: q.from_agent || null, state,
    question: (q.title || qs[0]?.prompt || '').trim() || 'A questionnaire', answer: answer || null,
    options: optionLabels(qs[0]?.menu?.options), more: Math.max(0, qs.length - 1),
  };
}

// ---- The chip's words ----------------------------------------------------------------------------------------------

/** The question's first words: whole words up to `max` characters, "…" when cut. Never an id. */
export function firstWords(text: string, max = 40): string {
  const t = scrubIds(text).replace(/[*_`#>]/g, '').replace(/\s+/g, ' ').trim();
  if (t.length <= max) return t;
  const cut = t.lastIndexOf(' ', max);
  return `${t.slice(0, cut > max / 2 ? cut : max).replace(/[\s,;:—-]+$/, '')}…`;
}

export const STATE_WORD: Record<RefCardState, string> = {
  waiting: 'Waiting', answered: 'Answered', dismissed: 'Dismissed', expired: 'Expired', withdrawn: 'Withdrawn',
  replaced: 'Replaced',
};

/** The state half of the chip: "Waiting", "Answered: Circle" (the answer's first words). */
export function chipState(c: Pick<CardSummary, 'state' | 'answer'>): string {
  if (c.state === 'answered' && c.answer) return `Answered: ${firstWords(c.answer, 24)}`;
  return STATE_WORD[c.state];
}

/** "Which size? · Answered: Circle". */
export function chipLabel(c: CardSummary): string {
  return `${firstWords(c.question)} · ${chipState(c)}`;
}

/** What the chip says before the card has loaded, or when it cannot be: the kind, never the id. */
export function placeholderLabel(id: string): string {
  return id.startsWith('qnr_') ? 'Questionnaire' : 'Card';
}

// ---- The card in words (calls, Telegram, a watch) -----------------------------------------------------------------

const KIND_WORD: Record<RefCardKind, string> = {
  decision: 'a question', approval: 'an approval', multipart: 'a question', questionnaire: 'a questionnaire', menu: 'a question',
  permission: 'for permission', choice: 'a question',
};

function list(xs: string[]): string {
  if (xs.length <= 1) return xs.join('');
  return `${xs.slice(0, -1).join(', ')} or ${xs[xs.length - 1]}`;
}

const ensureStop = (s: string) => (/[.?!…]$/.test(s) ? s : `${s}.`);

/** The card in plain words, for a surface that cannot show it: who asked, the question, where it stands, and the
 *  answer or what it asks. Never an id. */
export function describeCard(c: CardSummary): string {
  const who = c.from || 'An agent';
  const q = ensureStop(scrubIds(c.question).replace(/\s+/g, ' ').trim());
  const more = c.more > 0 ? ` (and ${c.more} more question${c.more === 1 ? '' : 's'})` : '';
  const head = c.kind === 'approval' ? `${who} asked for approval: ${q}` : `${who} asked ${KIND_WORD[c.kind]}: ${q}${more}`;
  switch (c.state) {
    case 'waiting': {
      if (c.kind === 'approval') return `${head} It is waiting for you to approve or deny it.`;
      const opts = c.options.length ? ` The options are ${list(c.options.map(scrubIds))}.` : '';
      return `${head} It is waiting for your answer.${opts}`;
    }
    case 'answered':
      if (c.answerParts?.length) {
        return `${head} Answered: ${c.answerParts.map((p) => `${ensureStop(scrubIds(p.question)).replace(/\.$/, ':')} ${scrubIds(p.text)}`).join('; ')}.`;
      }
      return c.answer ? `${head} Answered: ${ensureStop(scrubIds(c.answer))}` : `${head} It has been answered.`;
    case 'dismissed': return `${head} It was dismissed without an answer.`;
    case 'expired': return `${head} It expired without an answer.`;
    case 'withdrawn': return `${head} The agent withdrew it.`;
    case 'replaced': return `${head} A newer question replaced it.`;
  }
}
