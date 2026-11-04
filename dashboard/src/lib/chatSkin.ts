/**
 * chatSkin.ts — the agent chat's own look for a person's bubble and the agent's words, in one place, so the thread
 * (components/kit/ThreadView.tsx, TranscriptCards) and an agent's call transcripts (components/kit/CallCard.tsx, the
 * call bar's live transcript) draw them the same and cannot drift.
 *
 *   bubbles  the blue bubble on the right at 13px; the agent's reply in a bordered card bubble on the left at 13px
 *   modern   Arturo's look: the grey .bubble-user, the flat .txt reply at 16px (arturo.css, variables from kit-modern.css)
 */
/** Bubbles: how wide a bubble (the person's or the agent's) may grow: 85% of the column, at most --ui-msg-width (Settings › Display › Message width; 560px by default) on a wide
 *  screen. On a phone 85% is always the smaller, so a phone keeps 85%. */
export const BUBBLES_WIDTH = 'max-w-[min(85%,var(--ui-msg-width,560px))]';
/** ...and a reply with a table or fenced code, which needs the room: 85% of the column at any width. */
export const BUBBLES_WIDE = 'max-w-[85%]';
/** The width a reply's bubble may take: wide once its Markdown has started a fenced code block or a table row (a line
 *  opening with ``` or |), finished or not, so a reply still streaming is as wide as it will be when it lands and
 *  the hand-off never jumps from 560px to 85%. */
export function bubbleWidthOf(text: string): string {
  return /^ {0,3}(```|~~~|\|)/m.test(text) ? BUBBLES_WIDE : BUBBLES_WIDTH;
}

/** Bubbles: the person's bubble. */
export const BUBBLES_USER = `${BUBBLES_WIDTH} rounded-2xl rounded-br-sm bg-blue-600/90 px-3.5 py-2 text-px13 leading-relaxed text-white whitespace-pre-wrap break-words`;
/** Bubbles: the agent's reply bubble (as before release 4e.6), as wide as its words up to bubbleWidthOf; one per text
 *  part of a turn (components/kit/Message.tsx MessageText). */
export const BUBBLES_AGENT = 'w-fit min-w-0 rounded-2xl rounded-tl-sm border border-border bg-card text-card-foreground px-3.5 py-2 text-px13 leading-relaxed break-words';
/** Bubbles: the agent's words (a paragraph of components/kit/Markdown.tsx in the bubbles skin). */
export const BUBBLES_BODY = 'text-px13 text-foreground/90';
/** A paragraph of a reply (components/kit/Markdown.tsx), any skin; Bubbles adds its body size and colour. */
export const PARAGRAPH = 'leading-relaxed whitespace-pre-wrap break-words';
export const BUBBLES_PARAGRAPH = `${PARAGRAPH} ${BUBBLES_BODY}`;

/** The wrapper of an agent reply in the thread, by skin. */
export function assistantTurnClass(skin: string): string {
  return skin === 'bubbles' ? 'turn-assistant group w-full min-w-0' : 'turn-assistant';
}

/** An agent's chosen chat style as the skin its turns are drawn in (Dev shows the chat it came from). */
export function turnSkinOf(style: string, beforeDev: string): 'bubbles' | 'modern' {
  const s = style === 'dev' ? beforeDev : style;
  return s === 'modern' ? 'modern' : 'bubbles';
}
