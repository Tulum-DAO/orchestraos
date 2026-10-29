/**
 * What the native questionnaire form (components/QuestionnaireForm.tsx) needs beyond the one question card's rules
 * (lib/multiPart.ts: questionnaireParts, questionnaireAnswers, questionnaireBody).
 *
 * A LIVE questionnaire is one the gateway's QuestionnaireStore holds (every one `approval.py
 * questionnaire` makes; ids start with qnr_). The API serves its questions[] (GET
 * /api/questionnaires/:id) and takes answers at POST /api/questionnaires/:id/answers, the same
 * contract the phone and watch use. Legacy file questionnaires keep their iframe.
 */

export function isLiveQuestionnaire(q: { id: string; source?: string }): boolean {
  return q.source === 'live' || /^qnr_/.test(q.id);
}

/** The context shown above the questions (decide-by line, where things stand, the asker's notes,
 *  which are appended to the summary): the questionnaire's summary, rendered as markdown, or ''
 *  when it is empty or only repeats the title. The phone shows the same text. */
export const SUMMARY_MAX = 8000;

export function questionnaireSummary(q: { title?: string | null; summary?: string | null }): string {
  const body = (q.summary || '').trim();
  if (!body || body === (q.title || '').trim()) return '';
  // An agent writes this text; a runaway one must not freeze the page or bury the questions.
  if (body.length > SUMMARY_MAX) {
    let cut = body.slice(0, SUMMARY_MAX);
    // A cut inside a ``` block would swallow the note into the code: close the block first.
    // Fences count the way components/chat/Markdown.tsx reads them: ``` at column 0.
    if ((cut.match(/^```/gm) || []).length % 2 === 1) cut += '\n```';
    return cut + '\n\n*(summary truncated here: the full text is on your phone)*';
  }
  return body;
}
