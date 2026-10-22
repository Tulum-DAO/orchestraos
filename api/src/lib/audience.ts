/**
 * audience + exchange on transcript items (transcript.v2, additive): the chat fold the operator
 * chose (ios-watch-dev msg_1491bb2c, 2026-10-22). Every surface draws the same conversation: the
 * operator's message, ONE folded strip for everything that happened in between, the reply to him.
 *
 * - `audience: "user"` = drawn in the conversation: the operator's own messages, the FIRST and the
 *   CLOSING reply of a turn he opened (Shaw 2026-10-22 03:58 ET: "the first response at least to my
 *   message ... then the div, which is collapsible. And then the most recent message"), and only the
 *   closing reply of a job he set off (a <task-notification>). In a log with no turn he opened at
 *   all, the newest reply is drawn too.
 * - `audience: "agent"` = folded into the strip: tools, thinking, intermediate replies, and every
 *   turn something else opened (mail, a hook, lineage, the spawn brief), replies included.
 * - `exchange` = the uuid of the operator message the item belongs to (the latest one at or before
 *   it), so a client folds everything between his bubble and the reply. Absent before his first.
 *
 * A turn = from one opener (a user-role text that is not a tool result) to the next. Measured on
 * ios-watch-dev 2026-10-21: 12 operator turns vs 8 machine turns, each answered on screen.
 */

/** User-role texts the harness or another agent wrote, never the operator typing. */
const MACHINE_OPENERS: RegExp[] = [
  /^\[MSG from /,                 // agent mail delivered into the pane
  /^\[APPROVAL RESOLVED /,        // a card answer relayed by the approval loop
  /^Stop hook feedback:/,
  /^\[QUEUE-DIGEST\]/,
  /^\[LINEAGE/,
  /^\[WAL DELTA\]/,
  /^<command-name>/,              // a slash command and its output: dev-mode command rows
  /^<local-command-stdout>/,
  /^<local-command-stderr>/,
  /^Caveat: The messages below were generated/,
];
const NOTIFICATION = /^<task-notification>/;

export type OpenerKind = 'person' | 'notification' | 'machine';

/** What opened a turn, from its opening user-role text item. */
export function openerKind(it: any): OpenerKind {
  if (it?.is_system) return 'machine';           // the spawn brief, codex developer plumbing
  const t = String(it?.text ?? '').trimStart();
  if (NOTIFICATION.test(t)) return 'notification';
  if (MACHINE_OPENERS.some((re) => re.test(t))) return 'machine';
  return 'person';
}

const isOpener = (it: any) => it?.kind === 'text' && it.role === 'user';

/**
 * Stamp `audience` (and `exchange` when known) on every item, in place, and return the list.
 * Run on the WHOLE log before windowing, so a turn whose opener falls outside the window keeps
 * its tags.
 */
export function tagAudience(items: any[]): any[] {
  let i = 0;
  let exchange: string | undefined;
  let personTurn = false;
  // Items before the first opener belong to no turn: the strip.
  while (i < items.length && !isOpener(items[i])) {
    items[i].audience = 'agent';
    i++;
  }
  while (i < items.length) {
    const opener = items[i];
    const kind = openerKind(opener);
    if (kind === 'person') personTurn = true;
    if (kind === 'person' && opener.uuid) exchange = String(opener.uuid);
    let end = i + 1;
    while (end < items.length && !isOpener(items[end])) end++;
    // The turn's first and closing replies: its first and last assistant text.
    let first = -1, closing = -1;
    for (let k = i + 1; k < end; k++) {
      if (items[k]?.kind === 'text' && items[k].role === 'assistant') {
        if (first < 0) first = k;
        closing = k;
      }
    }
    for (let k = i; k < end; k++) {
      const it = items[k];
      const shown = (k === i && kind === 'person') || (k === first && kind === 'person')
        || (k === closing && kind !== 'machine');
      it.audience = shown ? 'user' : 'agent';
      if (exchange) it.exchange = exchange;
    }
    i = end;
  }
  // No turn the operator opened anywhere in this log (only mail, hooks, the spawn brief): the
  // newest reply is still drawn, never folded (Shaw, iOS 334: "if there are no recorded turns,
  // display the most recent message"). One rule, server-side, so every client draws the same.
  if (!personTurn) {
    for (let k = items.length - 1; k >= 0; k--) {
      if (items[k]?.kind === 'text' && items[k].role === 'assistant') { items[k].audience = 'user'; break; }
    }
  }
  return items;
}

/**
 * Tag the synthetic rows the queued merge adds after windowing (they carry no tags yet): a
 * still-pending operator message is his (user, its own exchange); a batch or live card of agent
 * mail is the strip, in the exchange of whatever precedes it.
 */
export function tagSynthetics(items: any[]): any[] {
  let exchange: string | undefined;
  for (const it of items) {
    if (it.kind === 'rotation') { exchange = undefined; continue; }   // a new generation: no strip spans it
    if (it.audience === undefined) {
      if (it.kind === 'text' && it.role === 'user' && it.queued) {
        it.audience = 'user';
        if (it.uuid) it.exchange = String(it.uuid);
      } else {
        it.audience = 'agent';
        if (exchange) it.exchange = exchange;
      }
    }
    if (it.exchange) exchange = it.exchange;
  }
  return items;
}
