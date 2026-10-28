/**
 * systemMessages.ts — machine-written lines in an agent's chat, read into a compact card.
 *
 * A port of the iOS app's SystemMessages (same grammars, same trimming rules), so the web and the
 * phone draw the same card for the same line. WHICH lines are machine lines is the server's call
 * (the transcript's `machine` tag, api/src/lib/audience.ts); this only reads one into the card's
 * parts, and falls back to the same prefixes when an older server sent no tag.
 *
 * What is trimmed is never lost: the card expands to the original text verbatim. A line that does
 * not parse returns null and is drawn as it always was.
 *
 * Grammars (the emitters' own):
 *   [DECISION ANSWERED <id>] '<q>' -> <label>[ — note: <text>]. Act on it, then run: …
 *   [MULTI-PART DECISION ANSWERED <id>] '<q>' -> [<part>: <picks>]… . Act on it, then run: …
 *   [APPROVAL RESOLVED <id>] Your request '<q>' was answered: <answer> (<ANSWER>)[ — note: …]. Act on it…
 *   [HUMAN-TASK <id>] the operator completed|couldn't: <task>[ — note: …]. Act, then run: …
 *   [questionnaire-answers <id>] <title>\n…
 *   [MSG from <sender> | <priority>] [banners] <subject> — Read /tmp/….md and act on it. …
 *   <task-notification> … <status>…</status> <summary>…</summary> </task-notification>
 */
export type MachineKind = 'decision' | 'questionnaire' | 'approval' | 'human_task' | 'relay' | 'task' | 'system';

export interface SystemMessage {
  kind: MachineKind;
  headline: string;
  /** What was asked / what the task or message was about. */
  subject: string | null;
  /** What was answered, or the task's status, or a relay's urgent priority. */
  detail: string | null;
  /** The operator's own note, a relay's age, a loop warning. */
  note: string | null;
  /** The approval card this answers, when the line names one. */
  cardId: string | null;
  /** The outcome, for the card's colour: approve / deny / done / cant / failed. */
  outcome: 'approve' | 'deny' | 'done' | 'cant' | 'failed' | null;
  /** A relay marked high or urgent. */
  urgent: boolean;
  /** A relay's sender id, as the router wrote it. */
  sender?: string | null;
  /** A relay's message-store id (from its /tmp/agent-msg-<id>.md pointer): the chat reads the full body by it. */
  messageId?: string | null;
}

/** The same prefixes the server classifies by, for a transcript from a server that sent no tag. */
export function machineKindOf(text: string): MachineKind | null {
  const t = text.trimStart();
  if (t.startsWith('[DECISION ANSWERED ')) return 'decision';
  if (t.startsWith('[MULTI-PART DECISION ANSWERED ') || t.startsWith('[questionnaire-answers ')
      || t.startsWith('[questionnaire-discarded ')) return 'questionnaire';
  if (t.startsWith('[APPROVAL RESOLVED ')) return 'approval';
  if (t.startsWith('[HUMAN-TASK ')) return 'human_task';
  if (t.startsWith('[MSG from ')) return 'relay';
  if (t.startsWith('<task-notification>')) return 'task';
  return null;
}

const ACK_TAILS = ['. Act on it, then run:', '. Act, then run:'];

/** Cut the agent-only ack instruction, up to the end of ITS line only: prose after it is kept. */
export function stripAck(s: string): string {
  const low = s.toLowerCase();
  let at = -1;
  for (const tail of ACK_TAILS) {
    const i = low.indexOf(tail.toLowerCase());
    if (i >= 0 && (at < 0 || i < at)) at = i;
  }
  if (at < 0) return s;
  const head = s.slice(0, at);
  const nl = s.indexOf('\n', at);
  if (nl < 0) return head;
  return `${head}\n${s.slice(nl + 1)}`.trim();
}

function unquote(s: string): string {
  let v = s.trim();
  while (v.endsWith('.')) v = v.slice(0, -1).trim();
  if (v.length >= 2 && v.startsWith("'") && v.endsWith("'")) v = v.slice(1, -1);
  return v.trim();
}

function splitNote(rest: string): [string, string | null] {
  const i = rest.indexOf(' — note: ');
  if (i < 0) return [rest, null];
  const note = rest.slice(i + ' — note: '.length).trim().replace(/\.$/, '');
  return [rest.slice(0, i), note || null];
}

