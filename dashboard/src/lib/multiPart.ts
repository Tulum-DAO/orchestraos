/**
 * multiPart.ts — the rules of the one question card (components/chat/MultiPartCard.tsx) every question is answered
 * with: an agent's multi-part menu (several questions on one screen, Claude Code's tabbed AskUserQuestion), an
 * agent's single menu, the decision cards agents post (Approvals, Inbox, the chat), questionnaires, and Arturo's
 * choice cards. A single question is a card of one part. Each kind answers through its own route; the converters at
 * the end of this file turn the card's answers into that route's body.
 *
 * The card shows one part at a time. Picking an option answers a single-select part and moves on; a multi-select
 * part takes ticks and its own Next. Skip leaves a part unanswered and moves on (not on a required part, and not on a
 * card of one part: there the ✕ does that job). Moving on from the last part sends every answer at once, so long as
 * one part is answered. On a single-select part of a menu blocking an agent in its terminal a pick and typed words
 * are exclusive; every other card takes both (pickAndWords). Pure, so tested here.
 */
import { canSendWords, visibleOptions, withInputKinds } from './decisionCard';

export interface MenuPartOption { n: string; label: string; detail?: string; input_kind?: string }
export interface MenuPart {
  index: number;
  question: string;
  select: 'single' | 'multi';
  options: MenuPartOption[];
  has_free_text?: boolean;
  tab_label?: string;
  /** Must be answered: no Skip (a questionnaire's required question). */
  required?: boolean;
  /** Multi-select: the option that stands alone ("None of these"); picking it clears the rest, and the reverse. */
  exclusive?: string;
  /** The option the agent leans to, marked on its row. */
  lean?: string;
  /** A single-choice part that takes a pick AND typed words together (a card that does not block an agent: a
   *  decision card, a questionnaire, Arturo's cards). Unset on a menu blocking an agent in its terminal, where the
   *  replay can deliver only one of them, so a pick and words are exclusive. */
  pickAndWords?: boolean;
}

export interface PartAnswer { ns: string[]; text: string; skipped: boolean }

/** Where the card is: every part's answer, and the part on show. */
export interface CardState { answers: PartAnswer[]; at: number }

/** What a step leads to: the new state, and whether every answer goes now. */
export interface Step { state: CardState; submit: boolean }

export function emptyAnswers(parts: MenuPart[]): PartAnswer[] {
  return parts.map(() => ({ ns: [], text: '', skipped: false }));
}

export function startState(parts: MenuPart[]): CardState {
  return { answers: emptyAnswers(parts), at: 0 };
}

/** The rows a part shows: its options without the write-in and chat rows (Claude Code's own "Type something." /
 *  "Chat about this", or any option of those kinds): the words field does their job. */
export function partRows(part: MenuPart): MenuPartOption[] {
  return visibleOptions(part.options).filter((o) => o.input_kind !== 'free_text' && o.input_kind !== 'chat');
}

/** A row's badge: A, B, C … in the order the rows are drawn. */
export function letter(i: number): string {
  return String.fromCharCode(65 + (i % 26));
}

/** Own words can only be typed where the menu has a write-in option. */
export function takesWords(part: MenuPart): boolean {
  return !!part.has_free_text || part.options.some((o) => o.input_kind === 'free_text');
}

export function answered(ans: PartAnswer | undefined): boolean {
  return !!ans && !ans.skipped && (ans.ns.length > 0 || ans.text.trim().length > 0);
}

export function skip(): PartAnswer {
  return { ns: [], text: '', skipped: true };
}

function withAnswer(s: CardState, a: PartAnswer): CardState {
  return { ...s, answers: s.answers.map((x, i) => (i === s.at ? a : x)) };
}

/** On from the part on show: to the next part, or, from the last, send — if anything is answered. */
function moveOn(parts: MenuPart[], s: CardState): Step {
  if (s.at < parts.length - 1) return { state: { ...s, at: s.at + 1 }, submit: false };
  return { state: s, submit: s.answers.some(answered) };
}

/** A click on an option. Single select: that option is the part's answer (replacing an earlier pick) and the card
 *  moves on. On a menu blocking an agent it clears typed words, since the replay can deliver only one of them; a
 *  card that takes both (pickAndWords) keeps them and sends them with the pick. Multi select: the
 *  tick toggles, in the menu's order, and the card stays; typed words are kept, since the replay types them into
 *  the write-in row and ticks the rest. */
