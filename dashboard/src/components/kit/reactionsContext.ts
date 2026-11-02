/**
 * reactionsContext — what the reaction parts share (components/kit/Reactions.tsx, ReactionBadges.tsx):
 *
 *   ReactionsContext      the conversation's reactions (lib/useReactions) and whose chat it is; provided by the agent
 *                         chat (TranscriptChatView). Absent (Arturo's page, his pane, a call transcript): no reactions
 *                         are drawn and nothing changes.
 *   ReactionSlotContext   one message that can be reacted to (Reactions.tsx ReactionRow): its badges, its hover icons
 *                         and its hold, for the bubble that draws it (Turns UserWords, TranscriptCards UserBubble,
 *                         ReactionBadges ReactionAnchor) to carry.
 *
 * Desktop or phone is the pointer, not the width (spec): `(pointer: coarse)` is a phone or tablet.
 */
import { createContext, useContext, useSyncExternalStore, type HTMLAttributes } from 'react';
import type { BadgeGroup, ReactionMap, Reactor } from '../../lib/reactionState';
import { boxOf, type Box } from '../../lib/reactionLayout';

export interface ReactionsCtx {
  map: ReactionMap;
  me: Reactor | null;
  error: string | null;
  toggle: (ref: string, emoji: string) => void;
  /** Whose chat: an old log's emoji reply is drawn as this agent's reaction (lib/threadKit/view rowReactions). */
  agentId: string;
  /** A reactor's name on a badge ("You" is decided by the badge itself). */
  nameOf: (r: Reactor) => string;
}

export const ReactionsContext = createContext<ReactionsCtx | null>(null);
export const useReactionsCtx = () => useContext(ReactionsContext);

export type ReactSide = 'user' | 'agent';

/** The hold's touch handlers (lib/railAgents longPress), spread on the bubble on a phone. */
export interface PressHandlers {
  onClickCapture: (e: { preventDefault: () => void; stopPropagation: () => void }) => void;
  onTouchStart: (e: { currentTarget: EventTarget; touches: ArrayLike<{ clientX: number; clientY: number }> }) => void;
  onTouchMove: (e: { touches: ArrayLike<{ clientX: number; clientY: number }> }) => void;
  onTouchEnd: () => void;
  onTouchCancel: () => void;
}

export interface ReactionSlot {
  side: ReactSide;
  groups: BadgeGroup[];
  coarse: boolean;
  /** A menu of this message is open: its hover icons stay shown. */
  open: boolean;
  /** Desktop: where the hover icons go, measured on hover. `out`: OUTSIDE the bubble, `top` from its top, vertically
   *  centred (lib/reactionLayout barOffsetTop), `left` (an agent's reply only) just past its words. Not `out`: under
   *  it, `left` (an agent's reply) under the end of its words. null = not measured yet (under, by the CSS). */
  barAt: { out: boolean; top?: number; left?: number } | null;
  copyText: string;
  press: PressHandlers | null;
  onEnter: (bubble: HTMLElement) => void;
  /** A badge: on desktop a click gives that emoji (or takes it back); on a phone a tap says who gave it. */
  onBadge: (g: BadgeGroup, el: HTMLElement) => void;
  openQuick: (el: HTMLElement) => void;
  openMore: (el: HTMLElement) => void;
  /** Phone: open the hold's sheet without holding (the bubble's hidden "React" button: a screen reader, a keyboard). */
  openLift: (bubble: HTMLElement, trigger: HTMLElement) => void;
  whoTitle: (g: BadgeGroup) => string;
}

export const ReactionSlotContext = createContext<ReactionSlot | null>(null);
export const useReactionSlot = () => useContext(ReactionSlotContext);

type AnchorProps = HTMLAttributes<HTMLElement> & { [k: `data-${string}`]: string | undefined };

/** What the bubble that carries a message's badges spreads on itself: the anchor mark the badges and hover icons are
 *  placed by (components/kit/reactions.css), the hold on a phone, the hover measure on desktop. No slot: nothing.
 *  On a phone the system's own long-press (text selection, a link's menu) is held back ONLY while a hold is in progress
 *  or its sheet is open (`data-rx-holding`, set by the hold); otherwise the bubble behaves natively. */
export function anchorProps(slot: ReactionSlot | null, side: ReactSide): AnchorProps {
  if (!slot) return {};
  const base: AnchorProps = { 'data-rx-anchor': side, 'data-has-badges': slot.groups.length ? '' : undefined };
  if (slot.coarse) {
    return {
      ...base, ...(slot.press as unknown as HTMLAttributes<HTMLElement>),
      onContextMenu: (e) => { if (e.currentTarget.hasAttribute('data-rx-holding')) e.preventDefault(); },
      // the lifting finger's compat mousedown lands here: it must not pull the focus out of the sheet the hold opened
      onMouseDown: (e) => { if (e.currentTarget.hasAttribute('data-rx-holding')) e.preventDefault(); },
    };
  }
  return { ...base, onPointerEnter: (e) => slot.onEnter(e.currentTarget) };
}

/** The box the thread is clipped to (its nearest scrolling or clipping ancestor), else the window: hover icons are
 *  never drawn where they would be cut off, and a badge overhanging a bubble's corner is pulled in before it is. */
export function clipBoxOf(el: HTMLElement): Box {
  for (let p = el.parentElement; p; p = p.parentElement) {
    const s = getComputedStyle(p);
    if (s.overflowX !== 'visible' || s.overflowY !== 'visible') return boxOf(p.getBoundingClientRect());
  }
  return { left: 0, top: 0, width: window.innerWidth, height: window.innerHeight };
}

/** The box an agent's reply's WORDS take inside its anchor (which is as wide as the column, so the reply's own sizing is
 *  untouched): the anchor's left, top and height, and as wide as its furthest line of text reaches. The badges, hover
 *  icons and hidden React button are not words. No text: the anchor's own box. */
export function wordsBox(anchor: HTMLElement): Box {
  const a = anchor.getBoundingClientRect();
  let right = -Infinity;
  const walk = document.createTreeWalker(anchor, NodeFilter.SHOW_TEXT, {
    acceptNode: (n) => (n.parentElement?.closest('.rx-badges, .rx-bar, .rx-sr-react') ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
  });
  const range = document.createRange();
  for (let n = walk.nextNode(); n; n = walk.nextNode()) {
    if (!n.textContent?.trim()) continue;
    range.selectNodeContents(n);
    for (const r of range.getClientRects()) if (r.width > 0) right = Math.max(right, r.right);
  }
  if (!Number.isFinite(right)) return boxOf(a);
  return { left: a.left, top: a.top, width: Math.min(a.width, right - a.left), height: a.height };
}

const COARSE = '(pointer: coarse)';
function subscribeCoarse(cb: () => void): () => void {
  const mq = typeof window !== 'undefined' ? window.matchMedia?.(COARSE) : undefined;
  mq?.addEventListener('change', cb);
  return () => mq?.removeEventListener('change', cb);
}
const coarseNow = () => typeof window !== 'undefined' && !!window.matchMedia?.(COARSE).matches;

/** A touch screen (phone, tablet): hold to react, badges on the bottom border. Else desktop: hover icons, top corner. */
export function useCoarsePointer(): boolean {
  return useSyncExternalStore(subscribeCoarse, coarseNow, () => false);
}
