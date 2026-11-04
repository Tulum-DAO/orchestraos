/**
 * Reactions — reacting to a message in an agent's chat (spec docs/superpowers/specs/2026-11-02-message-reactions-design.md,
 * build step 2). ThreadView wraps each message that can be reacted to (lib/threadKit/view reactRef) in a ReactionRow:
 * it draws no element of its own (the thread's rows, their scroll anchoring and the reply's hand-off are untouched); it
 * gives the message's bubble its slot (ReactionBadges.tsx: badges, hover icons, the hold) and opens the menus.
 *
 *   Desktop (pointer: fine): hover the bubble, its icons show on the outer side (centred on it): 🙂 opens the quick row
 *     (👍 👎 ❤️ 😂 🎉 😮 + more), Copy, … (Copy text, Copy message id). A click on a badge gives that emoji, or takes
 *     yours back.
 *   Phone (pointer: coarse): hold the bubble 500 ms (lib/railAgents longPressControl): it lifts over the dimmed thread,
 *     the quick row above it and Copy below. A screen reader reaches the same sheet through the bubble's hidden "React"
 *     button. A tap on a badge says who gave it (and takes yours back).
 *   More: the full picker (EmojiPicker.tsx, its own chunk, loaded on first use; a failed load says so with a retry,
 *     inside the popover): search, categories, skin tones; a popover on desktop, a sheet from the bottom on a phone.
 *
 * One emoji per reactor: picking another replaces it, the same again removes it (lib/reactionState, the server agrees).
 * Every menu is drawn over the page (a portal), placed by lib/reactionLayout, takes the focus when it opens and gives it
 * back to what opened it when it closes, and closes on a pick, Escape, a press outside it, or (desktop) the person
 * scrolling (a wheel or a swipe; the thread following a streaming reply does not close it). The phone's sheet is modal:
 * the focus stays inside it.
 */
