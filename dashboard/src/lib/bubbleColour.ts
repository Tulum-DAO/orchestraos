/**
 * bubbleColour.ts — in the Bubbles chat style, the person's bubble takes the colour of the agent it was sent to (its
 * avatar look, lib/avatarLooks.ts), with text that stays readable on it.
 *
 *   1. The look's colour as drawn in this theme: "ink" (the default look) is near-black in the light theme and
 *      near-white in the dark one (avatarCore resolveColor), any other swatch is itself.
 *   2. A colour that would vanish into the page (under MIN_SURFACE contrast with the page background: a black or
 *      charcoal agent in the dark theme, a white one in the light theme) follows the theme instead: the theme's ink,
 *      as the default look does. No black-on-black, no white-on-white.
 *   3. The text: pure black or pure white, whichever has the higher contrast on the bubble. On ANY colour the better
 *      of the two is at least 4.58:1 (they cross at a luminance of about 0.18), so the bubble's 13px words always meet
 *      WCAG AA for normal text (4.5:1) and the agent's colour is never altered to get there.
 *
 * Modern is untouched: its grey .bubble-user does not read these colours.
 */
import { contrastRatio, resolveColor, type Theme } from '../components/avatar/avatarCore.ts';

/** The page behind the thread (index.css --background: oklch(1 0 0) light, oklch(0.145 0 0) dark). */
export const PAGE_BG: Record<Theme, string> = { light: '#ffffff', dark: '#0a0a0a' };
/** WCAG 2.x AA, normal-size text. */
export const AA_TEXT = 4.5;
/** Under this contrast with the page, a bubble does not read as a bubble: it follows the theme's ink. */
export const MIN_SURFACE = 1.5;
export const TEXT_ON_DARK = '#ffffff';
export const TEXT_ON_LIGHT = '#000000';

/** "#abc", "#AABBCC" → "#aabbcc"; anything else → null. */
export function normalHex(c: string | null | undefined): string | null {
  const m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec((c ?? '').trim());
  if (!m) return null;
  const h = m[1].toLowerCase();
  return `#${h.length === 3 ? h.split('').map((x) => x + x).join('') : h}`;
}

/** Black or white text, whichever reads better on `bg`. */
export function textOn(bg: string): string {
  return contrastRatio(bg, TEXT_ON_DARK) >= contrastRatio(bg, TEXT_ON_LIGHT) ? TEXT_ON_DARK : TEXT_ON_LIGHT;
}

export interface BubbleColours { bg: string; fg: string; contrast: number }

/** The person's bubble for an agent whose look colour is `color` ("ink", a hex, or missing), in `theme`. */
export function bubbleColours(color: string | null | undefined, theme: Theme): BubbleColours {
  const ink = normalHex(resolveColor('ink', theme))!;
  let bg = (color === 'ink' ? null : normalHex(color)) ?? ink;
  if (contrastRatio(bg, PAGE_BG[theme]) < MIN_SURFACE) bg = ink;
  const fg = textOn(bg);
  return { bg, fg, contrast: contrastRatio(bg, fg) };
}

/** The CSS variables the bubble reads (index.css .bubble-user-colour). */
export function bubbleVars(c: BubbleColours): Record<'--bubble-user-bg' | '--bubble-user-fg', string> {
  return { '--bubble-user-bg': c.bg, '--bubble-user-fg': c.fg };
}
