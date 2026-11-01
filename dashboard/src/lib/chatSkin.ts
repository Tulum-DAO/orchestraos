/**
 * chatSkin.ts — the agent chat's own look for a person's bubble and the agent's words, in one place, so the thread
 * (components/kit/ThreadView.tsx, TranscriptCards) and an agent's call transcripts (components/kit/CallCard.tsx, the
 * call bar's live transcript) draw them the same and cannot drift.
 *
 *   bubbles  the blue bubble on the right at 13px; the agent's words plain on the left at 13px
 *   modern   Arturo's look: the grey .bubble-user, the flat .txt reply at 16px (arturo.css, variables from kit-modern.css)
 */
/** Bubbles: the person's bubble. */
export const BUBBLES_USER = 'max-w-[85%] rounded-2xl rounded-br-sm bg-blue-600/90 px-3.5 py-2 text-[13px] leading-relaxed text-white whitespace-pre-wrap break-words';
/** Bubbles: the agent's words (a paragraph of components/kit/Markdown.tsx in the bubbles skin). */
export const BUBBLES_BODY = 'text-[13px] text-foreground/90';
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
