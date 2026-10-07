/**
 * Where a questionnaire answer is written, and who it is attributed to.
 *
 * ITS OWN MODULE, with no imports beyond node:path, so the test loads the REAL functions.
 *
 * THE HOLE (harness-ux-planner, read in code; confirmed here in BOTH trees): POST /feedback built
 * `${questionnaire_id || 'q'}_${Date.now()}.json` straight from the request body and wrote it with
 * join(feedbackDir, filename). join() normalises `..`, so questionnaire_id "../../x" wrote a JSON
 * file OUTSIDE state/feedback, anywhere the API user can write. The API has no auth, so any caller
 * that can reach it — any local process, any same-origin agent page — could do it. And the write
 * happened BEFORE the id was ever looked up in the questionnaire index.
 *
 * TWO GUARDS, deliberately redundant: the id must match a strict pattern (every one of the 18 real
 * ids does), AND the final path must resolve inside the feedback directory. The second catches
 * anything the first is ever loosened to admit.
 */
import { resolve, sep } from 'node:path';

/** Every real questionnaire id matches this (measured: 18 of 18). No dots, no slashes. */
export const SAFE_QID = /^[A-Za-z0-9_-]{1,128}$/;

export class BadFeedbackInput extends Error {}

/** The file name for one answer. A missing id falls back to 'q', as before; a bad one is refused. */
export function feedbackFilename(questionnaireId: unknown, now: number): string {
  if (questionnaireId === undefined || questionnaireId === null || questionnaireId === '') {
    return `q_${now}.json`;
  }
  if (typeof questionnaireId !== 'string' || !SAFE_QID.test(questionnaireId)) {
    throw new BadFeedbackInput('questionnaire_id must match ' + SAFE_QID.source);
  }
  return `${questionnaireId}_${now}.json`;
}

/** The absolute path for `filename` inside `dir`, or a refusal if it would land anywhere else. */
export function containedPath(dir: string, filename: string): string {
  const root = resolve(dir);
  const full = resolve(root, filename);
  if (!full.startsWith(root + sep)) throw new BadFeedbackInput('path escapes the feedback directory');
  return full;
}

/**
 * Who answered. KEPT from the request, NOT discarded — measured: questionnaires are answered by
 * more than the operator (stored feedback carries "kai", "noah", and "shaw · tranche A".."E"), so
 * forcing the operator id onto every answer would erase real attribution. Without auth the server
 * cannot VERIFY it either way; it is advisory. It is only ever stored inside JSON, never used in a
 * path, so it is bounded rather than trusted: a short single-line string, else the fallback.
 */
export function cleanSubmittedBy(value: unknown, fallback: string): string {
  if (typeof value !== 'string') return fallback;
  const v = value.trim();
  if (!v || v.length > 64 || /[\u0000-\u001f\u007f]/.test(v)) return fallback;
  return v;
}
