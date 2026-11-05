// The reaction wake (spec "Agent wake"): what an agent receives when the operator reacts to a message in its chat.
// One line, so a pane delivery is not a multi-line paste:
//   <reaction from="user:<name>" emoji="👍" on="<first 120 chars of the message>"/> Reply, react with `orchestra react <emoji>`, or answer exactly NO_REPLY to stay silent.
// (Logs from before 2026-11-02 carry the older note, "You may reply, react (`orchestra react`), or do nothing.": still
// recognised.) A turn cannot end without text, so NO_REPLY is the agent's way to say nothing: the chat hides it
// (silentReply).
// The agent reads it; the chat never draws it (chat-transcript.ts stripPageContext and the reaction_wake flag,
// readInterruptedTurns, queued-merge.ts B1). Built and recognised HERE only, so the writer (services/reaction-wake.ts)
// and the readers cannot drift apart.
export const REACTION_WAKE_NOTE = 'Reply, react with `orchestra react <emoji>`, or answer exactly NO_REPLY to stay silent.';
/** What an agent answers to a wake when it has nothing to say (a turn cannot end without text). */
export const NO_REPLY = 'NO_REPLY';
export const REACTION_ON_MAX = 120;

import { isEmoji } from './emoji.js';

const OPEN = '<reaction from="user:';
// The WHOLE (trimmed) message must be the wake: mail or quoted text that merely contains a tag is not one.
// Either note: the current one, or the one older logs carry.
const WAKE_RE = /^<reaction from="user:[^"<>\n]*" emoji="[^"<>\n]*" on="[^"<>\n]*"\/>(?:\s*(?:You may reply, react \(`orchestra react`\), or do nothing\.|Reply, react with `orchestra react <emoji>`, or answer exactly NO_REPLY to stay silent\.))?$/;

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

// "(no reply needed)", "No response required.", "no reply": what agents wrote before NO_REPLY existed.
const NO_REPLY_PHRASE = /^\(?\s*no (reply|response)( needed| required)?\s*\)?\.?$/i;

/** An agent's answer to a reaction wake that is not a message: 'silent' (NO_REPLY, or a "no reply" phrase), 'emoji'
 *  (one emoji: a reaction written as text), else null (real words: a reply, shown). */
export function silentReply(text: unknown): 'silent' | 'emoji' | null {
  if (typeof text !== 'string') return null;
  const t = text.trim();
  if (!t) return null;
  if (t === NO_REPLY || NO_REPLY_PHRASE.test(t)) return 'silent';
  return isEmoji(t) ? 'emoji' : null;
}
