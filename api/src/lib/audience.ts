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
  /^\[DECISION ANSWERED /,        // a menu-card answer relayed by the approval loop
  /^\[MULTI-PART DECISION ANSWERED /,
  /^\[HUMAN-TASK /,               // a human task the operator closed
  /^\[questionnaire-answers /,    // a submitted questionnaire relayed to its author
  /^\[questionnaire-discarded /,
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

/**
 * WHAT a machine-written user-role line is, stamped by the server as `machine` on the item so the
 * web, iOS and the Mac all draw it the same way instead of each guessing from the text. These lines
 * reach the pane as ordinary typed input (the approval loop, the message router and the harness type
 * them in), so the log alone records them exactly like the operator's own words; the grammars below
 * are the emitters' own (approval_resume.py, questionnaire_resume.py, message-router.py), not
 * guesses. Absent = the operator's own message.
 *   decision       [DECISION ANSWERED <id>]       a decision card's answer
 *   questionnaire  [MULTI-PART DECISION ANSWERED] / [questionnaire-answers|-discarded <id>]
 *   approval       [APPROVAL RESOLVED <id>]       an approve/deny card's answer
 *   human_task     [HUMAN-TASK <id>]              a human task closed
 *   relay          [MSG from <agent> | <prio>]    another agent's message
 *   task           <task-notification>            a background job finished
 *   system         every other harness line (hooks, lineage, slash-command output, ...)
 */
export type MachineKind = 'decision' | 'questionnaire' | 'approval' | 'human_task' | 'relay' | 'task' | 'system';

export function machineKind(text: unknown): MachineKind | null {
  const t = String(text ?? '').trimStart();
  if (t.startsWith('[DECISION ANSWERED ')) return 'decision';
  if (t.startsWith('[MULTI-PART DECISION ANSWERED ') || t.startsWith('[questionnaire-answers ')
      || t.startsWith('[questionnaire-discarded ')) return 'questionnaire';
  if (t.startsWith('[APPROVAL RESOLVED ')) return 'approval';
  if (t.startsWith('[HUMAN-TASK ')) return 'human_task';
  if (t.startsWith('[MSG from ')) return 'relay';
  if (NOTIFICATION.test(t)) return 'task';
  if (MACHINE_OPENERS.some((re) => re.test(t))) return 'system';
  return null;
}

/** Stamp `machine` on a user-role text item (and on queued-batch entries), in place. */
function stampMachine(it: any): void {
  if (it?.kind === 'text' && it.role === 'user' && !it.is_system) {
    const k = machineKind(it.text);
    if (k) it.machine = k;
  } else if (it?.kind === 'queued_batch' && Array.isArray(it.entries)) {
    for (const e of it.entries) {
      const k = machineKind(e?.body);
      if (k && e) e.machine = k;
    }
  }
}

/** What opened a turn, from its opening user-role text item. */
export function openerKind(it: any): OpenerKind {
  if (it?.is_system) return 'machine';           // the spawn brief, codex developer plumbing
  const t = String(it?.text ?? '').trimStart();
  if (NOTIFICATION.test(t)) return 'notification';
  if (MACHINE_OPENERS.some((re) => re.test(t))) return 'machine';
  return 'person';
}

// A background job's notice that arrived mid-turn (chat-transcript.ts queuedCommandItems) sits inside
// the turn it landed in; it does not open one.
const isOpener = (it: any) => it?.kind === 'text' && it.role === 'user' && !it.mid_turn;

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
  for (const it of items) stampMachine(it);
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
  // newest reply is still drawn, never folded (iOS 334: "if there are no recorded turns,
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
    stampMachine(it);
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
