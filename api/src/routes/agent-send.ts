/**
 * B1 — POST /api/agents/:id/send (Agent Page v1, DEC-1789508247033721, contracts D1-D6).
 *
 * The missing bridge from the web composer to a live agent pane: text (+
 * upload refs) -> verified pane inject (watch_gateway.py :9091 /agent-message,
 * the SAME parser-confirmed `verified_inject` transport agents.ts's
 * `gatewayInject` already uses for /api/agents/:id/inject — reimplemented here
 * rather than imported because agents.ts is a shared file this builder does
 * not touch). Never re-implements the inject heuristics: this module only
 * calls the gateway HTTP endpoint and classifies its honest response.
 *
 * D1/D2/D3 (never drop, held escalates, never inject into a menu/permission
 * prompt): when the gateway can't inject because the pane is showing a
 * prompt, is stopped/stalled, or is unreachable, this route falls back
 * durable-first to msg_store.py (state = truth) rather than dropping the
 * message. Two distinct busy shapes:
 *   - the pane has a menu/permission prompt up (`state === 'waiting'`) ->
 *     durable write + {state:'held'} (explicitly blocked on a prompt).
 *   - any other non-composer busy/unreachable case -> durable write +
 *     {state:'queued'} (ordinary backlog, no visible blocker).
 * A mid-turn 'working' busy is auto-converted to a durable hold BY THE GATEWAY
 * ITSELF (accepts:['held'] on the /agent-message call) -> {state:'held'}.
 *
 * D4 (composer-hold): when the pane has the operator's own unsubmitted typed text, or
 * a stranded_input state, this is NOT a queue-and-forget case — it is the
 * same 409-with-retained-payload contract agents.ts's /:id/inject already
 * gives ChatInput.tsx (busy.attemptText + force retry). We echo `payload`
 * back on the 409 so a client can re-POST with force:true.
 *
 * D5 (capability gate): the {state} enum response is returned ONLY to clients
 * that advertise 'send-states' (client_caps body field or the
 * X-Client-Capabilities header, comma/space separated — same convention as
 * unified-approvals.ts's clientHydratesMultipart). Older clients get the
 * legacy {ok:true}/{ok:false,error} shape.
 *
 * D6 (parser-confirmed submit only): the ONLY submit path is verified_inject
 * via the gateway's /agent-message; this route never sends raw keystrokes and
 * never guesses at composer state itself.
 */
import { Router, type Request, type Response } from 'express';
import { readFileSync } from 'fs';
import { execFileSync } from 'child_process';
import { join } from 'path';
import { mkdtempSync, writeFileSync, rmSync } from 'fs';
import { tmpdir } from 'os';
import { homedir } from 'os';
import { getRegistry } from '../services/state-reader.js';
import { loadConfig } from '../lib/config.js';

const HOME = process.env.HOME || homedir();
const ORCHESTRA_DIR = process.env.ORCHESTRA_DIR || join(HOME, 'scripts/agent-orchestra');
const UPLOADS_DIR = join(ORCHESTRA_DIR, 'state', 'uploads');
const MSG_STORE = join(ORCHESTRA_DIR, 'msg_store.py');
const GATEWAY_TOKEN_FILE = join(HOME, '.config/jarvis/watch-gateway-token');
const GATEWAY_URL = process.env.WATCH_GATEWAY_URL || 'http://127.0.0.1:9091';

const IMAGE_EXTS = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'heic', 'heif', 'bmp', 'svg']);

export type SendState = 'delivered' | 'queued' | 'held';

export interface Attachment { upload_id: string }

export interface SendBody {
  text?: string;
  attachments?: Attachment[];
  client_caps?: string[];
}

export interface GatewayInjectResult {
  httpStatus: number;
  ok: boolean;
  delivered?: boolean;
  held?: boolean;
  message_id?: string;
  reason?: string;
  state?: string;
  activity?: string;
  composer_text?: string;
  stranded?: { text?: string; age_s?: number };
  hold_failed?: string;
  unreachable?: boolean;
  error?: string;
  attempts?: number;
}

export interface MsgStoreSendResult {
  sent: boolean;
  id?: string;
  error?: string;
}

export interface AgentSendDeps {
  /** Check if an agent exists in the registry. */
  agentExists(agentId: string): boolean;
  /** agent id -> tmux session (registry.tmux_session, falls back to the id itself). */
  resolveSession(agentId: string): string;
  /** Calls watch_gateway.py POST /agent-message. Never throws — network/auth
   * failures come back as {unreachable:true}. */
  gatewayInject(session: string, text: string): Promise<GatewayInjectResult>;
  /** Durable-first fallback: python3 msg_store.py send --body-file <tmp>. */
  msgStoreSend(params: { fromAgent: string; toAgent: string; body: string; reason: string }): Promise<MsgStoreSendResult>;
}

