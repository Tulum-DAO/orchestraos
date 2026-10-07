/**
 * POST /api/questionnaires/:id/submit, as a function the route calls and the tests drive.
 *
 * SECURITY (2026-10-07; found by the Claude review leg on public #195, confirmed in live source by
 * gm, msg_e46c0dd7). The route used to:
 *   - build `state/feedback/${qId}_${ts}.json` from the RAW url id and write it BEFORE any id
 *     check, even for an unknown id. Express decodes %2F in route params, so
 *     /api/questionnaires/..%2F..%2Fx/submit wrote outside state/feedback (the class 42660bc96e
 *     closed on /api/learning/feedback).
 *   - pass the client's `submitted_by` to msg_store as `--from`, so any caller could forge the
 *     SENDER of the completion message on the bus.
 * gm's ruling: a strict id, checked against index.json BEFORE any filesystem write, a resolved-path
 * containment check, and a FIXED sender: the configured operator (loadConfig().operatorId).
 * The client's value is kept only as `unverified_submitted_by`, never as identity.
 */
import { join } from 'path';
import { SAFE_QID, containedPath } from './feedbackPath.js';

export interface SubmitDeps {
  orchestraDir: string;
  /** The fixed sender: the configured operator, never a request field. */
  sender: string;
  readJson: (path: string) => any;
  writeFile: (path: string, data: string) => void;
  ensureDir: (path: string) => void;
  send: (argv: string[]) => void;      // msg_store.py argv after the script path
  now: () => number;
}

export type SubmitResult =
  | { status: 400 | 404; body: { error: string } }
  | { status: 200; body: Record<string, unknown> };

export function submitQuestionnaire(deps: SubmitDeps, qId: string, body: any): SubmitResult {
  const { answers, responses, submitted_by } = body || {};
  const payload = answers || responses;
  if (!payload) return { status: 400, body: { error: 'answers or responses required' } };

  // 1. The id, BEFORE anything touches the filesystem.
  if (typeof qId !== 'string' || !SAFE_QID.test(qId)) {
    return { status: 400, body: { error: 'questionnaire id must match ' + SAFE_QID.source } };
  }
  const indexFile = join(deps.orchestraDir, 'state', 'questionnaires', 'index.json');
  const questionnaires = deps.readJson(indexFile) || [];
  const qIndex = questionnaires.findIndex((q: any) => q.id === qId);
  if (qIndex < 0) return { status: 404, body: { error: 'unknown questionnaire' } };

  // 2. The path, resolved and contained (belt and braces: SAFE_QID already excludes / and .).
  const ts = deps.now();
  const feedbackDir = join(deps.orchestraDir, 'state', 'feedback');
  deps.ensureDir(feedbackDir);
  const filename = `${qId}_${ts}.json`;
  const fullPath = containedPath(feedbackDir, filename);
  const feedbackFile = `state/feedback/${filename}`;

  const feedbackData: Record<string, unknown> = {
    questionnaire_id: qId,
    answers: payload,
    submitted_by: deps.sender,
    submitted_at: new Date(ts).toISOString(),
  };
  if (typeof submitted_by === 'string' && submitted_by && submitted_by !== deps.sender) {
    feedbackData.unverified_submitted_by = submitted_by.slice(0, 200);
  }
  deps.writeFile(fullPath, JSON.stringify(feedbackData, null, 2));

  questionnaires[qIndex].status = 'completed';
  questionnaires[qIndex].response_file = feedbackFile;
  questionnaires[qIndex].completed_at = new Date(ts).toISOString();
  deps.writeFile(indexFile, JSON.stringify(questionnaires, null, 2));

  const createdBy = questionnaires[qIndex].created_by || null;
  const title = questionnaires[qIndex].title || qId;
  if (createdBy) {
    try {
      deps.send([
        'send',
        '--from', deps.sender,
        '--to', createdBy,
        '--type', 'questionnaire_completed',
        '--subject', `Questionnaire completed: ${title}`,
        '--body', JSON.stringify(feedbackData),
      ]);
    } catch (err: any) {
      console.error(`[questionnaires] Failed to notify ${createdBy}:`, err?.message);
    }
  }
  return { status: 200, body: { submitted: true, questionnaire_id: qId, feedback_file: feedbackFile,
    notified_agent: createdBy } };
}
