// The reaction wake (spec "Agent wake"): what an agent receives when the operator reacts to a message in its chat.
// One line, so a pane delivery is not a multi-line paste:
//   <reaction from="user:<name>" emoji="👍" on="<first 120 chars of the message>"/> You may reply, react (`orchestra react`), or do nothing.
// The agent reads it; the chat never draws it (chat-transcript.ts stripPageContext and the reaction_wake flag,
// readInterruptedTurns, queued-merge.ts B1). Built and recognised HERE only, so the writer (services/reaction-wake.ts)
// and the readers cannot drift apart.
export const REACTION_WAKE_NOTE = 'You may reply, react (`orchestra react`), or do nothing.';
export const REACTION_ON_MAX = 120;

const OPEN = '<reaction from="user:';
// The WHOLE (trimmed) message must be the wake: mail or quoted text that merely contains a tag is not one.
const WAKE_RE = /^<reaction from="user:[^"<>\n]*" emoji="[^"<>\n]*" on="[^"<>\n]*"\/>(?:\s*You may reply, react \(`orchestra react`\), or do nothing\.)?$/;

/** An attribute value: one line, without quotes or angle brackets (they could close the tag), at most `max` code
 *  points (never half an emoji). */
function attr(v: unknown, max: number): string {
  const flat = String(v ?? '').replace(/["<>]/g, '').replace(/\s+/g, ' ').trim();
  return Array.from(flat).slice(0, max).join('');
}

export function reactionWakeText(w: { user: string; emoji: string; on: string }): string {
  return `${OPEN}${attr(w.user, 80)}" emoji="${attr(w.emoji, 16)}" on="${attr(w.on, REACTION_ON_MAX)}"/> ${REACTION_WAKE_NOTE}`;
}

export function hasReactionWake(text: unknown): boolean {
  return typeof text === 'string' && WAKE_RE.test(text.trim());
}

/** '' when the whole message is the wake; otherwise the text untouched (trimmed, as before). */
export function stripReactionWake(text: string): string {
  return hasReactionWake(text) ? '' : text.trim();
}
