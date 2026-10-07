/**
 * B1 client — dashboard/src/lib/agentSend.ts (Agent Page v1, DEC-1789508247033721).
 *
 * Typed client for POST /api/agents/:id/send. Always advertises the
 * 'send-states' capability (X-Client-Capabilities header + client_caps body
 * field, belt-and-braces — the API's clientWantsSendStates() checks both) so
 * this client always gets the {state:'delivered'|'queued'|'held'} enum
 * response (D5) rather than the legacy {ok:true} shape.
 *
 * Kept as its own new file (not touching dashboard/src/lib/api.ts or
 * ChatInput.tsx per the file-ownership rule for this build) — the Agent Page
 * composer wires this in directly; see the wiring note in /tmp/gm-build-b1.md.
 */

import { STATE_COPY } from './stateCopy.ts';

export type SendState = 'delivered' | 'queued' | 'held';

export interface SendAttachment { upload_id: string }

export interface SendToAgentOptions {
  attachments?: SendAttachment[];
  /** Human override — retries a held/refused send through the gateway's
   * force path. Mirrors ChatInput.tsx's busy.attemptText + force retry. */
  force?: boolean;
}

/** The full parsed response — capability-gated shape (state enum) when the
 * server honors 'send-states' (always true for this client), a 409
 * composer-hold payload-echo, or a network-level failure. */
export interface AgentSendResult {
  httpStatus: number;
  state?: SendState;
  session?: string;
  attempts?: number;
  message_id?: string;
  reason?: string;
  // 409 composer-hold (D4) fields — same shape as the legacy /inject busy contract.
  busy?: boolean;
  activity?: string;
  composer_text?: string;
  stranded?: { text?: string; age_s?: number };
  payload?: { text?: string; attachments?: SendAttachment[]; client_caps?: string[] };
  error?: string;
}

const CLIENT_CAPS = ['send-states'];

export async function sendToAgent(
  id: string,
  { text, attachments }: { text: string; attachments?: SendAttachment[] },
  opts: SendToAgentOptions = {}
): Promise<AgentSendResult> {
  const body: Record<string, unknown> = {
    text,
    client_caps: CLIENT_CAPS,
  };
  if (attachments && attachments.length) body.attachments = attachments;
  if (opts.attachments && opts.attachments.length) body.attachments = opts.attachments;
  if (opts.force) body.force = true;

  let resp: Response;
  try {
    resp = await fetch(`/api/agents/${encodeURIComponent(id)}/send`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Client-Capabilities': CLIENT_CAPS.join(','),
      },
      body: JSON.stringify(body),
    });
  } catch (err: any) {
    // Never dead-end on a network failure either — the caller renders this
    // as a held/unsent bubble rather than throwing (D1/D2).
    return { httpStatus: 0, error: err?.message || 'network error' };
  }

  let parsed: Record<string, unknown> = {};
  try { parsed = await resp.json(); } catch { /* non-json */ }
  return { httpStatus: resp.status, ...(parsed as object) } as AgentSendResult;
}

// ── P3 helpers (queued bubble / held chip UI) ─────────────────────────────

/** The server ACCEPTED the message only if it answered 2xx.
 *
 * This gate is load-bearing, not defensive dressing. When msg_store's durable
 * write fails, agent-send.ts answers HTTP 502 with `state:'held'` — the state
 * enum alone says "held", which every helper below used to read as accepted.
 * The UI then rendered success and cleared the operator's typed text for a
 * message that was never stored. A state is only meaningful on a 2xx. */
function accepted(result: AgentSendResult): boolean {
  return result.httpStatus >= 200 && result.httpStatus < 300;
}

/** True when the composer should render a "queued" bubble in the transcript
 * (the message left the composer but hasn't landed in the pane yet). */
export function isQueued(result: AgentSendResult): boolean {
  return accepted(result) && result.state === 'queued';
}

/** True when the composer should render a "held" chip (blocked on a
 * menu/permission prompt, or gateway-side mid-turn hold). */
export function isHeld(result: AgentSendResult): boolean {
  return accepted(result) && result.state === 'held';
}

/** True when the send landed immediately (parser-confirmed submit). */
export function isDelivered(result: AgentSendResult): boolean {
  return accepted(result) && result.state === 'delivered';
}

/** True when the send did NOT land: a non-2xx answer (502 durable_write_failed,
 * 500, 404, 401) or a network-level failure (httpStatus 0). Distinct from the
 * 409 composer-hold, which is retryable with force and keeps its own UI. The
 * caller must keep the typed text and show an error for this. */
export function isFailed(result: AgentSendResult): boolean {
  return !isComposerHold(result) && !accepted(result);
}

/** True on the D4 composer-hold 409 — the payload is echoed back on
 * `result.payload` so the caller can retry with force:true rather than
 * losing what was typed. */
export function isComposerHold(result: AgentSendResult): boolean {
  return result.httpStatus === 409 && !!result.payload;
}

/** Human-readable one-liner for a held/queued/composer-hold chip. */
export function describeSendState(result: AgentSendResult): string {
  // A composer-hold 409 only ever means text is sitting in the agent's input box (the server
  // returns it for composer_text or stranded, nothing else). Same words as every surface.
  if (isComposerHold(result)) return STATE_COPY.stranded;
  if (isFailed(result)) {
    return result.error
      || (result.httpStatus ? `Send failed (HTTP ${result.httpStatus}) — not delivered` : 'Send failed — not delivered');
  }
  // 'busy_working' is a server CODE (agent-send.ts: mid-turn, the CLI queues natively), not a
  // sentence; it used to reach the panel verbatim. It is the case the queued words are true for.
  if (isHeld(result)) {
    if (result.reason === 'busy_working') return STATE_COPY.queuedOk;
    return result.reason || 'Held — will deliver at the next turn boundary';
  }
  if (isQueued(result)) return result.reason || 'Queued — agent is busy';
  if (result.error) return result.error;
  return '';
}
