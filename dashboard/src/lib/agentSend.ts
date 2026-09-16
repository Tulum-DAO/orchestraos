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

/** True when the composer should render a "queued" bubble in the transcript
 * (the message left the composer but hasn't landed in the pane yet). */
export function isQueued(result: AgentSendResult): boolean {
  return result.state === 'queued';
}

/** True when the composer should render a "held" chip (blocked on a
 * menu/permission prompt, or gateway-side mid-turn hold). */
export function isHeld(result: AgentSendResult): boolean {
  return result.state === 'held';
}

/** True when the send landed immediately (parser-confirmed submit). */
export function isDelivered(result: AgentSendResult): boolean {
  return result.state === 'delivered';
}

/** True on the D4 composer-hold 409 — the payload is echoed back on
 * `result.payload` so the caller can retry with force:true rather than
 * losing what was typed. */
export function isComposerHold(result: AgentSendResult): boolean {
  return result.httpStatus === 409 && !!result.payload;
}

/** Human-readable one-liner for a held/queued/composer-hold chip. */
export function describeSendState(result: AgentSendResult): string {
  if (isComposerHold(result)) {
    return result.composer_text
      ? 'Composer has unsubmitted text — waiting'
      : (result.stranded ? 'Composer input stranded — waiting' : 'Composer busy — waiting');
  }
  if (isHeld(result)) return result.reason || 'Held — will deliver at the next turn boundary';
  if (isQueued(result)) return result.reason || 'Queued — agent is busy';
  if (result.error) return result.error;
  return '';
}