const base = (kind: MachineKind, headline: string): SystemMessage =>
  ({ kind, headline, subject: null, detail: null, note: null, cardId: null, outcome: null, urgent: false });

function idOf(t: string): string | null {
  const m = t.match(/^\[[A-Za-z -]+? ([A-Za-z0-9_.:-]+)\]/);
  return m ? m[1] : null;
}

function decision(t: string, multi: boolean): SystemMessage | null {
  const close = t.indexOf(']');
  if (close < 0) return null;
  const [rest, note] = splitNote(stripAck(t.slice(close + 1)).trim());
  const msg = base(multi ? 'questionnaire' : 'decision', multi ? 'Questionnaire received' : 'Decision received');
  const arrow = rest.indexOf(' -> ');
  msg.subject = (arrow >= 0 ? unquote(rest.slice(0, arrow)) : unquote(rest)) || null;
  msg.detail = arrow >= 0 ? rest.slice(arrow + 4).trim().replace(/\.$/, '') || null : null;
  msg.note = note;
  msg.cardId = idOf(t);
  return msg;
}

function approval(t: string): SystemMessage | null {
  const close = t.indexOf(']');
  if (close < 0) return null;
  const [rest, note] = splitNote(stripAck(t.slice(close + 1)).trim());
  const m = rest.match(/^Your request '([\s\S]*)' was answered: (\w+)/);
  const answer = (m?.[2] || '').toLowerCase();
  const msg = base('approval', answer === 'deny' || answer === 'reject' ? 'Request denied'
    : answer === 'approve' || answer === 'accept' ? 'Request approved' : 'Request answered');
  msg.subject = m ? m[1].trim() : unquote(rest) || null;
  msg.detail = answer || null;
  msg.outcome = answer === 'approve' || answer === 'accept' ? 'approve' : answer === 'deny' || answer === 'reject' ? 'deny' : null;
  msg.note = note;
  msg.cardId = idOf(t);
  return msg;
}