// ── real deps ────────────────────────────────────────────────────────────

function realAgentExists(agentId: string): boolean {
  return !!(getRegistry() as any)?.agents?.[agentId];
}

function realResolveSession(agentId: string): string {
  const registry = getRegistry() as any;
  const agent = registry?.agents?.[agentId];
  if (agent) return (agent.tmux_session as string) || agentId;
  if (agentId.startsWith('unregistered:')) return agentId.replace('unregistered:', '');
  return agentId;
}

async function realGatewayInject(session: string, text: string): Promise<GatewayInjectResult> {
  let token = '';
  try { token = readFileSync(GATEWAY_TOKEN_FILE, 'utf-8').trim(); } catch { /* no token provisioned */ }
  if (!token) return { httpStatus: 0, ok: false, unreachable: true, error: 'gateway token unavailable' };
  try {
    const resp = await fetch(`${GATEWAY_URL}/agent-message`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      // accepts:['held'] lets the gateway itself durable-hold a pure mid-turn
      // 'working' busy (D1/D2) rather than 409-ing it back to us.
      body: JSON.stringify({ session, text, accepts: ['held'] }),
      signal: AbortSignal.timeout(25000),
    });
    const body: any = await resp.json().catch(() => ({}));
    return {
      httpStatus: resp.status,
      ok: !!body.ok,
      delivered: !!body.delivered,
      held: !!body.held,
      message_id: body.message_id,
      reason: body.reason,
      state: body.state,
      activity: body.activity,
      composer_text: body.composer_text,
      stranded: body.stranded,
      hold_failed: body.hold_failed,
      error: body.error,
      attempts: body.attempts,
    };
  } catch (err: any) {
    return { httpStatus: 0, ok: false, unreachable: true, error: err?.message || 'gateway unreachable' };
  }
}

async function realMsgStoreSend(params: { fromAgent: string; toAgent: string; body: string; reason: string }): Promise<MsgStoreSendResult> {
  // File-sourced body (msg_store.py --body-file): an inline --body arg is the
  // documented mangling hazard (backticks/shell metachars silently corrupting
  // a DELIVERED body) — every send from this route goes through a temp file,
  // never inline interpolation.
  let dir: string | null = null;
  try {
    dir = mkdtempSync(join(tmpdir(), 'agent-send-'));
    const bodyFile = join(dir, 'body.txt');
    writeFileSync(bodyFile, params.body, 'utf-8');
    const out = execFileSync('python3', [
      MSG_STORE, 'send',
      '--from', params.fromAgent,
      '--to', params.toAgent,
      '--type', 'held_message',
      '--priority', 'high',
      '--source', 'web_ui',
      '--body-file', bodyFile,
      '--reason', params.reason,
    ], { encoding: 'utf-8', timeout: 10000, cwd: ORCHESTRA_DIR });
    const parsed = JSON.parse(out);
    if (parsed.sent) return { sent: true, id: parsed.id };
    return { sent: false, error: parsed.error || 'msg_store refused the send' };
  } catch (err: any) {
    return { sent: false, error: err?.message || 'msg_store send failed' };
  } finally {
    if (dir) { try { rmSync(dir, { recursive: true, force: true }); } catch { /* best-effort cleanup */ } }
  }
}

export const defaultAgentSendDeps: AgentSendDeps = {
  agentExists: realAgentExists,
  resolveSession: realResolveSession,
  gatewayInject: realGatewayInject,
  msgStoreSend: realMsgStoreSend,
};

// ── capability gate (D5) ─────────────────────────────────────────────────

export function clientWantsSendStates(req: Request): boolean {
  const bodyCaps: string[] = Array.isArray((req.body as SendBody)?.client_caps) ? (req.body as SendBody).client_caps! : [];
  const headerRaw = (req.headers['x-client-capabilities'] as string) || '';
  const headerCaps = headerRaw.split(/[,\s]+/).map((t) => t.trim().toLowerCase()).filter(Boolean);
  const all = new Set([...bodyCaps.map((c) => String(c).toLowerCase()), ...headerCaps]);
  return all.has('send-states');
}

// ── message text (marker grammar) ───────────────────────────────────────

/** Prepends `[IMAGE: path]` / `[FILE: path]` markers — the SAME grammar
 * ChatInput.tsx already uses for its paperclip upload (extends, doesn't
 * fork). upload_id is the server-generated filename uploads.ts returns. */
