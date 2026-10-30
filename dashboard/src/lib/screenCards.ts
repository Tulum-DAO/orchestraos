/**
 * screenCards.ts — which cards are ON SCREEN, for Arturo: the one context source every card renderer reports into
 * (the question card and the approval card, wherever they are drawn: the chat, a chip's popover, the drawer's
 * History, the Approvals page, the Inbox), so a new surface gets it for free. contract/notes/arturo-screen-context.md
 * defines the shape sent to him; the native apps build the same one.
 *
 * Each card reports what it is (kind, who it is from, its state, its question, options or answer) and, on a card of
 * several parts, the part on show with what is picked or typed so far. The hook (lib/threadKit/useScreenCard.ts) adds
 * whether it is in view (an IntersectionObserver), when it was last touched (a press, a key or focus inside it) and
 * when a card leaves (unmounted: navigation, the drawer closing, a popover closing).
 *
 * FOCUS, in order: an open popover (the newest), else the card touched last that is still in view, else the only
 * card in view. Several in view and none touched: no focus, and Arturo asks which, by their questions.
 *
 * What goes to Arturo is small: the focused card in full, every other card one line, at most MAX_SENT cards, each
 * under a short ref (c1, c2…) that only his tools use. No card id ever leaves the page in it.
 *
 * Pure (the reducer, focus, the payload, how an answer from Arturo maps onto a card) plus one tiny store at the end.
 */
import type { RefCardKind, RefCardState } from './cardRefs.ts';
import { firstWords } from './cardRefs.ts';
import { choose, letter, next, partRows, setWords, takesWords, type CardState, type MenuPart, type Step } from './multiPart.ts';

export type Surface = 'popover' | 'approvals' | 'history' | 'chat' | 'inbox' | 'questionnaires';

/** The part on show of a question card (a single question is part 1 of 1). Options carry their letters. */
export interface ScreenPart {
  n: number; of: number; question: string; select: 'single' | 'multi';
  options: string[]; picked: string[]; typed: string;
}

export interface ScreenCard {
  id: string;
  kind: RefCardKind;
  from: string | null;
  state: RefCardState;
  question: string;
  summary?: string;
  /** A card that is not a question card (an approval): what it offers. */
  options?: string[];
  answer?: string | null;
  part?: ScreenPart;
}

export interface Entry {
  key: string;
  card: ScreenCard;
  surface: Surface;
  visible: boolean;
  /** When it came into view (0 = not in view). */
  seenAt: number;
  /** When it was last pressed, typed in or focused (0 = never). */
  touchedAt: number;
  /** Report order: a newer popover wins over an older one. */
  seq: number;
}

export interface ScreenState { entries: Record<string, Entry>; seq: number }

export const EMPTY: ScreenState = { entries: {}, seq: 0 };

export type ScreenAction =
  | { type: 'report'; key: string; card: ScreenCard; surface: Surface }
  | { type: 'gone'; key: string }
  | { type: 'visible'; key: string; visible: boolean; now: number }
  | { type: 'touch'; key: string; now: number };

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

export function reduce(s: ScreenState, a: ScreenAction): ScreenState {
  const cur = s.entries[a.key];
  switch (a.type) {
    case 'report': {
      if (cur && cur.surface === a.surface && same(cur.card, a.card)) return s;
      const seq = cur ? cur.seq : s.seq + 1;
      const e: Entry = cur ? { ...cur, card: a.card, surface: a.surface }
        : { key: a.key, card: a.card, surface: a.surface, visible: false, seenAt: 0, touchedAt: 0, seq };
      return { entries: { ...s.entries, [a.key]: e }, seq: Math.max(s.seq, seq) };
    }
    case 'gone': {
      if (!cur) return s;
      const entries = { ...s.entries };
      delete entries[a.key];
      return { ...s, entries };
    }
    case 'visible': {
      if (!cur || cur.visible === a.visible) return s;
      return { ...s, entries: { ...s.entries, [a.key]: { ...cur, visible: a.visible, seenAt: a.visible ? a.now : 0 } } };
    }
    case 'touch': {
      if (!cur) return s;
      return { ...s, entries: { ...s.entries, [a.key]: { ...cur, touchedAt: a.now } } };
    }
  }
}

export type FocusWhy = 'popover' | 'touched' | 'only';

/** The cards in view, newest in view first; a card drawn twice at once (the drawer and the chat, or a compact
 *  History row and its opened card) once: the copy in a popover, else the fuller one, else the one touched or seen
 *  last. */
