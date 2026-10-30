/**
 * drawerHistory.ts — the drawer's History section as cards (components/arturo/DrawerHistory.tsx): what is running,
 * what is waiting on the operator, and what was resolved and how. Pure, so tested without a browser.
 *
 * The cards come from the feeds the chat already reads: the agent's decisions (GET /api/agents/:id/decisions:
 * decision cards, approvals, and the one-line records of in-agent menus answered in the terminal) and its recorded
 * multi-part answers (GET /api/agents/:id/menu-batches, skipped parts included). Nothing here answers anything:
 * a waiting card is the chat's own card, answered through its own route.
 *
 * Waiting runs oldest first (the one that has waited longest is the one to answer first, as the bell does);
 * Resolved runs newest first and is shown 20 at a time.
 */
import { chosenText, decisionTone, deliveryText, type Decision } from './decisions.ts';
import { answered, outcome, partRows, skip, type BatchRecord, type MenuPart, type PartAnswer } from './multiPart.ts';
import { operatorUserId } from './runtimeConfig.ts';
import type { ArturoJob } from './arturoJobs.ts';
import type { ThreadTurn } from './arturoThreads.ts';
import { storedAnswerText } from './choiceCard.ts';

/** What a card is: a decision (a question card an agent posted), an approval (Approve / Deny), a menu answered in
 *  the agent's own terminal (a one-line record), a multi-part question (several parts on one card), or one of
 *  Arturo's choice cards (ask_choices, kept in his thread with its answer). */
export type HistoryKind = 'decision' | 'approval' | 'menu' | 'multipart' | 'choice';
export type HistoryState = 'waiting' | 'resolved';
export type HistoryOutcome = 'answered' | 'approved' | 'denied' | 'held' | 'dismissed' | 'expired' | 'withdrawn' | 'replaced';

export const KIND_LABEL: Record<HistoryKind, string> = {
  decision: 'Decision', approval: 'Approval', menu: 'Menu', multipart: 'Multi-part', choice: 'Choice',
};
export const KINDS: HistoryKind[] = ['decision', 'approval', 'menu', 'multipart', 'choice'];

export const OUTCOME_LABEL: Record<HistoryOutcome, string> = {
  answered: 'Answered', approved: 'Approved', denied: 'Denied', held: 'Answered · not delivered yet',
  dismissed: 'Dismissed', expired: 'Expired', withdrawn: 'Withdrawn', replaced: 'Replaced',
};

/** One line of an answer: the part's question on a multi-part card (none on a card of one part), and what was
 *  chosen and said, or that it was skipped. */
export interface AnswerLine { question?: string; text: string; skipped: boolean }

export interface HistoryItem {
  id: string;
  kind: HistoryKind;
  state: HistoryState;
  /** How it ended; null while it waits. */
  outcome: HistoryOutcome | null;
  question: string;
  summary: string;
  agent: string;
  createdAt: number;
  answeredAt: number | null;
  /** The answer, part by part; empty when nothing was answered (dismissed, expired, withdrawn, replaced). */
  answer: AnswerLine[];
  /** Who answered: "you", or the agent that answered its own gate. Null when nobody did. */
  by: string | null;
  /** Where it was answered ("the dashboard"); null when unknown. */
  where: string | null;
  /** How fast the answer reached the agent ("delivered in 2s"), '' when unknown. */
  delivery: string;
  /** The chat draws this card (an element `decision-<id>`), so "Go to card" can scroll to it. */
  inChat: boolean;
  decision?: Decision;
  batch?: BatchRecord;
  /** One of Arturo's choice cards: its options, and the words its answer reads as (lib/choiceCard storedAnswerText). */
  choice?: { options: string[]; multi: boolean; exclusive?: string; answered: string | null };
}

const SURFACE: Record<string, string> = {
  web: 'the dashboard', phone: 'the phone app', watch: 'the watch', gateway: 'an app',
  agent_cli: "the agent's terminal", arturo: 'the assistant',
};

const ms = (iso: string | null | undefined): number | null => {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isNaN(t) ? null : t;
};

export function historyKind(d: Pick<Decision, 'kind'>): HistoryKind {
  if (d.kind === 'menu_answer') return 'menu';
  return d.kind === 'menu' ? 'decision' : 'approval';
}

/** How a card ended, from the feed's state (the same reading as the chat card's outline, lib/decisions decisionTone). */
export function outcomeOf(d: Decision): HistoryOutcome | null {
  if (d.kind === 'menu_answer') return d.answer === 'dismiss' ? 'dismissed' : 'answered';
  switch (d.state) {
    case 'pending': return null;
    case 'expired': return 'expired';
    case 'withdrawn': return 'withdrawn';
    case 'dismissed': return 'dismissed';
    case 'superseded': return 'replaced';
    case 'answered': {
      const t = decisionTone(d);
      return t === 'approved' || t === 'denied' || t === 'held' ? t : 'answered';
    }
    default: return 'answered';
  }
}