export function choose(parts: MenuPart[], s: CardState, n: string): Step {
  const part = parts[s.at];
  const a = s.answers[s.at];
  if (part.select === 'multi') {
    let picked = a.ns.includes(n) ? a.ns.filter((x) => x !== n) : [...a.ns, n];
    if (part.exclusive && picked.includes(n)) picked = n === part.exclusive ? [n] : picked.filter((x) => x !== part.exclusive);
    const ns = part.options.map((o) => o.n).filter((x) => picked.includes(x));
    return { state: withAnswer(s, { ...a, ns, skipped: false }), submit: false };
  }
  return moveOn(parts, withAnswer(s, { ns: [n], text: part.pickAndWords ? a.text : '', skipped: false }));
}

/** Typing own words. On a single-choice part of a blocking menu the words are the answer instead of a pick, so
 *  typing clears it; a card that takes both keeps the pick. */
export function setWords(parts: MenuPart[], s: CardState, text: string): CardState {
  const a = s.answers[s.at];
  const single = parts[s.at]?.select !== 'multi' && !parts[s.at]?.pickAndWords;
  return withAnswer(s, { ns: single && text.trim() ? [] : a.ns, text, skipped: text.trim() ? false : a.skipped });
}

/** Next is offered once the part on show is answered (ticks, words, or an earlier pick seen again through Back). */
export function canNext(s: CardState): boolean {
  return answered(s.answers[s.at]);
}

export function next(parts: MenuPart[], s: CardState): Step {
  return canNext(s) ? moveOn(parts, s) : { state: s, submit: false };
}

/** Skip is offered on an optional part of a card with more than one part. */
export function canSkip(parts: MenuPart[], s: CardState): boolean {
  return parts.length > 1 && !parts[s.at]?.required;
}

/** Skip: this part stays unanswered (any earlier pick on it is cleared) and the card moves on. */
export function skipPart(parts: MenuPart[], s: CardState): Step {
  if (!canSkip(parts, s)) return { state: s, submit: false };
  return moveOn(parts, withAnswer(s, skip()));
}

/** Back shows the part before, with its answer still marked. */
export function back(s: CardState): CardState {
  return { ...s, at: Math.max(0, s.at - 1) };
}

/** Skipping the last part with nothing answered sends nothing: the card says so instead. */
export function nothingAnswered(s: CardState): boolean {
  return !s.answers.some(answered);
}

export type SubmitAnswer = { part: number; ns: string[]; text?: string } | { part: number; skipped: true };

/** The submit body's answers: every part, in order. A part left unanswered goes as an explicit skip, which the
 *  gateway records as skipped and steps past on the agent's screen (the card can only move past a part by answering
 *  or skipping it). */
export function submitAnswers(parts: MenuPart[], answers: PartAnswer[]): SubmitAnswer[] {
  return parts.map((p, i) => {
    const a = answers[i];
    if (!answered(a)) return { part: p.index, skipped: true };
    const text = takesWords(p) ? a.text.trim() : '';
    return { part: p.index, ns: a.ns, ...(text ? { text } : {}) };
  });
}

/** What an answered card shows for one part: only what was chosen, or that it was skipped. */
export interface PartOutcome { labels: string[]; words: string; skipped: boolean }

export function outcome(part: MenuPart, a: PartAnswer | undefined): PartOutcome {
  if (!answered(a)) return { labels: [], words: '', skipped: true };
  return {
    labels: a!.ns.map((n) => part.options.find((o) => o.n === n)?.label ?? n),
    words: a!.text.trim(),
    skipped: false,
  };
}

/** What a key does while the card has focus: A–Z pick that row on the part on show, Return moves on. Keys typed
 *  into a field, or on a focused button, are theirs; so are keys with a modifier. */
export function cardKey(parts: MenuPart[], s: CardState, key: string,
  opts: { inField?: boolean; onButton?: boolean; modifier?: boolean } = {}): Step | null {
  if (opts.modifier || opts.inField || opts.onButton) return null;
  if (key === 'Enter') return next(parts, s);
  const rows = parts[s.at] ? partRows(parts[s.at]) : [];
  const k = key.length === 1 ? key.toUpperCase().charCodeAt(0) - 65 : -1;
  return k >= 0 && k < rows.length ? choose(parts, s, rows[k].n) : null;
}