function humanTask(t: string): SystemMessage | null {
  const close = t.indexOf(']');
  if (close < 0) return null;
  const [rest, note] = splitNote(stripAck(t.slice(close + 1)).trim());
  const cant = /couldn['’]t:/.test(rest);
  const msg = base('human_task', cant ? "Task couldn't be done" : 'Task done');
  const colon = rest.indexOf(':');
  msg.subject = (colon >= 0 ? rest.slice(colon + 1) : rest).trim().replace(/\.$/, '') || null;
  msg.outcome = cant ? 'cant' : 'done';
  msg.note = note;
  msg.cardId = idOf(t);
  return msg;
}

function questionnaireAnswers(t: string): SystemMessage | null {
  const discarded = t.startsWith('[questionnaire-discarded ');
  const close = t.indexOf(']');
  if (close < 0) return null;
  const firstLine = t.slice(close + 1).split('\n')[0].trim();
  const msg = base('questionnaire', discarded ? 'Questionnaire discarded' : 'Questionnaire received');
  msg.subject = unquote(firstLine.replace(/ was .*$/, '')) || null;
  msg.cardId = idOf(t);
  return msg;
}

function relay(t: string): SystemMessage | null {
  const after = t.slice('[MSG from '.length);
  const close = after.indexOf(']');
  if (close < 0) return null;
  const [senderRaw, prioRaw] = after.slice(0, close).split('|', 2).map((x) => x.trim());
  if (!senderRaw) return null;
  const priority = prioRaw ? prioRaw.toLowerCase() : null;
  let rest = after.slice(close + 1).trim();
  let timing: string | null = null;
  let loop = false;
  // Strip ONLY the banners the relay layer adds; a subject may itself start with a bracket.
  while (rest.startsWith('(') || rest.startsWith('[')) {
    const closer = rest[0] === '(' ? ')' : ']';
    const end = rest.indexOf(closer);
    if (end < 0) break;
    const banner = rest.slice(1, end);
    const low = banner.toLowerCase();
    if (!(low.startsWith('sent ') || low.startsWith('loop-suspect'))) break;
    rest = rest.slice(end + 1).trim();
    if (low.startsWith('loop-suspect')) loop = true;
    if (low.startsWith('sent ')) timing = banner.split('—')[0].trim().toLowerCase();
  }
  // Cut the agent's plumbing at the LAST " — Read " (a subject may contain an em dash).
  const cut = rest.lastIndexOf(' — Read ');
  const subject = (cut >= 0 ? rest.slice(0, cut) : rest).trim();
  const msg = base('relay', `Message from ${senderRaw}`);
  msg.subject = subject || null;
  msg.urgent = priority === 'high' || priority === 'urgent';
  msg.detail = msg.urgent ? priority : null;
  msg.note = [timing, loop ? 'possible reply loop' : null].filter(Boolean).join(' · ') || null;
  msg.sender = senderRaw;
  msg.messageId = rest.match(/\/tmp\/agent-msg-([A-Za-z0-9_]+)\.md/)?.[1] ?? null;
  // The approvals service's stored copy of an answer the agent already got ("Approval <id> resolved|discarded"):
  // it names its card, so it folds into that card like the direct answer line does.
  msg.cardId = subject.match(/^Approval ((?:apr|qnr)_[A-Za-z0-9_]+) (?:resolved|discarded)$/)?.[1] ?? null;
  return msg;
}

function tag(name: string, s: string): string | null {
  const m = s.match(new RegExp(`<${name}>([\\s\\S]*?)</${name}>`));
  return m ? m[1].trim() : null;
}

function taskNotification(t: string): SystemMessage | null {
  const status = tag('status', t);
  const summary = tag('summary', t);
  if (!summary) return null;                    // nothing human to show: the raw line instead
  const s = (status || '').toLowerCase();
  const msg = base('task', s === 'completed' ? 'Background task finished' : s === 'failed' ? 'Background task failed'
    : s ? `Background task ${s}` : 'Background task');
  msg.subject = summary;
  msg.detail = status;
  msg.outcome = s === 'failed' ? 'failed' : null;
  return msg;
}

/** The card for a machine line, or null (draw it as before). `kind` is the server's tag. */
export function parseSystemMessage(text: string, kind?: MachineKind | null): SystemMessage | null {
  const t = text.trim();
  const k = kind ?? machineKindOf(t);
  if (!k) return null;
  if (t.startsWith('[MULTI-PART DECISION ANSWERED')) return decision(t, true);
  if (t.startsWith('[DECISION ANSWERED')) return decision(t, false);
  if (t.startsWith('[APPROVAL RESOLVED')) return approval(t);
  if (t.startsWith('[HUMAN-TASK')) return humanTask(t);
  if (t.startsWith('[questionnaire-')) return questionnaireAnswers(t);
  if (t.startsWith('[MSG from ')) return relay(t);
  if (t.startsWith('<task-notification>')) return taskNotification(t);
  return null;                                  // a 'system' line keeps its existing rendering
}

/** Drop the machine lines that answer a card the chat already draws (the card's outcome row shows
 *  the answer), the approvals service's stored copy of that answer included, keeping every other relay and
 *  every line whose card is not drawn here. */
export function foldCardAnswers<N extends { kind: string; text?: string; machine?: string }>(
  nodes: N[], drawnCardIds: ReadonlySet<string>,
): N[] {
  if (!drawnCardIds.size) return nodes;
  return nodes.filter((n) => {
    if (n.kind !== 'user' || !n.text) return true;
    const sys = parseSystemMessage(n.text, (n.machine as MachineKind | undefined) ?? null);
    return !(sys?.cardId && drawnCardIds.has(sys.cardId));
  });
}

/** The one line a service's message (a sender that is not an agent: the approvals service, briefings) shows
 *  instead of "Message from": what happened and to what, from its body when it is one of the lines this file
 *  reads ("Request approved · <question>"), else the sender and the subject. */
export function serviceNoticeText(msg: SystemMessage, body?: string | null): string {
  const b = (body || '').trim();
  const dismissed = b.match(/^\[APPROVAL DISCARDED [^\]]+\] Your request '([\s\S]*?)' was DISMISSED/);
  if (dismissed) return `Request dismissed · ${dismissed[1].trim()}`;
  const known = b ? parseSystemMessage(b) : null;
  if (known && known.kind !== 'relay' && known.subject) return `${known.headline} · ${known.subject}`;
  return [msg.sender, msg.subject].filter(Boolean).join(' · ');
}
