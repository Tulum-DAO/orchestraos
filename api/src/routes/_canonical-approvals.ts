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
import { CODE_ROOT } from '../lib/codeRoot.js';

const ORCHESTRA = process.env.ORCHESTRA_DIR || loadConfig().dataDir;
const SCRIPTS = process.env.ORCHESTRA_SCRIPTS_DIR || join(CODE_ROOT, 'scripts');   // code, not data (lib/codeRoot.ts)
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
  /** Why a safety-gate card refused this caller (error 'agent_cannot_answer_gate_card'; plan Task 7a). */
  why?: string;
  id: string;
  status?: number;
}

/** Who is on the other end of an answer request, for approval.py's caller check (plan Task 7a). */
export interface AnswerPeer {
  /** '<ip>|<port>|<server port>' of this request's own socket. */
  peer?: string;
  /** The dashboard proxy's `x-orchestra-peer` header: the proxy's own client, in the same form. */
  forwardedPeer?: string;
  /** The last X-Forwarded-For entry (Tailscale Serve's client address). */
  forwardedFor?: string;
}

const PEER_SPEC = /^[0-9A-Fa-f:.]{2,45}\|\d{1,5}\|\d{1,5}$/;
const ADDR = /^[0-9A-Fa-f:.]{2,45}$/;

function header1(req: { headers?: Record<string, unknown> }, name: string): string {
  const v = req.headers?.[name];
  return String(Array.isArray(v) ? v[0] : v ?? '').trim();
}

/**
 * The request's peer description. Only well-formed values are passed on; approval.py treats anything it cannot
 * resolve as unknown (refused on a gate card), so a malformed or missing value never makes an agent a person.
 */
export function answerPeer(req: {
  socket?: { remoteAddress?: string; remotePort?: number; localPort?: number } | null;
  headers?: Record<string, unknown>;
}): AnswerPeer {
  const out: AnswerPeer = {};
  const s = req.socket;
  const own = s ? `${s.remoteAddress ?? ''}|${s.remotePort ?? ''}|${s.localPort ?? ''}` : '';
  if (PEER_SPEC.test(own)) out.peer = own;
  const fwd = header1(req, 'x-orchestra-peer');
  if (PEER_SPEC.test(fwd)) out.forwardedPeer = fwd;
  const xff = header1(req, 'x-forwarded-for').split(',').pop()?.trim() ?? '';
  if (ADDR.test(xff)) out.forwardedFor = xff;
  return out;
}

/** HTTP status for a refused canonical answer: a gate card refusing an agent is 403, anything else 400. */
export function answerRefusalStatus(result: AnswerResult): number {
  return result.error === 'agent_cannot_answer_gate_card' ? 403 : 400;
}

/**
 * The canonical PENDING feed (status='pending' rows) — the web GET unions this
 * with the frozen filesystem store (§2.3 dual-read). Read-only; a failure to
 * reach the service degrades to an empty canonical set (the filesystem read
 * still renders), never a 500.
 */
export function readCanonicalPending(): CanonicalRow[] {
  return readCanonicalPendingOrNull() ?? [];
}

/** The pending rows, or null when they could not be read (a timeout, a crash, bad JSON). An authorization
 *  check uses this one, so "could not read" is never mistaken for "no such card" (fail closed). */
export function readCanonicalPendingOrNull(): CanonicalRow[] | null {
  try {
    const out = execFileSync('python3', [APPROVAL_CLI, 'pending', '--json'], {
      timeout: 5000,
      encoding: 'utf-8',
    });
    const rows = JSON.parse(out.trim());
    return Array.isArray(rows) ? rows : null;
  } catch {
    return null;
  }
}

/**
 * The surface an answer is recorded under. The web route is the trusted origin, so it picks: a
 * client may ask for `chat` (answered from a chat thread on the web) and gets it; anything else,
 * including a claim to be the phone or the watch, is `web`, as before the hint existed.
 */
export function answerSurface(raw: unknown): 'web' | 'chat' {
  return raw === 'chat' ? 'chat' : 'web';
}

/** The approval.py argv for one answer. Split out so the surface mapping is tested without a shell-out. */
export function canonicalAnswerArgv(
  id: string,
  answer: string,
  text?: string,
  extra?: { optionN?: string; answerText?: string },
  opts?: { surface?: unknown; peer?: AnswerPeer },
): string[] {
  // R7d attribution: the web route holds the authenticated web session, so it
  // asserts the provenance tags server-side across the shell boundary —
  // surface web (or chat, see answerSurface), answered_by='operator' (a client can
  // never set these; this bridge is the trusted origin). Provenance only; never gates an authz call.
  const args = [APPROVAL_CLI, 'answer', '--id', id, '--answer', answer,
                '--surface', answerSurface(opts?.surface), '--answered-by', 'operator'];
  if (text) args.push('--text', text);
  if (extra?.optionN) args.push('--option-n', extra.optionN);
  if (extra?.answerText) args.push('--answer-text', extra.answerText);
  // Task 7a: who is calling. Always a peer flag (a missing socket reads as unknown, never as a person).
  const peer = opts?.peer;
  args.push('--peer', peer?.peer || 'unknown');
  if (peer?.forwardedPeer) args.push('--forwarded-peer', peer.forwardedPeer);
  if (peer?.forwardedFor) args.push('--forwarded-for', peer.forwardedFor);
  return args;
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
  extra?: { optionN?: string; answerText?: string },
  opts?: { surface?: unknown; peer?: AnswerPeer },
): AnswerResult {
  const args = canonicalAnswerArgv(id, answer, text, extra, opts);
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