export function inView(s: ScreenState): Entry[] {
  // The fuller copy wins (the opened card over its compact summary, which carries no part), then the newer.
  const rank = (e: Entry) => [e.surface === 'popover' ? 1 : 0, e.card.part || e.card.options ? 1 : 0, e.touchedAt, e.seenAt, e.seq];
  const cmp = (a: Entry, b: Entry) => { const ra = rank(a), rb = rank(b); for (let i = 0; i < ra.length; i++) if (ra[i] !== rb[i]) return rb[i] - ra[i]; return 0; };
  const best = new Map<string, Entry>();
  for (const e of Object.values(s.entries)) {
    if (!e.visible) continue;
    const b = best.get(e.card.id);
    if (!b || cmp(e, b) < 0) best.set(e.card.id, e);
  }
  return [...best.values()].sort((a, b) => b.seenAt - a.seenAt || b.seq - a.seq);
}

/** The card in focus: an open popover, else the one touched last still in view, else the only one in view. */
export function focusOf(s: ScreenState): { entry: Entry; why: FocusWhy } | null {
  const shown = inView(s);
  const pops = shown.filter((e) => e.surface === 'popover').sort((a, b) => b.seq - a.seq);
  if (pops.length) return { entry: pops[0], why: 'popover' };
  const touched = shown.filter((e) => e.touchedAt > 0).sort((a, b) => b.touchedAt - a.touchedAt);
  if (touched.length) return { entry: touched[0], why: 'touched' };
  if (shown.length === 1) return { entry: shown[0], why: 'only' };
  return null;
}

// ---- What Arturo is sent --------------------------------------------------------------------------------------------

export const MAX_SENT = 8;

export interface SentCard {
  ref: string; kind?: string; from?: string; state?: string; surface?: string; question?: string; summary?: string;
  options?: string[]; answer?: string;
  part?: { n: number; of: number; question: string; select: 'single' | 'multi'; options?: string[]; picked?: string[]; typed?: string };
  line?: string;
}

export interface ScreenPayload { v: 1; focus: string | null; cards: SentCard[] }

const KIND_WORD: Record<RefCardKind, string> = {
  decision: 'Decision', approval: 'Approval', multipart: 'Multi-part question', questionnaire: 'Questionnaire', menu: 'Menu',
  permission: 'Permission request', choice: 'Choice card',
};

const clip = (t: string | null | undefined, n: number) => {
  const s = String(t ?? '').replace(/\s+/g, ' ').trim();
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
};

/** One line for a card that is not in focus: "Approval from dev-demo, waiting: Deploy the drawer?". */
export function briefLine(c: ScreenCard): string {
  const part = c.part && c.part.of > 1 ? ` (part ${c.part.n} of ${c.part.of}: ${firstWords(c.part.question, 60)})` : '';
  const ans = c.state === 'answered' && c.answer ? ` (answered: ${firstWords(c.answer, 40)})` : '';
  return `${KIND_WORD[c.kind]}${c.from ? ` from ${c.from}` : ''}, ${c.state}: ${firstWords(c.question, 90)}${part}${ans}`;
}

function full(ref: string, e: Entry): SentCard {
  const c = e.card;
  const out: SentCard = { ref, kind: c.kind, state: c.state, surface: e.surface, question: clip(c.question, 300) };
  if (c.from) out.from = c.from;
  if (c.summary && c.summary.trim() && c.summary.trim() !== c.question.trim()) out.summary = clip(c.summary, 400);
  if (c.options?.length) out.options = c.options.slice(0, 12).map((o) => clip(o, 100));
  if (c.answer) out.answer = clip(c.answer, 300);
  if (c.part) {
    const p = c.part;
    out.part = { n: p.n, of: p.of, question: clip(p.question, 300), select: p.select };
    if (p.options.length) out.part.options = p.options.slice(0, 12).map((o) => clip(o, 100));
    if (p.picked.length) out.part.picked = p.picked.slice(0, 12).map((o) => clip(o, 100));
    if (p.typed.trim()) out.part.typed = clip(p.typed, 300);
  }
  return out;
}

/** The context's `cards`: the focused card in full, the rest one line each, and which ref is which card (kept on
 *  the page to apply an answer Arturo hands back). Null when no card is in view. */
export function screenPayload(s: ScreenState): { payload: ScreenPayload; refs: Record<string, string> } | null {
  const shown = inView(s);
  if (!shown.length) return null;
  const f = focusOf(s);
  const ordered = f ? [f.entry, ...shown.filter((e) => e !== f.entry)] : shown;
  const refs: Record<string, string> = {};
  const cards = ordered.slice(0, MAX_SENT).map((e, i) => {
    const ref = `c${i + 1}`;
    refs[ref] = e.key;
    return f && e === f.entry ? full(ref, e) : { ref, line: briefLine(e.card) };
  });
  return { payload: { v: 1, focus: f ? 'c1' : null, cards }, refs };
}

/** The context card's words for the cards on screen: the focused card by the question on show (on a card of several
 *  parts, the current part's, as it pages); else how many are in view. Never an id. Null with no card in view. */
export function screenLabel(s: ScreenState): string | null {
  const f = focusOf(s);
  if (f) {
    const c = f.entry.card;
    return firstWords(c.part && c.part.of > 1 && c.part.question ? c.part.question : c.question, 36);
  }
  const n = inView(s).length;
  return n > 1 ? `${n} cards on screen` : null;
}