/** The one line under a card's parts: what went wrong, else why nothing can be sent yet, else a standing hint. The
 *  "can't send yet" note belongs to a card still waiting for input: once a send is under way (or done) it would
 *  contradict it, as when the server's card is already marked answered before the reply comes back. */
export function cardNote(o: { phase: 'answer' | 'sending' | 'done'; problem?: string | null; emptyNote?: string | null;
  waiting?: string | null; hint?: string | null }): string | null {
  if (o.problem) return o.problem;
  if (o.phase !== 'answer') return null;
  return o.emptyNote || o.waiting || o.hint || null;
}

/** Why the gateway could not read the menu, in plain words. */
export function captureProblem(reason: string | undefined): string {
  if (reason === 'not_on_part_zero') return 'The agent’s screen is partway through this question. Go back to its first part, or answer it in the terminal.';
  if (reason === 'menu_gone') return 'The question is gone from the agent’s screen.';
  return 'Couldn’t read every part of this question. Try again, or answer it in the terminal.';
}

/** An answered AskUserQuestion in an agent's transcript, read back into parts and answers so the thread can keep
 *  the answered card. The call's input has the questions (the CLI requires their texts to be unique, and refuses the
 *  call otherwise, so duplicates mean there is nothing to draw).
 *
 *  Where the gateway's durable record of the answer exists (`record`, matched by matchBatchRecord), its answers are
 *  used as stored: skipped parts are marked skipped there, and option numbers need no parsing. Otherwise the result
 *  text is read: it names each answer as "question"="answer", several picks joined by ", ". Answers are found by the
 *  question texts the input already gives (so a quote or comma inside an answer cannot split it), and a pick-any
 *  answer is split by the part's own option labels (so a label containing ", " stays whole); anything left over is
 *  the person's own words. A question the result does not name was skipped. Null when the call is not one this card
 *  can draw (no questions, duplicate questions, or no result yet). */
export function fromAskUserQuestion(input: Record<string, unknown> | undefined, result: string | undefined,
  record?: BatchRecord | null): { parts: MenuPart[]; answers: PartAnswer[] } | null {
  const qs = Array.isArray(input?.questions) ? (input!.questions as Record<string, unknown>[]) : [];
  if (!qs.length || !result) return null;
  const texts = qs.map((q) => String(q.question ?? ''));
  if (new Set(texts).size !== texts.length) return null;
  const parts: MenuPart[] = qs.map((q, i) => ({
    index: i,
    question: texts[i],
    select: q.multiSelect ? 'multi' : 'single',
    options: (Array.isArray(q.options) ? q.options as Record<string, unknown>[] : []).map((o, k) => ({
      n: String(k + 1), label: String(o.label ?? ''), ...(o.description ? { detail: String(o.description) } : {}),
    })),
    has_free_text: true,
  }));
  if (record) {
    const fromRecord = answersFromRecord(parts, record);
    if (fromRecord) return { parts, answers: fromRecord };
  }
  const said = answersInResult(texts, result);
  const answers = parts.map((p, i): PartAnswer => {
    const a = said[i];
    if (a === undefined || !a.trim()) return skip();
    const whole = p.options.find((o) => o.label === a);
    if (whole) return { ns: [whole.n], text: '', skipped: false };
    const { ns, rest } = splitByLabels(a, p.options);
    return { ns, text: rest, skipped: false };
  });
  return { parts, answers };
}

/** Each question's answer string in the result text, by index; undefined where the question is not named. */
function answersInResult(questions: string[], result: string): (string | undefined)[] {
  const marks = questions.map((q) => {
    const m = `"${q}"="`;
    const at = result.indexOf(m);
    return at < 0 ? null : { start: at + m.length, mark: at };
  });
  return marks.map((m) => {
    if (!m) return undefined;
    // The answer runs to the next question's marker (less its ", " joiner), else to the last quote in the text.
    const nexts = marks.filter((o) => o && o.mark > m.start).map((o) => o!.mark);
    let end = nexts.length ? Math.min(...nexts) : result.lastIndexOf('"');
    if (nexts.length && result.slice(end - 2, end) === ', ') end -= 2;
    if (nexts.length && result[end - 1] === '"') end -= 1;
    return end > m.start ? result.slice(m.start, end) : '';
  });
}

/** Option labels at the front of a pick-any answer, joined by ", " (longest label first, so "Red, white" beats
 *  "Red"); the remainder is own words. */