/** Who answered: you (this install's operator, or nobody named), else the agent that answered. */
export function answeredBy(d: Pick<Decision, 'answered_by'>): string {
  return !d.answered_by || d.answered_by === operatorUserId() ? 'you' : d.answered_by;
}

/** THE ANSWER of a card of one part: the option picked and any words, an approval's verb and its reason. */
export function decisionAnswer(d: Decision): AnswerLine[] {
  const o = outcomeOf(d);
  if (o === null || o === 'dismissed' || o === 'expired' || o === 'withdrawn' || o === 'replaced') return [];
  if (historyKind(d) === 'approval') {
    const a = (d.answer || '').toLowerCase();
    const verb = a === 'approve' || a === 'accept' ? 'Approved' : a === 'deny' || a === 'reject' ? 'Denied' : '';
    const reason = (d.answer_text || '').trim();
    if (verb) return [{ text: reason ? `${verb} — ${reason}` : verb, skipped: false }];
  }
  if (historyKind(d) === 'menu') {
    const opt = d.option_n ? (d.menu?.options || []).find((x) => x.n === d.option_n) : undefined;
    const words = (d.answer_text || '').trim();
    const label = opt?.label && !(words && /write-in|other|type something/i.test(opt.label)) ? opt.label : '';
    const text = label && words ? `${label} — ${words}` : label || words || (d.option_n ? `Option ${d.option_n}` : 'Answered');
    return [{ text, skipped: false }];
  }
  return [{ text: chosenText(d), skipped: false }];
}

/** A recorded multi-part answer onto its own parts, in the card's terms (skips kept as skips). Option numbers of
 *  the write-in row are not picks: its words are the answer. Ticks in the menu's order. */
export function batchAnswers(rec: BatchRecord): PartAnswer[] {
  return rec.parts.map((p) => {
    const a = rec.answers.find((x) => x.part === p.index);
    if (!a || a.skipped) return skip();
    const picked = new Set((a.ns ?? []).map(String));
    const ns = partRows(p).map((o) => o.n).filter((n) => picked.has(n));
    const got = { ns, text: (a.text ?? '').trim(), skipped: false };
    return answered(got) ? got : skip();
  });
}

/** THE ANSWER of a multi-part card: one line per part, each with its question, skips said as skipped. */
export function batchAnswerLines(rec: BatchRecord): AnswerLine[] {
  const answers = batchAnswers(rec);
  return rec.parts.map((p: MenuPart, i) => {
    const out = outcome(p, answers[i]);
    if (out.skipped) return { question: p.question, text: 'Skipped', skipped: true };
    const picks = out.labels.join(', ');
    return { question: p.question, text: picks && out.words ? `${picks} — ${out.words}` : picks || out.words, skipped: false };
  });
}

export function fromDecision(d: Decision): HistoryItem {
  const o = outcomeOf(d);
  const answeredAt = ms(d.answered_at);
  const nobody = o === null || o === 'expired' || o === 'withdrawn' || o === 'replaced';
  return {
    id: d.id, kind: historyKind(d), state: o === null ? 'waiting' : 'resolved', outcome: o,
    question: (d.kind === 'menu_answer' ? d.question : d.question || d.summary || '').trim() || 'Decision',
    summary: (d.summary || '').trim(), agent: d.from_agent,
    createdAt: ms(d.created_at) ?? 0, answeredAt: o === null ? null : answeredAt,
    answer: decisionAnswer(d),
    by: nobody ? null : answeredBy(d),
    where: d.answer_surface && SURFACE[d.answer_surface] ? SURFACE[d.answer_surface] : null,
    delivery: deliveryText(d),
    inChat: true, decision: d,
  };
}

export function fromBatch(rec: BatchRecord, agent: string): HistoryItem {
  const at = ms(rec.answered_at);
  const first = rec.parts[0]?.question?.trim() || 'Question';
  return {
    id: rec.id, kind: 'multipart', state: 'resolved', outcome: 'answered',
    question: rec.parts.length > 1 ? `${first} (+${rec.parts.length - 1} more)` : first,
    summary: '', agent, createdAt: at ?? 0, answeredAt: at,
    answer: batchAnswerLines(rec), by: 'you', where: null, delivery: '',
    // Drawn in the chat as the answered question in the agent's own turn, not as a card with an id.
    inChat: false, batch: rec,
  };
}

