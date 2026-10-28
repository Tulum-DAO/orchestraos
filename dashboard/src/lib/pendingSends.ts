/**
 * pendingSends.ts — the just-sent bubble between "the server accepted it" and "the transcript has it".
 *
 * The rule this serves: a message is in exactly ONE place. While the request is in flight it is in
 * the box (with a spinner). The moment the server accepts it, the box clears and a pending bubble
 * appears in the same update. When the agent's transcript shows the row, the bubble goes and the
 * row takes its place. Matching is heldEchoReconcile's, for every kind of bubble.
 *
 * Pure helpers only; the shared state lives in stores/pendingSends.ts.
 */
import type { ChatItem } from './transcript.ts';
import { unconfirmed, type Echo, type Row } from './heldEchoReconcile.ts';

/** What the bubble says about where the message is.
 *  sent         delivered, not yet in the transcript: faded.
 *  queuedInTurn delivered while the agent was mid-turn (its CLI queues it): faded + "queued" chip.
 *  held         the server holds it for the agent's next turn boundary: amber.
 *  queued       /send stored it for later delivery (agent unreachable or stopped): amber. */
export type PendingKind = 'sent' | 'queuedInTurn' | 'held' | 'queued';

export interface PendingSend extends Echo {
  id: string;
  agentId: string;
  /** What the person typed (the bubble's words). `text` is what the agent got, markers included. */
  display: string;
  kind: PendingKind;
  createdAt: number;
  /** The agent has been seen not idle since this was sent, so its next idle means the turn that
   *  read the message has ended (the backstop below). */
  sawBusy?: boolean;
}

/** Agent states, as the gateway reports them, in which a delivered message waits in the CLI's queue. */
const MID_TURN = new Set(['working', 'thinking', 'stalled']);

export function kindFor(state: 'delivered' | 'held' | 'queued', agentState?: string | null): PendingKind {
  if (state === 'held') return 'held';
  if (state === 'queued') return 'queued';
  return agentState && MID_TURN.has(agentState) ? 'queuedInTurn' : 'sent';
}

/**
 * The transcript's operator rows, with ids that stay the same from one poll to the next (the
 * item's uuid, never its index: the window slides, and an index-based id would make an old row
 * look new and clear a bubble early). A drained queued batch offers each of its entries.
 */
export function userRows(items: readonly ChatItem[]): Row[] {
  const rows: Row[] = [];
  items.forEach((it, i) => {
    const any = it as { kind?: string; role?: string; text?: string; uuid?: string; is_system?: boolean; ts?: string; key?: string; entries?: { body?: string; sent_ts?: string }[] };
    if (any.kind === 'text' && any.role === 'user' && any.text && !any.is_system) {
      rows.push({ id: any.uuid || `u:${any.ts ?? ''}:${i}`, text: any.text });
    } else if (any.kind === 'queued_batch' && Array.isArray(any.entries)) {
      const base = any.key || any.uuid || `qb:${any.ts ?? ''}`;
      any.entries.forEach((e, j) => { if (e?.body) rows.push({ id: `${base}:${j}:${e.sent_ts ?? ''}`, text: e.body }); });
    }
  });
  return rows;
}

/** The bubbles still to draw, given the transcript rows on screen. */
export function visiblePending(pending: readonly PendingSend[], rows: readonly Row[]): PendingSend[] {
  return unconfirmed(pending, rows);
}

/** How long a bubble must exist before an idle agent can clear it without having been seen busy:
 *  the status feed can lag a short turn entirely, and a message that never made the agent busy
 *  has been read by the time this passes. */
export const IDLE_BACKSTOP_MS = 20_000;

/**
 * The agent-idle backstop: once the agent is idle again after reading, any bubble its transcript
 * row could not match (a long message arrives as a shorter banner) is dropped, so nothing strands.
 * Held/queued bubbles wait for their own row: the server delivers them at the boundary, after idle.
 */
export function afterIdle(pending: readonly PendingSend[], agentIdle: boolean, now: number): PendingSend[] {
  if (!agentIdle) return pending.map((p) => (p.sawBusy ? p : { ...p, sawBusy: true }));
  return pending.filter((p) => {
    if (p.kind === 'held' || p.kind === 'queued') return now - p.createdAt < 10 * 60_000;
    return !(p.sawBusy || now - p.createdAt >= IDLE_BACKSTOP_MS);
  });
}
