/**
 * The card popover's frame (components/kit/CardPopFrame.tsx, opened by CardChip): where it is placed, and what a key
 * does in it. Pure, so it is tested without a browser.
 */
import type { CSSProperties } from 'react';

export const POP = {
  /** Between the chip and the popover. */
  GAP: 8,
  /** The screen's gutter. */
  EDGE: 16,
  /** About a whole card: below the chip when this fits there, else above, else the full height. */
  ROOM: 420,
  MAX_W: 480,
} as const;

/** Beside the chip, never off the screen. Its controls are inside the card (CardPopContext), so it needs no room
 *  around it. `cw` is the page's width without its scrollbar (documentElement.clientWidth), `vh` the visible height.
 *  A phone uses a bottom sheet instead (kit.css). */
export function placePopover(a: { left: number; top: number; bottom: number }, cw: number, vh: number): CSSProperties {
  const { GAP, EDGE, ROOM, MAX_W } = POP;
  const width = Math.max(0, Math.min(MAX_W, cw - EDGE * 2));
  const left = Math.max(EDGE, Math.min(a.left, cw - width - EDGE));
  const below = vh - a.bottom - GAP - EDGE;
  const above = a.top - GAP - EDGE;
  if (below >= ROOM) return { left, width, top: a.bottom + GAP, maxHeight: below };
  if (above >= ROOM) return { left, width, bottom: vh - a.top + GAP, maxHeight: above };
  return { left, width, top: EDGE, maxHeight: vh - EDGE * 2 };
}

export interface PopKeyEvent { key: string; shiftKey?: boolean; defaultPrevented?: boolean }
export interface PopFocus {
  /** This popover is the last one open (a card opened from a chip inside another card's popover is on top). */
  topmost: boolean;
  /** Focus is in this popover (the frame itself or anything in it). */
  inside: boolean;
  /** Focus is on the frame itself or its first focusable / on its last focusable. */
  atStart: boolean;
  atEnd: boolean;
}
/** What a key does: 'close' (Escape), 'first' / 'last' (Tab wraps inside the popover, which is a dialog), 'keep'
 *  (Tab moves inside it as usual, but no outer focus trap may take it out), or null (not this popover's key). Only the
 *  topmost popover acts, and never on a key something else already handled. Whatever it acts on it also stops, so
 *  an outer popover or the agent panel (its window Escape and Tab trap) do not act on it too. */
export function popoverKeyAction(e: PopKeyEvent, f: PopFocus): 'close' | 'first' | 'last' | 'keep' | null {
  if (!f.topmost || e.defaultPrevented) return null;
  if (e.key === 'Escape') return 'close';
  if (e.key !== 'Tab' || !f.inside) return null;
  if (e.shiftKey && f.atStart) return 'last';
  if (!e.shiftKey && f.atEnd) return 'first';
  return 'keep';
}