/** The question a choice card asks: the reply it came with says it (the card itself holds only options), so its
 *  last line that asks something, else its last line, cut to a sentence's length. */
export function choiceQuestion(reply: string): string {
  const lines = String(reply || '').split('\n').map((l) => l.replace(/[*_`#>]/g, '').trim()).filter(Boolean);
  const asked = [...lines].reverse().find((l) => l.includes('?'));
  const line = asked ? asked.slice(0, asked.lastIndexOf('?') + 1) : lines[lines.length - 1] || '';
  const sentence = line.split(/(?<=[.!])\s+/).pop() || line;
  const q = sentence.length > 140 ? `${sentence.slice(0, 139).trimEnd()}…` : sentence;
  return q || 'Pick one';
}

/** One of Arturo's choice cards, from the thread turn that showed it (meta kind "card"); null for any other turn.
 *  Answered from its own record on the server (picks and words, and when); closed unanswered = dismissed. */
export function fromChoiceTurn(t: ThreadTurn, agent: string): HistoryItem | null {
  const m = t.meta;
  if (t.role === 'user' || m?.kind !== 'card' || !m.card_id || !m.choices) return null;
  const answered = m.answer ? storedAnswerText(m.choices.options, m.answer) : null;
  const outcome: HistoryOutcome | null = m.dismissed ? 'dismissed' : answered ? 'answered' : null;
  const at = (m.answer?.at ?? null) !== null ? m.answer!.at! * 1000 : null;
  return {
    id: m.card_id, kind: 'choice', state: outcome ? 'resolved' : 'waiting', outcome,
    question: choiceQuestion(t.content), summary: m.choices.note || '', agent,
    createdAt: (t.ts ?? 0) * 1000, answeredAt: outcome === 'answered' ? at ?? (t.ts ?? 0) * 1000 : null,
    answer: answered ? [{ text: answered, skipped: false }] : [], by: answered ? 'you' : null, where: null, delivery: '',
    inChat: true,
    choice: { options: m.choices.options, multi: !!m.choices.multi, ...(m.choices.exclusive ? { exclusive: m.choices.exclusive } : {}), answered },
  };
}

/** Every card the agent has, from its feeds (and, for Arturo, the choice cards in his open conversation). */
export function historyItems(decisions: Decision[], batches: BatchRecord[], agent: string, turns: ThreadTurn[] = [],
  /** Who asks on those choice cards, by name (the assistant's name on this install). */
  asker = agent): HistoryItem[] {
  const seen = new Set<string>();
  const out: HistoryItem[] = [];
  for (const d of decisions) { if (!seen.has(d.id)) { seen.add(d.id); out.push(fromDecision(d)); } }
  for (const b of batches) { if (!seen.has(b.id)) { seen.add(b.id); out.push(fromBatch(b, agent)); } }
  for (const t of turns) {
    const c = fromChoiceTurn(t, asker);
    if (c && !seen.has(c.id)) { seen.add(c.id); out.push(c); }
  }
  return out;
}

const when = (i: HistoryItem) => i.answeredAt ?? i.createdAt;

/** Waiting oldest first; Resolved newest first. */
export function groupHistory(items: HistoryItem[]): { waiting: HistoryItem[]; resolved: HistoryItem[] } {
  const waiting = items.filter((i) => i.state === 'waiting').sort((a, b) => a.createdAt - b.createdAt || a.id.localeCompare(b.id));
  const resolved = items.filter((i) => i.state === 'resolved').sort((a, b) => when(b) - when(a) || a.id.localeCompare(b.id));
  return { waiting, resolved };
}

// ---- Filters and paging ---------------------------------------------------------------------------------------

export type StateFilter = 'all' | HistoryState;
export interface HistoryFilter { kind: 'all' | HistoryKind; state: StateFilter }
export const NO_FILTER: HistoryFilter = { kind: 'all', state: 'all' };

export function applyFilter(items: HistoryItem[], f: HistoryFilter): HistoryItem[] {
  return items.filter((i) => (f.kind === 'all' || i.kind === f.kind) && (f.state === 'all' || i.state === f.state));
}

export interface Chip<T extends string> { id: T; label: string; count: number }

/** The filter chips: the states (All, Waiting, Resolved), counted under the kind filter; and All kinds, then only the
 *  kinds this agent has (a chip that can only show nothing is noise), each counted under the state filter. No kind
 *  row when there is only one kind. */
export function filterChips(items: HistoryItem[], f: HistoryFilter): { kinds: Chip<'all' | HistoryKind>[]; states: Chip<StateFilter>[] } {
  const byState = applyFilter(items, { kind: 'all', state: f.state });
  const kinds: Chip<'all' | HistoryKind>[] = [{ id: 'all', label: 'All kinds', count: byState.length }];
  for (const k of KINDS) {
    const n = byState.filter((i) => i.kind === k).length;
    if (n || f.kind === k) kinds.push({ id: k, label: KIND_LABEL[k], count: n });
  }
  const byKind = applyFilter(items, { kind: f.kind, state: 'all' });
  const states: Chip<StateFilter>[] = [
    { id: 'all', label: 'All', count: byKind.length },
    { id: 'waiting', label: 'Waiting', count: byKind.filter((i) => i.state === 'waiting').length },
    { id: 'resolved', label: 'Resolved', count: byKind.filter((i) => i.state === 'resolved').length },
  ];
  // One kind and no kind chosen: the row could only repeat the state row's count.
  return { kinds: kinds.length <= 2 && f.kind === 'all' ? [] : kinds, states };
}

export const PAGE_SIZE = 20;

/** The first `limit` items, and how many more Show more would bring (at most a page). */
export function pageOf<T>(items: T[], limit: number): { shown: T[]; more: number; rest: number } {
  const shown = items.slice(0, Math.max(0, limit));
  const rest = items.length - shown.length;
  return { shown, rest, more: Math.min(PAGE_SIZE, rest) };
}

// ---- Words for the card -------------------------------------------------------------------------------------------

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const hhmm = (d: Date) => `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
const sameDay = (a: Date, b: Date) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();

/** When, in local time: "14:05" today, "Yesterday 14:05", "Oct 28, 14:05" this year, "Oct 28 2025" before. */
export function whenText(at: number | null, now = Date.now()): string {
  if (!at) return '';
  const d = new Date(at), n = new Date(now);
  if (sameDay(d, n)) return hhmm(d);
  const y = new Date(n.getFullYear(), n.getMonth(), n.getDate() - 1);
  if (sameDay(d, y)) return `Yesterday ${hhmm(d)}`;
  if (d.getFullYear() === n.getFullYear()) return `${MONTHS[d.getMonth()]} ${d.getDate()}, ${hhmm(d)}`;
  return `${MONTHS[d.getMonth()]} ${d.getDate()} ${d.getFullYear()}`;
}

/** How long it has waited: "just now", "12m", "3h", "2d". */
export function waitedText(at: number, now = Date.now()): string {
  const m = Math.floor(Math.max(0, now - at) / 60_000);
  if (m < 1) return 'just now';
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  return h < 24 ? `${h}h` : `${Math.floor(h / 24)}d`;
}

/** A waiting card's line: "pm-demo · waiting 3h". */
export function waitingLine(i: HistoryItem, now = Date.now()): string {
  const w = waitedText(i.createdAt, now);
  return `${i.agent} · ${w === 'just now' ? 'just now' : `waiting ${w}`}`;
}

/** A resolved card's line: who, where, when (and how fast it reached the agent): "you · the dashboard · 14:05". */
export function resolvedLine(i: HistoryItem, now = Date.now()): string {
  const bits = [i.by, i.where, whenText(i.answeredAt ?? i.createdAt, now), i.delivery].filter(Boolean);
  return bits.join(' · ');
}

/** THE ANSWER in one string, for a compact card or a screen reader: parts joined by "; ". */
export function answerText(lines: AnswerLine[]): string {
  return lines.map((l) => (l.question ? `${l.question}: ${l.text}` : l.text)).join('; ');
}

// ---- Running ------------------------------------------------------------------------------------------------------

export interface RunningRow { id: string; title: string; detail: string; cancellable: boolean }

/** What is running beside the agent's own turn: Arturo's background jobs (each can be cancelled: they have a
 *  route), or an agent's subagents, which are only counted and have no cancel route, so none is offered. */
export function runningRows(o: { jobs?: ArturoJob[] | null; subagents?: number | null; name: string; elapsed?: (j: ArturoJob) => string }): RunningRow[] {
  if (o.jobs) {
    return o.jobs.filter((j) => j.status === 'running')
      .map((j) => ({ id: j.id, title: j.title || j.kind, detail: o.elapsed ? o.elapsed(j) : '', cancellable: true }));
  }
  const n = o.subagents ?? 0;
  return n > 0 ? [{ id: 'subagents', title: `${n} subagent${n === 1 ? '' : 's'} working`, detail: `for ${o.name}`, cancellable: false }] : [];
}

/** A card's text as plain words for its two-line preview: Markdown marks, list bullets and line breaks dropped. */
export function plainSummary(text: string | null | undefined): string {
  return String(text || '')
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/^[ \t]*(#{1,6}|[-*+]|\d+\.)[ \t]+/gm, '')
    .replace(/\*\*|__|`/g, '')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/\s+/g, ' ')
    .trim();
}