function splitByLabels(answer: string, options: MenuPartOption[]): { ns: string[]; rest: string } {
  const byLength = [...options].sort((a, b) => b.label.length - a.label.length);
  const ns: string[] = [];
  let pos = 0;
  while (pos < answer.length) {
    const hit = byLength.find((o) => o.label && answer.startsWith(o.label, pos)
      && (pos + o.label.length === answer.length || answer.startsWith(', ', pos + o.label.length)));
    if (!hit) break;
    if (!ns.includes(hit.n)) ns.push(hit.n);
    pos += hit.label.length + 2;
  }
  const rest = pos < answer.length ? answer.slice(pos) : '';
  return { ns: options.map((o) => o.n).filter((n) => ns.includes(n)), rest };
}

/** The gateway's durable record of a multi-part answer (approval_requests, answer='batch'): its hydrated parts as
 *  captured from the screen, and the batch as submitted. */
export interface BatchRecord {
  id: string;
  answered_at?: string | null;
  parts: MenuPart[];
  answers: { part: number; ns?: string[]; text?: string; skipped?: boolean }[];
}

const qNorm = (q: string) => q.toLowerCase().replace(/\s+/g, ' ').trim();

/** The durable record for an AskUserQuestion, matched by its questions: same number of parts, and each captured
 *  question contained in the asked one or the other way round (the capture is the screen's text, which can be
 *  rewrapped or cut short). The newest match wins. */
export function matchBatchRecord(records: BatchRecord[] | undefined, questions: string[]): BatchRecord | null {
  const want = questions.map(qNorm);
  const hits = (records ?? []).filter((r) => r.parts.length === want.length && r.parts.every((p, i) => {
    const got = qNorm(p.question || '');
    return !!got && (want[i].includes(got) || got.includes(want[i]));
  }));
  hits.sort((a, b) => String(b.answered_at ?? '').localeCompare(String(a.answered_at ?? '')));
  return hits[0] ?? null;
}

/** A record's answers onto the asked parts: option numbers mapped by LABEL from the captured part to the asked one
 *  (the capture adds the CLI's own rows, so its numbers need not line up). Null if a pick cannot be placed. */
function answersFromRecord(parts: MenuPart[], record: BatchRecord): PartAnswer[] | null {
  const out: PartAnswer[] = [];
  for (let i = 0; i < parts.length; i++) {
    const a = record.answers.find((x) => x.part === record.parts[i]?.index);
    if (!a || a.skipped) { out.push(skip()); continue; }
    const ns: string[] = [];
    for (const n of a.ns ?? []) {
      const label = record.parts[i].options.find((o) => o.n === String(n))?.label;
      const asked = parts[i].options.find((o) => o.label === label);
      if (asked) ns.push(asked.n);
      else if (!(record.parts[i].options.find((o) => o.n === String(n))?.input_kind === 'free_text')) return null;
    }
    out.push({ ns, text: (a.text ?? '').trim(), skipped: false });
  }
  return out;
}


// ---- Each card kind's parts, and its answers in its route's body ----------------------------------------------

export interface PlainOption { n: string; label: string; detail?: string; input_kind?: string }

/** A card of one part: an agent's single menu, or a decision card it posted. `words`: typed words alone can be sent
 *  (the menu has a write-in; a permission prompt has none). */
export function onePart(question: string, options: PlainOption[], opts: { words: boolean; multi?: boolean; exclusive?: string; lean?: string | null; pickAndWords?: boolean }): MenuPart[] {
  return [{
    index: 0, question, select: opts.multi ? 'multi' : 'single',
    options: options.map((o) => ({ n: o.n, label: o.label, ...(o.detail ? { detail: o.detail } : {}), ...(o.input_kind ? { input_kind: o.input_kind } : {}) })),
    has_free_text: opts.words,
    ...(opts.exclusive ? { exclusive: opts.exclusive } : {}),
    ...(opts.lean ? { lean: opts.lean } : {}),
    ...(opts.pickAndWords ? { pickAndWords: true } : {}),
  }];
}

/** A decision card (kind 'menu'), as a card of one part: words alone only where it has a write-in. One an agent
 *  posted takes a pick and words together; one bridged from an agent's terminal menu (`terminal`, lib/decisionCard
 *  isTerminalMenu) takes one or the other. */
export function decisionParts(row: { question: string; options: PlainOption[]; lean?: string | null; terminal?: boolean }): MenuPart[] {
  const options = withInputKinds(row.options);
  return onePart(row.question, options, { words: canSendWords(options) || !row.terminal, lean: row.lean ?? null, pickAndWords: !row.terminal });
}

