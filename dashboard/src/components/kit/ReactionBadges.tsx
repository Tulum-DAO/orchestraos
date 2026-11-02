/**
 * ReactionBadges — what a bubble that can be reacted to carries on itself (reactions build step 2): its badges and,
 * on desktop, its hover icons. The bubble reads them from its message's slot (components/kit/reactionsContext.ts);
 * with no slot it draws exactly as before.
 *
 *   SlotBadges      one badge per emoji, a count above 1, the viewer's own tinted, who gave it on hover (desktop) or
 *                   tap (phone). The badge row OVERLAPS the bubble's border at its inner corner (lib/reactionLayout
 *                   badgePlace): desktop straddles the TOP border (iMessage: agent top-right, the person's top-left);
 *                   a phone straddles the BOTTOM border on the corner nearest the middle of the screen. The
 *                   bubble keeps room for it (reactions.css, = badgeReserve); near the scroller's edge it is pulled in.
 *   SlotBar         desktop hover icons on the bubble's outer side, vertically centred: 🙂 react, Copy, … (more).
 *                   On a phone: a "React" button hidden until focused, so a screen reader (or a keyboard) can open
 *                   the hold's sheet without holding.
 *   ReactionAnchor  an agent's reply has no bubble of its own: a plain block around its words (its width and layout
 *                   unchanged), whose words' extent (reactionsContext wordsBox) places the badges and icons.
 *
 * Kept apart from Reactions.tsx (the menus) so the bubbles that import it (Turns, Message, TranscriptCards) never
 * import the menus that draw those bubbles again.
 */
import { useLayoutEffect, useRef, type ReactNode } from 'react';
import { Ellipsis, SmilePlus } from 'lucide-react';
import Copy from './Copy';
import { badgePlace } from '../../lib/reactionLayout';
import { anchorProps, clipBoxOf, useReactionSlot, wordsBox, type ReactSide } from './reactionsContext';
import './reactions.css';

/** Every badge row re-placed in ONE animation frame: all measured first, then all written (no read/write thrash). */
const queued = new Set<() => () => void>();
let frame = 0;
function schedule(measure: () => () => void): void {
  queued.add(measure);
  if (frame) return;
  frame = requestAnimationFrame(() => {
    frame = 0;
    const writes = [...queued].map((m) => m());
    queued.clear();
    writes.forEach((w) => w());
  });
}

export function SlotBadges({ side }: { side: ReactSide }) {
  const slot = useReactionSlot();
  const ref = useRef<HTMLSpanElement>(null);
  const count = slot?.groups.length ?? 0;
  const coarse = slot?.coarse ?? false;
  // Placed on the bubble's border by lib/reactionLayout badgePlace, in the scroller's coordinates (so an overhang is
  // never clipped), then written as an offset from the bubble. reactions.css draws the same place before this runs.
  useLayoutEffect(() => {
    const el = ref.current;
    const anchor = el?.parentElement;
    if (!el || !anchor || !count) return;
    const measure = () => {
      const a = side === 'agent' ? wordsBox(anchor) : anchor.getBoundingClientRect();
      const box = anchor.getBoundingClientRect();
      const clip = clipBoxOf(anchor);
      const rel = { left: a.left - clip.left, top: a.top - clip.top, width: a.width, height: a.height };
      const p = badgePlace(side, coarse ? 'phone' : 'desktop', rel, { width: el.offsetWidth, height: el.offsetHeight },
        { width: clip.width, height: clip.height });
      const left = Math.round(p.left + clip.left - box.left);
      const top = Math.round(p.top + clip.top - box.top);
      return () => {
        el.style.left = `${left}px`;
        el.style.top = `${top}px`;
        el.style.right = 'auto';
        el.style.bottom = 'auto';
        el.style.transform = 'none';
        el.toggleAttribute('data-clamped', p.clamped);
      };
    };
    measure()();
    const later = () => schedule(measure);
    const ro = new ResizeObserver(later);
    ro.observe(anchor);
    ro.observe(el);
    window.addEventListener('resize', later);
    return () => { ro.disconnect(); window.removeEventListener('resize', later); queued.delete(measure); };
  }, [side, coarse, count]);
  if (!slot || !count) return null;
  return (
    <span ref={ref} className="rx-badges touch-compact" data-side={side}>
      {slot.groups.map((g) => (
        <button key={g.emoji} type="button" className="rx-badge hit-44" data-mine={g.mine ? '' : undefined}
          aria-pressed={slot.coarse ? undefined : g.mine}
          title={slot.coarse ? undefined : slot.whoTitle(g)} aria-label={`${g.emoji} from ${slot.whoTitle(g)}`}
          onPointerDown={(e) => e.stopPropagation()} onTouchStart={(e) => e.stopPropagation()}
          onClick={(e) => { e.stopPropagation(); slot.onBadge(g, e.currentTarget); }}>
          <span className="rx-emoji">{g.emoji}</span>
          {g.count > 1 && <span className="rx-count">{g.count}</span>}
        </button>
      ))}
    </span>
  );
}

export function SlotBar({ side }: { side: ReactSide }) {
  const slot = useReactionSlot();
  if (!slot) return null;
  if (slot.coarse) {
    // No visible clutter on a phone (the hold is the way in); a screen reader or keyboard reaches this instead.
    return (
      <button type="button" className="rx-sr-react" aria-haspopup="dialog" aria-label="React to this message" data-label="React"
        onClick={(e) => {
          e.stopPropagation();
          const bubble = e.currentTarget.closest<HTMLElement>('[data-rx-anchor]');
          if (bubble) slot.openLift(bubble, e.currentTarget);
        }} />
    );
  }
  const out = slot.barAt?.out ?? false;
  return (
    <span className="rx-bar" data-side={side} data-place={out ? 'out' : 'under'} data-open={slot.open ? '' : undefined}
      style={slot.barAt ? { top: slot.barAt.top, left: slot.barAt.left, right: slot.barAt.left !== undefined ? 'auto' : undefined } : undefined} onPointerDown={(e) => e.stopPropagation()}>
      <button type="button" className="rx-bar-btn" aria-label="React" title="React" aria-haspopup="dialog"
        onClick={(e) => { e.stopPropagation(); slot.openQuick(e.currentTarget); }}>
        <SmilePlus size={15} />
      </button>
      {slot.copyText.trim() && <Copy text={slot.copyText} label="Copy message" title="Copy" size={14} className="rx-bar-btn" />}
      <button type="button" className="rx-bar-btn" aria-label="More" title="More" aria-haspopup="dialog"
        onClick={(e) => { e.stopPropagation(); slot.openMore(e.currentTarget); }}>
        <Ellipsis size={15} />
      </button>
    </span>
  );
}

/** An agent's reply in a plain block (no width of its own: the reply sizes exactly as without it), carrying its badges
 *  and hover icons. No slot: just the words. */
export function ReactionAnchor({ children }: { children: ReactNode }) {
  const slot = useReactionSlot();
  if (!slot) return <>{children}</>;
  return (
    <div {...anchorProps(slot, 'agent')}>
      {children}
      <SlotBar side="agent" />
      <SlotBadges side="agent" />
    </div>
  );
}