import { Component, lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { Copy as CopyIcon, Hash, SmilePlus } from 'lucide-react';
import { longPressControl } from '../../lib/railAgents';
import { badgeGroups, whoReacted, reactorName, QUICK_EMOJI, type BadgeGroup, type Reactor, type StoredReaction } from '../../lib/reactionState';
import { BAR_WIDTH, barOffsetTop, boxOf, liftLayout, popoverAt, type Box, type Size } from '../../lib/reactionLayout';
import { splitAttachments } from '../../lib/threadKit/markdown';
import {
  ReactionSlotContext, clipBoxOf, useCoarsePointer, useReactionsCtx, wordsBox, type PressHandlers, type ReactionSlot, type ReactSide,
} from './reactionsContext';
import { MessageText, type Skin } from './Message';
import { UserWords } from './Turns';
import { withRefs } from './RefText';
import './reactions.css';

const loadPicker = () => import('./EmojiPicker');
/** The picker's chunk. Replaced on a retry: React.lazy remembers a failed load for good. */
let EmojiPicker = lazy(loadPicker);

const QUICK: Size = { width: 7 * 38 + 8, height: 44 };
const MENU: Size = { width: 190, height: 84 };
const HOLD_MENU: Size = { width: 150, height: 46 };
const PICKER: Size = { width: 352, height: 420 };
const LEAVE_MS = 180;
/** A click on the dim this soon after the sheet opened is the lifting finger's own tap, not a dismissal. */
const GHOST_MS = 350;
/** Keys that scroll the thread when pressed outside a menu: a desktop menu closes on them as on a wheel. */
const SCROLL_KEYS = new Set(['PageUp', 'PageDown', 'Home', 'End', ' ', 'ArrowUp', 'ArrowDown']);
const FOCUSABLE = 'input, button:not([disabled]), [href], [tabindex]:not([tabindex="-1"])';

type Menu =
  | { kind: 'quick'; at: Box; view: Size }
  | { kind: 'more'; at: Box; view: Size }
  | { kind: 'picker'; at: Box; view: Size }
  | { kind: 'who'; at: Box; view: Size; emoji: string }
  | { kind: 'lift'; at: Box; view: Size };

const viewNow = (): Size => ({ width: window.innerWidth, height: window.innerHeight });
const boxOfEl = (el: Element): Box => boxOf(el.getBoundingClientRect());
const focusFirst = (root: HTMLElement | null) => {
  (root?.querySelector<HTMLElement>(FOCUSABLE) ?? root)?.focus({ preventScroll: true });
};

/** Accepted trade-off (as iMessage): on a phone a long-press on a bubble is the reaction hold, not the system's text
 *  selection or link menu; Copy is in the hold's menu.
 *
 *  The hold (lib/railAgents longPressControl: 500 ms, a move or an early lift cancels it), on the bubble the finger
 *  went down on. While it is in progress, and while the sheet it opened is up, that bubble is marked
 *  `data-rx-holding` (reactions.css: no selection or callout then; the system's own long-press otherwise). */
function holdOn(onHold: (el: HTMLElement) => void): { handlers: PressHandlers; release: () => void } {
  let held: HTMLElement | null = null;
  const unmark = () => held?.removeAttribute('data-rx-holding');
  const lp = longPressControl(() => { if (held) onHold(held); }, 500, { onEnd: (fired) => { if (!fired) unmark(); } });
  return {
    handlers: {
      ...lp.handlers,
      onTouchStart: (e) => {
        unmark();
        held = e.currentTarget as HTMLElement;
        held.setAttribute('data-rx-holding', '');
        lp.handlers.onTouchStart(e);
      },
    },
    release: () => { unmark(); held = null; lp.reset(); },
  };
}

export function ReactionRow({ reactRef, side, list, text, skin, children }: {
  /** The ref a reaction is written under (view.ts NodeRow.ref). */
  reactRef: string;
  side: ReactSide;
  list: readonly StoredReaction[];
  /** The message's words, as written: Copy, and the lifted copy on a phone. */
  text: string;
  skin: Skin;
  children: ReactNode;
}) {
  const ctx = useReactionsCtx();
  const coarse = useCoarsePointer();
  const [menu, setMenu] = useState<Menu | null>(null);
  const [barAt, setBarAt] = useState<ReactionSlot['barAt']>(null);
  const me = ctx?.me ?? null;
  const groups = useMemo(() => badgeGroups(list, me), [list, me]);
  const copyText = side === 'user' ? splitAttachments(text).text : text;
  // What opened the menu: the focus goes back to it when the menu closes.
  const opener = useRef<HTMLElement | null>(null);
  // One hold per row: it remembers its finger across renders. The bubble it started on is what lifts.
  const hold = useMemo(() => holdOn((el) => {
    navigator.vibrate?.(8);   // (no opener: close() already cleared it, and a hold has nothing to give the focus back to)
    setMenu({ kind: 'lift', at: boxOfEl(el), view: viewNow() });
  }), []);
  // Stable, so a menu's closing fade is never restarted by a re-render.
  const close = useCallback(() => {
    // Give the focus back only if it is still ours (inside the menu) or nowhere: a press elsewhere (the composer) that
    // dismissed the menu keeps the focus it took.
    const active = document.activeElement;
    const ours = !active || active === document.body || !!active.closest('.rx-pop, .rx-lift');
    setMenu(null);
    hold.release();
    const back = opener.current;
    opener.current = null;
    if (ours && back?.isConnected) back.focus({ preventScroll: true });
  }, [hold]);
  if (!ctx) return <>{children}</>;
  const pick = (emoji: string) => { close(); ctx.toggle(reactRef, emoji); };
  const open = (kind: 'quick' | 'more', el: HTMLElement) => { opener.current = el; setMenu({ kind, at: boxOfEl(el), view: viewNow() }); };
  const nameOf = ctx.nameOf;
  const slot: ReactionSlot = {
    side, groups, coarse, copyText, open: !!menu, barAt, press: coarse ? hold.handlers : null,
    onEnter: (el) => {
      const box = side === 'agent' && !el.hasAttribute('data-rx-bubble') ? wordsBox(el) : boxOfEl(el);
      const top = barOffsetTop(side, box, clipBoxOf(el));
      // Outside: centred beside the words. Under (no room outside): under the END of the words, not the column's edge.
      setBarAt(top !== null
        ? { out: true, top, left: side === 'agent' ? Math.round(box.width) + 12 : undefined }
        : { out: false, left: side === 'agent' ? Math.max(0, Math.round(box.width) - BAR_WIDTH) : undefined });
    },
    onBadge: (g, el) => {
      if (!coarse) { pick(g.emoji); return; }
      opener.current = el;
      setMenu({ kind: 'who', emoji: g.emoji, at: boxOfEl(el), view: viewNow() });
    },
    openQuick: (el) => open('quick', el),
    openMore: (el) => open('more', el),
    openLift: (bubble, trigger) => { opener.current = trigger; setMenu({ kind: 'lift', at: boxOfEl(bubble), view: viewNow() }); },
    whoTitle: (g) => whoReacted(g, me, nameOf),
  };
  const mine = groups.find((g) => g.mine)?.emoji ?? null;
  const align = side === 'agent' ? 'start' : 'end';
  return (
    <ReactionSlotContext.Provider value={slot}>
      {children}
      {menu?.kind === 'quick' && (
        <Popover at={popoverAt(menu.at, QUICK, menu.view, { align })} size={QUICK} label="React" onClose={close}>
          <QuickRow mine={mine} onPick={pick} onMore={() => setMenu({ ...menu, kind: 'picker' })} />
        </Popover>
      )}
      {menu?.kind === 'more' && (
        <Popover at={popoverAt(menu.at, MENU, menu.view, { align })} size={MENU} label="More" className="rx-menu" onClose={close}>
          <MenuButton icon={<CopyIcon size={14} />} label="Copy text" onClick={() => { void copy(copyText); close(); }} />
          <MenuButton icon={<Hash size={14} />} label="Copy message id" onClick={() => { void copy(reactRef); close(); }} />
        </Popover>
      )}
      {menu?.kind === 'picker' && (
        <Popover at={coarse ? { left: 0, top: 0 } : popoverAt(menu.at, PICKER, menu.view, { align })} size={PICKER}
          label="Pick an emoji" className="rx-picker" sheet={coarse} onClose={close}>
          <PickerBoundary render={(Picker) => (
            <Suspense fallback={<div className="rx-empty">Loading emoji…</div>}>
              <Picker onPick={pick} autoFocus={!coarse} />
            </Suspense>
          )} />
        </Popover>
      )}
      {menu?.kind === 'who' && (() => {
        const g = groups.find((x) => x.emoji === menu.emoji);
        if (!g) return null;
        const size = { width: 240, height: 48 + 26 * g.reactors.length };
        return (
          <Popover at={popoverAt(menu.at, size, menu.view, { align })} size={size} label={`Who reacted ${g.emoji}`} className="rx-who" onClose={close}>
            <WhoList g={g} me={me} nameOf={nameOf} onTakeBack={g.mine ? () => pick(g.emoji) : undefined} />
          </Popover>
        );
      })()}
      {menu?.kind === 'lift' && (
        <LiftSheet at={menu.at} view={menu.view} side={side} skin={skin} text={copyText} mine={mine}
          onPick={pick} onMore={() => setMenu({ ...menu, kind: 'picker' })} onClose={close} />
      )}
    </ReactionSlotContext.Provider>
  );
}

/** The picker's chunk failed to load (offline after an update, a bad deploy): say so inside the popover, offer a retry,
 *  and never let it reach the thread. `render` is handed the CURRENT lazy picker each time it draws, so a retry draws
 *  the fresh one (not the failed one an already-built child element would still hold). */
class PickerBoundary extends Component<{ render: (Picker: typeof EmojiPicker) => ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch() { /* shown in the popover; nothing else to do */ }
  retry = () => { EmojiPicker = lazy(loadPicker); this.setState({ failed: false }); };
  render() {
    if (!this.state.failed) return this.props.render(EmojiPicker);
    return (
      <div className="rx-empty rx-failed" role="alert">
        <p>Couldn’t load emoji — try again</p>
        <button type="button" className="rx-retry" onClick={this.retry}>Try again</button>
      </div>
    );
  }
}

async function copy(text: string): Promise<void> {
  try { await navigator.clipboard.writeText(text); } catch { /* the browser refused the clipboard */ }
}

function MenuButton({ icon, label, onClick }: { icon: ReactNode; label: string; onClick: () => void }) {
  return <button type="button" onClick={onClick}>{icon}{label}</button>;
}

/** The six quick emoji and "more". The viewer's own is marked (a tap on it takes it back). */
function QuickRow({ mine, onPick, onMore }: { mine: string | null; onPick: (emoji: string) => void; onMore: () => void }) {
  return (
    <>
      {QUICK_EMOJI.map((e) => (
        <button key={e} type="button" aria-label={`React ${e}`} data-mine={mine === e ? '' : undefined} onClick={() => onPick(e)}>{e}</button>
      ))}
      <button type="button" className="rx-more" aria-label="More emoji" title="More emoji" onClick={onMore}><SmilePlus size={20} /></button>
    </>
  );
}

/** A phone's answer to a tap on a badge: who gave it, and a way to take yours back. */
function WhoList({ g, me, nameOf, onTakeBack }: { g: BadgeGroup; me: Reactor | null; nameOf: (r: Reactor) => string; onTakeBack?: () => void }) {
  return (
    <>
      {g.reactors.map((r) => (
        <div key={`${r.kind}:${r.id}`} className="rx-who-line"><span>{g.emoji}</span><span>{reactorName(r, me, nameOf)}</span></div>
      ))}
      {onTakeBack && (
        <div className="rx-menu" style={{ padding: 0, minWidth: 0 }}>
          <button type="button" onClick={onTakeBack}>Remove my {g.emoji}</button>
        </div>
      )}
    </>
  );
}

/** Close with a short fade (never a jump): `leaving` marks it, then it goes. */
function useLeave(onClose: () => void): [boolean, () => void] {
  const [leaving, setLeaving] = useState(false);
  useEffect(() => {
    if (!leaving) return;
    const t = setTimeout(onClose, LEAVE_MS);
    return () => clearTimeout(t);
  }, [leaving, onClose]);
  const leave = useCallback(() => setLeaving(true), []);
  return [leaving, leave];
}

/** A menu over the page at a fixed place. Takes the focus when it opens (its first control); closed by Escape, a press
 *  outside it, or (unless it is a sheet) the person scrolling the page: a wheel, a swipe, or a scrolling key outside
 *  it. A scroll the page makes itself (the thread following a streaming reply) leaves it open. */
function Popover({ at, size, label, className = 'rx-quick', sheet = false, onClose, children }: {
  at: { left: number; top: number }; size: Size; label: string; className?: string; sheet?: boolean;
  onClose: () => void; children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [leaving, leave] = useLeave(onClose);
  useEffect(() => { focusFirst(ref.current); }, []);
  useEffect(() => {
    const outside = (e: Event) => { if (!ref.current?.contains(e.target as Node)) leave(); };
    const key = (e: KeyboardEvent) => {
      if (e.key === 'Escape') leave();
      else if (!sheet && SCROLL_KEYS.has(e.key) && !ref.current?.contains(e.target as Node)) leave();
    };
    document.addEventListener('pointerdown', outside, true);
    document.addEventListener('keydown', key);
    if (!sheet) {
      window.addEventListener('wheel', outside, { capture: true, passive: true });
      window.addEventListener('touchmove', outside, { capture: true, passive: true });
    }
    return () => {
      document.removeEventListener('pointerdown', outside, true);
      document.removeEventListener('keydown', key);
      window.removeEventListener('wheel', outside, true);
      window.removeEventListener('touchmove', outside, true);
    };
  }, [leave, sheet]);
  return createPortal(
    <div ref={ref} role="dialog" aria-label={label} tabIndex={-1} className={`rx-pop touch-compact ${className}`} data-sheet={sheet ? '' : undefined}
      data-leaving={leaving ? '' : undefined}
      style={sheet ? undefined : { left: at.left, top: at.top, width: className === 'rx-quick' ? undefined : size.width }}>
      {children}
    </div>,
    document.body,
  );
}

/** The phone hold: the message lifted where it is over the dimmed thread, the quick row above it, Copy below. Modal:
 *  the focus starts on the first emoji and stays inside until it closes. */
function LiftSheet({ at, view, side, skin, text, mine, onPick, onMore, onClose }: {
  at: Box; view: Size; side: ReactSide; skin: Skin; text: string; mine: string | null;
  onPick: (emoji: string) => void; onMore: () => void; onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [leaving, leave] = useLeave(onClose);
  // The finger that lifted the message is still coming up: its tap lands on the dim. Only a press that STARTS on the
  // dim, and not in the first GHOST_MS, dismisses it.
  const openedAt = useRef(0);
  const downOnDim = useRef(false);
  useEffect(() => {
    openedAt.current = performance.now();
    focusFirst(ref.current?.querySelector('.rx-quick') ?? null);
  }, []);
  useEffect(() => {
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') leave(); };
    document.addEventListener('keydown', key);
    return () => document.removeEventListener('keydown', key);
  }, [leave]);
  const trap = (e: ReactKeyboardEvent) => {
    if (e.key !== 'Tab' || !ref.current) return;
    const items = [...ref.current.querySelectorAll<HTMLElement>(FOCUSABLE)];
    if (!items.length) return;
    const first = items[0], last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  };
  const l = liftLayout(at, view, QUICK.height, HOLD_MENU.height);
  const quickLeft = popoverAt(at, QUICK, view, { align: side === 'agent' ? 'start' : 'end' }).left;
  const menuLeft = popoverAt(at, HOLD_MENU, view, { align: side === 'agent' ? 'start' : 'end' }).left;
  // Modern's bare reply is lifted on a padded backing; a bubble (the person's, the agent's in Bubbles) as it is.
  const bare = side === 'agent' && skin !== 'bubbles';
  const pad = bare ? 8 : 0;
  return createPortal(
    <div ref={ref} className="rx-lift touch-compact" role="dialog" aria-modal="true" aria-label="React to message"
      data-leaving={leaving ? '' : undefined} onKeyDown={trap}
      // the lifting finger's compat mousedown lands on the lifted copy or the dim: it must not take the focus out
      onMouseDown={(e) => e.preventDefault()}>
      <div className="rx-dim" aria-hidden onPointerDown={() => { downOnDim.current = true; }}
        onClick={() => { if (downOnDim.current && performance.now() - openedAt.current >= GHOST_MS) leave(); downOnDim.current = false; }} />
      <div className="rx-pop rx-quick" style={{ left: quickLeft, top: l.quickTop }}>
        <QuickRow mine={mine} onPick={onPick} onMore={onMore} />
      </div>
      {/* A copy of the message, not the message: the thread under the dim is never moved. No slot: no badges, no hold. */}
      <ReactionSlotContext.Provider value={null}>
        <div className={`rx-lifted ${skin === 'modern' ? 'kit-modern' : ''}`} data-side={side} data-bare={bare ? '' : undefined} data-clipped={l.clipped ? '' : undefined}
          style={{ left: at.left - pad, top: l.bubbleTop - pad, width: at.width + 2 * pad, maxHeight: l.bubbleHeight + 2 * pad }}>
          {side === 'user'
            ? <UserWords skin={skin === 'modern' ? 'modern' : 'bubbles'}>{withRefs(text, skin)}</UserWords>
            : <MessageText text={text} skin={skin} />}
        </div>
      </ReactionSlotContext.Provider>
      <div className="rx-pop rx-menu" style={{ left: menuLeft, top: l.menuTop, minWidth: HOLD_MENU.width }}>
        <MenuButton icon={<CopyIcon size={14} />} label="Copy" onClick={() => { void copy(text); leave(); }} />
      </div>
      {/* For a screen reader (it has no Escape key and cannot see the dim): hidden until it is focused. */}
      <button type="button" className="rx-sr-react" aria-label="Close" data-label="Close" onClick={leave} />
    </div>,
    document.body,
  );
}

/** Why the last reaction was not saved, for a few seconds, at the foot of the thread. */
export function ReactionNotice() {
  const ctx = useReactionsCtx();
  return ctx?.error ? <div role="status" className="rx-notice" data-anchor-skip>{ctx.error}</div> : null;
}
