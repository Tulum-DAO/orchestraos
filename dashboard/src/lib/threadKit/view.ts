/**
 * view.ts — what the kit's thread view (components/kit/ThreadView.tsx) draws, in order: the thread model's items
 * (lib/threadKit/thread.ts: messages, results) with each voice call folded into one call row (lib/threadKit/calls.ts).
 * Pure, so the order is tested without a browser.
 *
 * Arturo's page feeds it his turns (stored ones from hydrateTurns, live ones with their send state): the turns between
 * two calls become thread items (consecutive finished jobs stack into one result card), a call's turns a call row.
 *
 * An agent's page feeds it its transcript as the agent chat builds it (finished work marked, tool runs grouped,
 * answered-card lines folded, its decision cards placed under the messages they belong to): each transcript node a
 * node row (drawn by the agent chat's own primitives, which are the kit's parts in the Bubbles skin), each decision
 * card a decision row, and each just-sent message still waiting for its own row a pending row.
 *
 * LiveText is the hook for words still being written that the thread does not hold yet (a live-text stream): the view
 * draws it as a streaming reply at the end of the thread, until a message with the same key arrives.
 */
import { groupCalls, hasVoiceCallMarker } from './calls';
import { agentItem, threadFromArturo, type ArturoTurnLike, type MessagePart, type ThreadItem, type ThreadMessage } from './thread';
import { hasPasteMarkers } from '../pastedText';
import { machineKindOf } from '../systemMessages';
import { isInterrupted, type GroupedNode } from '../toolGroups';
import type { Decision, FeedEntry } from '../decisions';
import type { PendingKind } from '../pendingSends';

export interface CallRow {
  kind: 'call'; key: string; callId: string | null; turns: ArturoTurnLike[]; live: boolean; startTs?: number; endTs?: number;
}

/** A transcript node of an agent's chat. `interrupted`: a tool run the agent stopped before finishing reads as
 *  stopped, not as still running (every run but the last, and the last when the agent is stopped). */
export interface NodeRow {
  kind: 'node'; key: string; node: GroupedNode; interrupted: boolean;
  /** In the Modern style: the node as a plain message the view draws itself (lib/threadKit/view modernMessage). */
  message?: ThreadMessage;
}
/** One of the agent's approval or decision cards, under the message it belongs to. */
export interface DecisionRow { kind: 'decision'; key: string; decision: Decision }
/** A message just sent, drawn until its own transcript row arrives (lib/pendingSends). */
export interface PendingRow { kind: 'pending'; key: string; text: string; pending: PendingKind }

export type ThreadRow = ThreadItem | CallRow | NodeRow | DecisionRow | PendingRow;

/** Words being written live, before the thread holds them. `key` is the message they will become, when known: once
 *  a row with that key is in the thread, the overlay is not drawn. */
export interface LiveText { key?: string; text: string; parts?: MessagePart[] }

/** The key a turn's message gets (thread.ts threadFromArturo: `t<id>`, else its index). */
export const turnKey = (t: ArturoTurnLike, index: number) => `t${t.id ?? index}`;

/** Arturo's turns as rows: calls folded, the rest as thread items. `live` = a call is on now (its turns so far are the
 *  last row). `by` names him on his finished jobs. */
export function arturoRows(turns: ArturoTurnLike[], live: boolean, by?: string): ThreadRow[] {
  const rows: ThreadRow[] = [];
  let run: { t: ArturoTurnLike; i: number }[] = [];
  const flush = () => {
    if (!run.length) return;
    // Keys stay the turns' own (by id, else their index in the whole thread), never their place in the run.
    const keyed = run.map(({ t, i }) => (t.id !== undefined ? t : { ...t, id: String(i) }));
    rows.push(...threadFromArturo(keyed, by).items);
    run = [];
  };
  for (const item of groupCalls(turns, live)) {
    if (item.kind === 'turn') { run.push({ t: item.turn, i: item.index }); continue; }
    flush();
    rows.push({ kind: 'call', key: item.key, callId: item.callId, turns: item.turns, live: item.live,
                ...(item.startTs !== undefined ? { startTs: item.startTs } : {}), ...(item.endTs !== undefined ? { endTs: item.endTs } : {}) });
  }
  flush();
  return rows;
}

/** In the Modern style, an agent's plain message as a message the view draws itself: the operator's own words (not
 *  the session prompt, a machine line, a call marker, a paste or a queued message, which keep their own cards) and
 *  the agent's replies (not one carrying pasted text). Anything else: null, drawn by the chat's own primitive. */
export function modernMessage(node: GroupedNode): ThreadMessage | null {
  if (node.kind === 'assistant') {
    if (hasPasteMarkers(node.text)) return null;
  } else if (node.kind === 'user') {
    if (node.isSystem || node.machine || node.queued || machineKindOf(node.text) || hasVoiceCallMarker(node.text)
        || hasPasteMarkers(node.text)) return null;
  } else return null;
  const item = agentItem(node);
  if (!item || item.kind !== 'message') return null;
  // A file sent with it (not an uploaded image) keeps the chat's bubble, which shows it as a chip.
  return item.attachments?.some((a) => a.kind !== 'image' || !a.url) ? null : item;
}

/** An agent's chat as rows: its interleaved feed (lib/decisions interleave), then the sends still waiting for their
 *  rows. `state` = the agent's live state, which says whether its last tool run was stopped. `modern`: plain
 *  messages carry the message the Modern style draws (modernMessage). */
export function agentRows(entries: FeedEntry[], pending: { id: string; display: string; kind: PendingKind }[], state: string,
  opts: { modern?: boolean } = {}): ThreadRow[] {
  const lastNode = entries.reduce((last, e, i) => (e.kind === 'node' ? i : last), -1);
  const rows: ThreadRow[] = entries.map((e, i): ThreadRow => {
    if (e.kind === 'decision') return { kind: 'decision', key: `decision-${e.decision.id}`, decision: e.decision };
    const message = opts.modern ? modernMessage(e.node) : null;
    return { kind: 'node', key: e.node.key, node: e.node, interrupted: isInterrupted({ isLast: i === lastNode, state }),
             ...(message ? { message } : {}) };
  });
  for (const p of pending) rows.push({ kind: 'pending', key: `pending-${p.id}`, text: p.display, pending: p.kind });
  return rows;
}

/** The key of the operator's latest turn, call turns included: only that bubble (or a failed one) shows its send
 *  state. */
export function lastUserKey(turns: ArturoTurnLike[]): string | undefined {
  for (let i = turns.length - 1; i >= 0; i--) if (turns[i].role === 'user') return turnKey(turns[i], i);
  return undefined;
}

/** Whether the live overlay is drawn: there are words, and the thread does not hold their message yet. */
export function showLive(rows: ThreadRow[], live: LiveText | null | undefined): boolean {
  if (!live || !(live.text.trim() || live.parts?.length)) return false;
  return !live.key || !rows.some((r) => r.key === live.key);
}