// ---- A question card's part, as reported ----------------------------------------------------------------------------

/** The part on show of a question card, as the context reports it: its options lettered as drawn, what is picked
 *  (by letter and label) and what is typed. */
export function partView(parts: MenuPart[], st: CardState): ScreenPart | undefined {
  const p = parts[st.at];
  if (!p) return undefined;
  const rows = partRows(p);
  const a = st.answers[st.at];
  return {
    n: st.at + 1, of: parts.length, question: p.question, select: p.select,
    options: rows.map((o, i) => `${letter(i)}. ${o.label}`),
    picked: rows.map((o, i) => (a?.ns.includes(o.n) ? `${letter(i)}. ${o.label}` : '')).filter(Boolean),
    typed: a?.text ?? '',
  };
}

// ---- An answer Arturo hands back ------------------------------------------------------------------------------------

/** What Arturo's answer_screen_card names: options by letter or label, and the person's own words. */
export interface ScreenAnswer { picks: string[]; words?: string }

/** The options a pick names on a part: "B", "b", "Circle", "B. Circle". Unknown picks are returned to say so. */
export function resolvePicks(part: MenuPart, picks: string[]): { ns: string[]; unknown: string[] } {
  const rows = partRows(part);
  const ns: string[] = [];
  const unknown: string[] = [];
  for (const raw of picks) {
    const t = String(raw ?? '').trim();
    if (!t) continue;
    const byLabel = (l: string) => rows.find((o) => o.label.trim().toLowerCase() === l.trim().toLowerCase());
    const byLetter = (c: string) => rows[c.toUpperCase().charCodeAt(0) - 65];
    const lettered = t.match(/^([A-Za-z])[.)]\s*(.+)$/);
    const hit = /^[A-Za-z]$/.test(t) ? byLetter(t)
      : lettered ? byLabel(lettered[2]) ?? byLetter(lettered[1])
        : byLabel(t);
    if (hit) { if (!ns.includes(hit.n)) ns.push(hit.n); } else unknown.push(t);
  }
  return { ns, unknown };
}

/** The card's own step for an answer on the part on show, exactly what the same taps and typing would do (so a
 *  multi-part card moves to its next part, and the last part sends); or why it cannot be applied. */
export function answerStep(parts: MenuPart[], st: CardState, a: ScreenAnswer): Step | { error: string } {
  const part = parts[st.at];
  if (!part) return { error: 'That card has nothing to answer.' };
  const { ns, unknown } = resolvePicks(part, a.picks ?? []);
  if (unknown.length) return { error: `There is no option ${unknown.join(', ')} on this question.` };
  const words = (a.words ?? '').trim();
  if (!ns.length && !words) return { error: 'Nothing to answer with.' };
  if (words && !takesWords(part)) return { error: 'This question takes a pick, not words.' };
  if (part.select === 'multi') {
    let s = st;
    for (const n of ns) if (!s.answers[s.at].ns.includes(n)) s = choose(parts, s, n).state;
    if (words) s = setWords(parts, s, words);
    return next(parts, s);
  }
  if (ns.length > 1) return { error: 'Pick one option on this question.' };
  if (ns.length && words && !part.pickAndWords) return { error: 'This question takes a pick or words, not both.' };
  const s = words ? setWords(parts, st, words) : st;
  return ns.length ? choose(parts, s, ns[0]) : next(parts, s);
}

// ---- The store ------------------------------------------------------------------------------------------------------

let state: ScreenState = EMPTY;
const subs = new Set<() => void>();
const answerers = new Map<string, (a: ScreenAnswer) => Promise<string | null> | string | null>();

export function screenDispatch(a: ScreenAction): void {
  const nextState = reduce(state, a);
  if (nextState === state) return;
  state = nextState;
  if (a.type === 'gone') answerers.delete(a.key);
  subs.forEach((fn) => { try { fn(); } catch { /* a bad subscriber must not break the others */ } });
}
export function getScreen(): ScreenState { return state; }
export function subscribeScreen(fn: () => void): () => void {
  subs.add(fn);
  return () => { subs.delete(fn); };
}

/** A card says how an answer is applied to it (its own submit path); the hook clears it when the card goes. */
export function setAnswerer(key: string, fn: ((a: ScreenAnswer) => Promise<string | null> | string | null) | null): void {
  if (fn) answerers.set(key, fn); else answerers.delete(key);
}

/** Apply an answer Arturo handed back to the card it named (by the key its ref stood for when the turn was sent).
 *  The card sends it through its own route, with its own send states. Returns what went wrong, or null. */
export async function applyScreenAnswer(key: string | undefined, a: ScreenAnswer): Promise<string | null> {
  const fn = key ? answerers.get(key) : undefined;
  if (!fn || !key || !state.entries[key]) return 'That card is no longer on your screen, so nothing was sent.';
  return (await fn(a)) ?? null;
}