export function buildMessageText(text: string, attachments: Attachment[] | undefined): string {
  const trimmed = (text || '').trim();
  if (!attachments || attachments.length === 0) return trimmed;
  const markers = attachments
    .filter((a) => a && a.upload_id)
    .map((a) => {
      const ext = (a.upload_id.split('.').pop() || '').toLowerCase();
      const tag = IMAGE_EXTS.has(ext) ? 'IMAGE' : 'FILE';
      const absPath = join(UPLOADS_DIR, a.upload_id);
      return `[${tag}: ${absPath}]`;
    });
  return [...markers, trimmed].filter(Boolean).join(' ');
}

// ── the handler (exported standalone so tests can drive it with fake req/res,
// no server/supertest needed — same pattern as unified-approvals.menu.test.ts) ──

export async function handleAgentSend(deps: AgentSendDeps, req: Request, res: Response): Promise<void> {
  const agentId = String(req.params.id || '').trim();
  const body: SendBody = req.body || {};
  const text = String(body.text || '').trim();
  const capable = clientWantsSendStates(req);

  if (!agentId || (!text && !(body.attachments && body.attachments.length))) {
    const err = 'agent id and (text or attachments) required';
    res.status(400).json(capable ? { error: err } : { ok: false, error: err });
    return;
  }

  if (!deps.agentExists(agentId)) {
    res.status(404).json({ ok: false, error: 'unknown agent', agent: agentId });
    return;
  }

  const fromAgent = (req.headers['x-orchestra-user'] as string) || loadConfig().operatorId;
  const session = deps.resolveSession(agentId);
  const messageText = buildMessageText(text, body.attachments);

  const gw = await deps.gatewayInject(session, messageText);

  const shape = (state: SendState, extra: Record<string, unknown> = {}) =>
    capable ? { state, ...extra } : { ok: state === 'delivered' || state === 'queued' || state === 'held', ...extra };

  // Delivered — the parser-confirmed submit landed.
  if (gw.ok && gw.delivered) {
    res.status(200).json(shape('delivered', { session, attempts: gw.attempts }));
    return;
  }

  // Gateway itself durable-held a pure mid-turn 'working' busy (accepts:['held']).
  if (gw.ok && gw.held) {
    res.status(200).json(shape('held', { message_id: gw.message_id, reason: 'busy_working' }));
    return;
  }

  // D4 — composer-hold: the operator's own unsubmitted typed text, or a stranded
  // composer. NEVER queue on top of this; echo the payload for a force retry.
  if (gw.composer_text || gw.stranded) {
    const legacy = {
      busy: true,
      reason: gw.reason,
      state: gw.state,
      activity: gw.activity,
      composer_text: gw.composer_text,
      stranded: gw.stranded,
    };
    res.status(409).json({
      ...legacy,
      payload: { text, attachments: body.attachments, client_caps: body.client_caps },
    });
    return;
  }

  // Everything else that isn't a clean delivery: durable-first to msg_store
  // (D1/D2/D3 — never drop, never dead-end). A visible prompt/menu
  // (`state === 'waiting'`, i.e. waiting_permission) is reported as `held`
  // (explicitly blocked on the prompt); anything else durable-writes as
  // ordinary `queued` backlog.
  const isMenuOrPrompt = gw.state === 'waiting';
  const reason = gw.unreachable
    ? `gateway unreachable (${gw.error || 'no response'}) — durable-first per B1/D1`
    : `pane busy (${gw.reason || gw.state || 'unknown'}) — durable-first per B1/D1`;

  const write = await deps.msgStoreSend({ fromAgent, toAgent: session, body: messageText, reason });

  if (!write.sent) {
    // Never lie about delivery on a failed durable write either.
    const err = write.error || 'durable write failed';
    res.status(502).json(capable
      ? { state: 'held', reason: 'durable_write_failed', error: err }
      : { ok: false, error: err });
    return;
  }

  const state: SendState = isMenuOrPrompt ? 'held' : 'queued';
  res.status(200).json(shape(state, {
    message_id: write.id,
    reason: isMenuOrPrompt
      ? 'agent pane is showing a menu/permission prompt'
      : (gw.unreachable ? 'gateway unreachable' : (gw.reason || 'agent busy')),
  }));
}

const router = Router();

router.post('/:id/send', (req: Request, res: Response) => {
  handleAgentSend(defaultAgentSendDeps, req, res).catch((err) => {
    res.status(500).json({ ok: false, error: err?.message || 'internal error' });
  });
});

export default router;
