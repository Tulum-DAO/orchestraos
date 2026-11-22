/**
 * bubbleColour.ts — in the Bubbles chat style, the person's bubble takes the colour of the agent it was sent to (its
 * avatar look, lib/avatarLooks.ts), with text that stays readable on it.
 *
 *   1. The look's colour as drawn in this theme: "ink" (the default look) is near-black in the light theme and
 *      near-white in the dark one (avatarCore resolveColor), any other swatch is itself.
 *   2. A colour that would vanish into the page (under MIN_SURFACE contrast with the page background: a black or
 *      charcoal agent in the dark theme, a white one in the light theme) follows the theme instead: the theme's ink,
 *      as the default look does. No black-on-black, no white-on-white.
 *   3. The text: white where white reaches WCAG AA for normal text (4.5:1, the bubble's 13px words), else black. On
 *      ANY colour the better of pure black and pure white is at least 4.58:1 (they cross at a luminance of about
 *      0.18), so the words always meet AA and the agent's colour is never altered to get there.
 *   4. The edge: a 1px inner line (index.css), the bubble's own colour where that already stands off every surface a
 *      thread sits on (the page, the compact agent card) by EDGE_CONTRAST (WCAG's 3:1 for a component's boundary),
 *      else that colour shaded away from the surface (darker in light, lighter in dark) just until it does. A Yellow
 *      bubble on the light card gets a darker yellow line; Blue keeps its own.
 *   5. A message still on its way (lib/pendingSends): the BACKGROUND fades toward the page (FADE: a just-sent one a
 *      little, a held or queued one more), never the words: the text is picked again for the faded colour by rule 3,
 *      so it stays at AA, and the edge stays the agent's full colour, so the bubble still reads as its own.
 *
 * Modern is untouched: its grey .bubble-user does not read these colours.
 */
import { contrastRatio, resolveColor, type Theme } from '../components/avatar/avatarCore.ts';

/** The page behind the thread (index.css --background: oklch(1 0 0) light, oklch(0.145 0 0) dark). */
export const PAGE_BG: Record<Theme, string> = { light: '#ffffff', dark: '#0a0a0a' };
/** Every surface a thread is drawn on: the page, and the compact agent card (tone-900/950: neutral-100/50 in light,
 *  neutral-900/950 in dark). */
export const SURFACES: Record<Theme, string[]> = { light: ['#ffffff', '#fafafa', '#f5f5f5'], dark: ['#0a0a0a', '#171717'] };
/** WCAG 2.x AA, normal-size text. */
export const AA_TEXT = 4.5;
/** WCAG 2.x 1.4.11: a component's boundary against what is next to it. */
export const EDGE_CONTRAST = 3;
/** Under this contrast with the page, a bubble does not read as a bubble: it follows the theme's ink. */
export const MIN_SURFACE = 1.5;
/** How far a message still on its way fades toward the page: just sent, and held or queued by the server. */
export const FADE = { sent: 0.3, held: 0.55 } as const;
export const TEXT_ON_DARK = '#ffffff';
export const TEXT_ON_LIGHT = '#000000';

/** "#abc", "#AABBCC" → "#aabbcc"; anything else → null. */
export function normalHex(c: string | null | undefined): string | null {
  const m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec((c ?? '').trim());
  if (!m) return null;
  const h = m[1].toLowerCase();
  return `#${h.length === 3 ? h.split('').map((x) => x + x).join('') : h}`;
}

const toHex = (n: number) => Math.round(Math.max(0, Math.min(255, n))).toString(16).padStart(2, '0');
/** `hex` mixed toward `to` by `t` (0: hex, 1: to), as a hex colour. */
export function mixHex(hex: string, to: string, t: number): string {
  const a = parseInt(hex.slice(1), 16), b = parseInt(to.slice(1), 16);
  const ch = (v: number, s: number) => (v >> s) & 255;
  return `#${[16, 8, 0].map((s) => toHex(ch(a, s) + (ch(b, s) - ch(a, s)) * t)).join('')}`;
}

/** White text where white reaches AA on `bg` (it reads more naturally on a saturated colour), else black. */
export function textOn(bg: string): string {
  return contrastRatio(bg, TEXT_ON_DARK) >= AA_TEXT || contrastRatio(bg, TEXT_ON_DARK) >= contrastRatio(bg, TEXT_ON_LIGHT)
    ? TEXT_ON_DARK : TEXT_ON_LIGHT;
}

/** The edge for `bg`: itself, or shaded away from the theme's surfaces until it stands off every one by 3:1. */
export function edgeOn(bg: string, theme: Theme): string {
  const away = theme === 'light' ? '#000000' : '#ffffff';
  const ok = (c: string) => SURFACES[theme].every((s) => contrastRatio(c, s) >= EDGE_CONTRAST);
  for (let t = 0; t <= 1; t += 0.02) {
    const c = mixHex(bg, away, t);
    if (ok(c)) return c;
  }
  return away;
}

export interface Swatch { bg: string; fg: string; contrast: number }
export interface BubbleColours extends Swatch { edge: string; sent: Swatch; held: Swatch }

const swatch = (bg: string): Swatch => { const fg = textOn(bg); return { bg, fg, contrast: contrastRatio(bg, fg) }; };

/** The person's bubble for an agent whose look colour is `color` ("ink", a hex, or missing), in `theme`: settled,
 *  just sent and held, and its edge. */
export function bubbleColours(color: string | null | undefined, theme: Theme): BubbleColours {
  const ink = normalHex(resolveColor('ink', theme))!;
  let bg = (color === 'ink' ? null : normalHex(color)) ?? ink;
  if (contrastRatio(bg, PAGE_BG[theme]) < MIN_SURFACE) bg = ink;
  return {
    ...swatch(bg),
    edge: edgeOn(bg, theme),
    sent: swatch(mixHex(bg, PAGE_BG[theme], FADE.sent)),
    held: swatch(mixHex(bg, PAGE_BG[theme], FADE.held)),
  };
}

export type BubbleVar = '--bubble-user-bg' | '--bubble-user-fg' | '--bubble-user-edge'
  | '--bubble-user-sent-bg' | '--bubble-user-sent-fg' | '--bubble-user-held-bg' | '--bubble-user-held-fg';

/** The CSS variables the bubble reads (index.css .bubble-user-colour, .is-sent, .is-held). */
export function bubbleVars(c: BubbleColours): Record<BubbleVar, string> {
  return {
    '--bubble-user-bg': c.bg, '--bubble-user-fg': c.fg, '--bubble-user-edge': c.edge,
    '--bubble-user-sent-bg': c.sent.bg, '--bubble-user-sent-fg': c.sent.fg,
    '--bubble-user-held-bg': c.held.bg, '--bubble-user-held-fg': c.held.fg,
  };
}
