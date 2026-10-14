/**
 * The logic of the native questionnaire form (components/QuestionnaireForm.tsx).
 *
 * A LIVE questionnaire is one the gateway's QuestionnaireStore holds (every one `approval.py
 * questionnaire` makes; ids start with qnr_). The API serves its questions[] (GET
 * /api/questionnaires/:id) and takes answers at POST /api/questionnaires/:id/answers, the same
 * contract the phone and watch use. Legacy file questionnaires keep their iframe.
 */

export interface LiveOption { n: string; label: string; detail?: string; input_kind?: string }
export interface LiveQuestion {
  n: number;
  prompt: string;
  kind: 'menu' | 'free_text' | string;
  required: number | boolean;
  menu?: { options?: LiveOption[] };
  answer: { option_n: string | null; answer_text: string | null; state?: string } | null;
}
export interface Draft { option_n?: string; answer_text?: string }
export type Drafts = Record<number, Draft>;

export function isLiveQuestionnaire(q: { id: string; source?: string }): boolean {
  return q.source === 'live' || /^qnr_/.test(q.id);
}

/** The options the operator can pick. "chat" options open a conversation in a pane; the gateway
 *  refuses them in questionnaires, so they are not offered. */
export function answerableOptions(q: LiveQuestion): LiveOption[] {
  return (q.menu?.options ?? []).filter((o) => (o.input_kind ?? 'direct') !== 'chat');
}

function option(q: LiveQuestion, n: string | undefined): LiveOption | undefined {
  return n === undefined ? undefined : (q.menu?.options ?? []).find((o) => o.n === n);
}

/** The request body's answers: only questions with something given (an option, text, or both). */
export function buildAnswers(drafts: Drafts): Record<string, Draft> {
  const out: Record<string, Draft> = {};
  for (const [n, d] of Object.entries(drafts)) {
    const text = (d.answer_text ?? '').trim();
    const a: Draft = {};
    if (d.option_n) a.option_n = d.option_n;
    if (text) a.answer_text = text;
    if (a.option_n || a.answer_text) out[n] = a;
  }
  return out;
}

/** Required questions still unanswered. Text alone answers any question (the text-is-a-valid-
 *  answer ruling); a picked option that asks for text (input_kind free_text) needs that text. */
export function missingRequired(questions: LiveQuestion[], drafts: Drafts): number[] {
  return questions.filter((q) => {
    if (!q.required) return false;
    const d = drafts[q.n] ?? {};
    const text = (d.answer_text ?? '').trim();
    if (d.option_n) {
      const o = option(q, d.option_n);
      return o?.input_kind === 'free_text' && !text;
    }
    return !text;
  }).map((q) => q.n);
}

/** Answers already saved as a draft (from any surface), so the form opens where it was left. */
export function initialAnswers(questions: LiveQuestion[]): Drafts {
  const out: Drafts = {};
  for (const q of questions) {
    const a = q.answer;
    if (!a) continue;
    const d: Draft = {};
    // Only an option the form offers: a draft on a hidden (chat) or unknown option would be sent
    // without the operator seeing it, and the gateway refuses it. Its text is kept.
    if (a.option_n && answerableOptions(q).some((o) => o.n === a.option_n)) d.option_n = a.option_n;
    if (a.answer_text) d.answer_text = a.answer_text;
    if (d.option_n || d.answer_text) out[q.n] = d;
  }
  return out;
}
