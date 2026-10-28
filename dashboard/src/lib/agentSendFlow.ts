/**
 * agentSendFlow.ts — one send from a chat composer to an agent, start to finish.
 *
 * Shared by the full agent page (Composer) and the floating agent pane (AgentCard) so both behave
 * the same: upload any files, POST /api/agents/:id/send (the held-aware route), and turn the
 * answer into what ChatInput does next. When the outcome is UNKNOWN (typed but unconfirmed, no
 * answer in time, or the connection dropped mid-request) it checks the agent's transcript before
 * saying anything, by sendOutcome.ts's rules: found -> treat as delivered; not found -> "couldn't
 * confirm". "Not sent" is said only when the server definitively refused.
 */
import { withTimeout, SendTimeout } from './composerSend';
import {
  sendToAgent, isDelivered, isQueued, isHeld, isComposerHold, isUnknown, describeSendState, COULDNT_CONFIRM,
  type SendAttachment, type AgentSendResult,
} from './agentSend';
import { STATE_COPY } from './stateCopy';
import { classify, requiresProbe, rowEvidences } from './sendOutcome';
import { kindFor, userRows, type PendingKind } from './pendingSends';
import { fetchTranscript } from './transcript';

export interface ComposerSendResult {
  ok: boolean;
  note?: string;
  queued?: boolean;
  held?: boolean;
  refused?: boolean;
  composer_text?: string;
  stranded?: { text?: string; age_s?: number };
  /** Present when the server accepted it: the bubble to show in place of the box's text. */
  pending?: { text: string; kind: PendingKind; pointer?: string };
}

async function uploadAttachment(file: File): Promise<SendAttachment> {
  const formData = new FormData();
  formData.append('file', file);
  const res = await fetch('/api/uploads', { method: 'POST', body: formData });
  if (!res.ok) throw new Error(`Upload failed: ${res.status}`);
  const data = await res.json();
  return { upload_id: data.filename };
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/**
 * Does the agent's transcript show this send? true / false / null (could not check). Only rows
 * that were not on screen at send time count, so an older identical message is not evidence.
 * Looks twice: the agent logs the line a moment after the submit.
 */
export async function transcriptShows(
  agentId: string, sent: string, pointer: string | undefined, priorRowIds: ReadonlySet<string> | undefined,
): Promise<boolean | null> {
  for (let attempt = 0; attempt < 2; attempt++) {
    if (attempt) await sleep(1500);
    try {
      const r = await fetchTranscript(agentId, 150);
      const rows = userRows(r.items || []).filter((row) => !priorRowIds?.has(row.id));
      if (rows.some((row) => rowEvidences(sent, row.text, pointer))) return true;
    } catch {
      return null;
    }
  }
  return false;
}

export async function sendFromComposer(
  agentId: string,
  { text, attachments }: { text: string; attachments: File[] },
  { force, priorRowIds }: { force: boolean; priorRowIds?: ReadonlySet<string> },
): Promise<ComposerSendResult> {
  let uploaded: SendAttachment[] | undefined;
  try {
    uploaded = attachments.length ? await withTimeout(Promise.all(attachments.map(uploadAttachment))) : undefined;
  } catch {
    // Nothing was sent yet: the upload is before the send.
    return { ok: false, note: "Couldn't upload the file — nothing was sent. Your message and file are still here." };
  }

  let result: AgentSendResult;
  try {
    result = await withTimeout(sendToAgent(agentId, { text, attachments: uploaded }, { force }));
  } catch (err) {
    // No answer in time: the request may still land. Look before saying anything.
    if (!(err instanceof SendTimeout)) throw err;
    result = { httpStatus: 0, state: 'unknown', reason: 'client_timeout' };
  }

  const sent = result.sent_text || text;
  const pointer = result.chip_dodge_pointer;

  if (isDelivered(result)) {
    const kind = kindFor('delivered', result.agent_state);
    return { ok: true, note: kind === 'queuedInTurn' ? STATE_COPY.queuedOk : undefined, pending: { text: sent, kind, pointer } };
  }
  if (isHeld(result) || isQueued(result)) {
    const kind = kindFor(isHeld(result) ? 'held' : 'queued');
    return {
      ok: false, queued: isQueued(result), held: isHeld(result), note: describeSendState(result) || undefined,
      pending: { text: sent, kind, pointer },
    };
  }
  if (isComposerHold(result)) {
    // A REFUSAL: the agent's own box already holds text. Nothing was sent; offer the overwrite.
    return { ok: false, refused: true, note: describeSendState(result), composer_text: result.composer_text, stranded: result.stranded };
  }

  // Unknown, or the connection failed mid-request: check the transcript first.
  const network = result.httpStatus === 0 && !isUnknown(result);
  if (isUnknown(result) || network) {
    const probe = requiresProbe(result.reason) ? await transcriptShows(agentId, sent, pointer, priorRowIds) : null;
    if (classify(result.reason, probe) === 'delivered') {
      return { ok: true, pending: { text: sent, kind: 'sent', pointer } };
    }
    if (network && probe === null) {
      return { ok: false, note: "Can't reach the server — your message is still here. Check the conversation before sending again." };
    }
    return { ok: false, note: COULDNT_CONFIRM };
  }

  // A definite refusal or failure from the server: nothing was delivered.
  return { ok: false, note: describeSendState(result) || 'Not sent.' };
}
