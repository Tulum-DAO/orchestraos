/**
 * _canonical-approvals.ts — the ONE Node-side bridge to the canonical approval
 * service (SPEC all-model-parity §4 service boundary). BOTH web routes
 * (unified-approvals.ts + the legacy approvals.ts) reach `approval_requests`
 * ONLY through this module, which shells to `scripts/approval.py` — the single
 * ApprovalStore writer that iOS/watch/gateway also use. The Node side NEVER
 * imports the store or mutates tasks.db directly: one service, N thin callers,
 * so a web answer produces the SAME canonical transition (record_answer ->
 * fire_resume) as every other surface. This is what closes the split-brain the
 * VERIFY doc found (web wrote a filesystem store + a dead queue/inbox lane that
 * never reached the ledger, so watch/iOS never resolved a web answer).
 *
 * Isolation seams (mirror approval.py's APPROVAL_DB_PATH rationale — hermetic
 * tests drive the canonical service against a scratch DB):
 *   ORCHESTRA_DIR         — the orchestra root (filesystem stores, inbox, learning.db)
 *   ORCHESTRA_SCRIPTS_DIR — where approval.py lives (defaults to $ORCHESTRA_DIR/scripts).
 *                           Split out so a test can point the filesystem paths at a
 *                           scratch dir while still running the REAL service code.
 *   APPROVAL_DB_PATH      — inherited by the python child (execFile inherits env),
 *                           so the canonical read/write hit the scratch tasks.db.
 */
import { execFileSync } from 'child_process';
import { join } from 'path';
import { loadConfig } from '../lib/config.js';

const ORCHESTRA = process.env.ORCHESTRA_DIR || loadConfig().dataDir;
const SCRIPTS = process.env.ORCHESTRA_SCRIPTS_DIR || join(ORCHESTRA, 'scripts');
const APPROVAL_CLI = join(SCRIPTS, 'approval.py');

export interface CanonicalRow {
  id: string;
  from_agent: string;
  question: string;
  op_key: string | null;
  options: string[];
  status: string;
  created_at: string;
  kind: string | null;
  summary: string | null;
  risk_level: string | null;
  reversibility: string | null;
  feature: string | null;
  provider: string | null;
  // gm-mine-menu-card seam 1: the structured menu blob (question/options[{n,
  // label,input_kind}]) `approval.py pending --json` now emits via the SAME
  // parser (watch_gateway._menu) the gateway's /pending-approvals uses. None
  // for non-menu rows / legacy rows. Additive — every field above is
  // unchanged.
  menu?: { options?: Array<{ n: string; label: string; input_kind?: string; detail?: string }>; [k: string]: any } | null;
}

export interface AnswerResult {
  ok: boolean;
  applied?: boolean;
  error?: string;
  id: string;
  status?: number;
}

/**
 * The canonical PENDING feed (status='pending' rows) — the web GET unions this
 * with the frozen filesystem store (§2.3 dual-read). Read-only; a failure to
 * reach the service degrades to an empty canonical set (the filesystem read
 * still renders), never a 500.
 */
export function readCanonicalPending(): CanonicalRow[] {
  try {
    const out = execFileSync('python3', [APPROVAL_CLI, 'pending', '--json'], {
      timeout: 5000,
      encoding: 'utf-8',
    });
    const rows = JSON.parse(out.trim());
    return Array.isArray(rows) ? rows : [];
  } catch {
    return [];
  }
}

/**
 * Land a the operator answer on a canonical row through the ONE state machine. `answer`
 * is the raw verb (approve/deny/hold/accept/respond/option) — ALL validation
 * (menu/option/free-text/qnr_/perm:) lives in watch_gateway.apply_answer, which
 * the service invokes; this bridge re-implements NONE of it. On a validation
 * refusal the CLI exits non-zero but still prints the JSON verdict to stdout,
 * so we recover it from the thrown error's stdout.
 *
 * `extra.optionN` / `extra.answerText` are additive (gm-mine-menu-card seam
 * 1/2): a kind='menu' row answers with `answer='option'` plus one or both,
 * forwarded verbatim to `approval.py answer --option-n --answer-text` — the
 * SAME flags watch/iOS's answer path already validates (A1/A2/A3 live in
 * watch_gateway.apply_answer, re-implemented NOWHERE here).
 */
export function canonicalAnswer(
  id: string,
  answer: string,
  text?: string,
  extra?: { optionN?: string; answerText?: string }
): AnswerResult {
  // R7d attribution: the web route holds the authenticated web session, so it
  // asserts the provenance tags server-side across the shell boundary —
  // surface='web', answered_by='operator' (a client can never set these; this
  // bridge is the trusted origin). Provenance only; never gates an authz call.
  const args = [APPROVAL_CLI, 'answer', '--id', id, '--answer', answer,
                '--surface', 'web', '--answered-by', 'operator'];
  if (text) args.push('--text', text);
  if (extra?.optionN) args.push('--option-n', extra.optionN);
  if (extra?.answerText) args.push('--answer-text', extra.answerText);
  try {
    const out = execFileSync('python3', args, { timeout: 15000, encoding: 'utf-8' });
    return JSON.parse(out.trim());
  } catch (err: any) {
    const stdout = (err?.stdout ?? '').toString();
    try {
      return JSON.parse(stdout.trim());
    } catch {
      return { ok: false, error: err?.message || 'canonical answer failed', id };
    }
  }
}