/** One part's answer as a single menu's, decision card's or questionnaire question's body: the picked option, the
 *  words, or both where the card took both (the rules above keep a blocking menu to one). Null when nothing is
 *  answered. */
export function singleBody(a: PartAnswer | undefined): { option_n?: string; answer_text?: string } | null {
  if (!answered(a)) return null;
  const text = a!.text.trim();
  const out: { option_n?: string; answer_text?: string } = {};
  if (a!.ns.length) out.option_n = a!.ns[0];
  if (text) out.answer_text = text;
  return out.option_n || out.answer_text ? out : null;
}

/** An answered single menu or decision card, from what was stored (option_n / answer_text). */
export function answerFromStored(part: MenuPart, optionN: string | null | undefined, text: string | null | undefined): PartAnswer {
  const words = (text || '').trim();
  const opt = optionN ? part.options.find((o) => o.n === String(optionN)) : undefined;
  // A write-in option's number with words is the words (the row is not drawn).
  const ns = opt && opt.input_kind !== 'free_text' && partRows(part).some((o) => o.n === opt.n) ? [opt.n] : [];
  if (!ns.length && !words) return skip();
  return { ns, text: words, skipped: false };
}

export interface QuestionnaireQuestion {
  n: number; prompt: string; kind: string; required: number | boolean;
  menu?: { options?: PlainOption[] };
  answer: { option_n: string | null; answer_text: string | null; state?: string } | null;
}

/** A questionnaire's questions as parts. Text alone answers any question (the gateway's ruling), so every part takes
 *  words; a free-text question is words only. Chat options are refused in questionnaires and never drawn. */
export function questionnaireParts(questions: QuestionnaireQuestion[]): MenuPart[] {
  return questions.map((q) => ({
    index: q.n, question: q.prompt, select: 'single',
    options: q.kind === 'free_text' ? [] : (q.menu?.options ?? []).filter((o) => (o.input_kind ?? 'direct') !== 'chat')
      .map((o) => ({ n: o.n, label: o.label, ...(o.detail ? { detail: o.detail } : {}), ...(o.input_kind ? { input_kind: o.input_kind } : {}) })),
    has_free_text: true,
    pickAndWords: true,
    required: !!q.required,
  }));
}

/** A questionnaire's saved draft or answers as the card's answers (a pick and words together are kept). */
export function questionnaireAnswers(parts: MenuPart[], questions: QuestionnaireQuestion[]): PartAnswer[] {
  return parts.map((p, i) => {
    const a = questions[i]?.answer;
    if (!a) return { ns: [], text: '', skipped: false };
    if (a.state === 'skipped') return skip();
    const got = answerFromStored(p, a.option_n, a.answer_text);
    return got.skipped ? { ns: [], text: '', skipped: false } : got;
  });
}

/** The questionnaire submit body (POST /api/questionnaires/:id/answers): {n: {option_n?, answer_text?} |
 *  {skipped: true}}. A required part left unanswered is left out (the gateway then names it as missing). */
export function questionnaireBody(parts: MenuPart[], answers: PartAnswer[]): Record<string, { option_n?: string; answer_text?: string; skipped?: true }> {
  const out: Record<string, { option_n?: string; answer_text?: string; skipped?: true }> = {};
  parts.forEach((p, i) => {
    const body = singleBody(answers[i]);
    if (body) out[String(p.index)] = body;
    else if (!p.required) out[String(p.index)] = { skipped: true };
  });
  return out;
}

/** An answered choice card (Arturo's ask_choices) from the words it sent: the picked labels joined by ", ", then
 *  " — " and the person's own words (lib/choiceCard choiceAnswerText), or words alone. */
export function choiceAnswerFromText(part: MenuPart, sent: string): PartAnswer {
  const text = sent.trim();
  if (!text) return skip();
  const whole = part.options.find((o) => o.label === text);
  if (whole) return { ns: [whole.n], text: '', skipped: false };
  const cut = text.indexOf(' — ');
  const head = cut >= 0 ? text.slice(0, cut) : text;
  const { ns, rest } = splitByLabels(head, part.options);
  if (ns.length && !rest) return { ns: part.select === 'multi' ? ns : ns.slice(0, 1), text: cut >= 0 ? text.slice(cut + 3).trim() : '', skipped: false };
  return { ns: [], text, skipped: false };
}
