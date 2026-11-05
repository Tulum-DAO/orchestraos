/**
 * useScreenCard — how a card renderer reports into the one cards-on-screen context (lib/screenCards.ts): what the
 * card is (re-reported whenever it changes: a part paged, a pick made, an answer sent), whether it is in view (an
 * IntersectionObserver on its frame), when it was last touched (a press, a key or focus inside it), and that it left
 * (unmounted). The surface comes from the nearest CardSurface provider: a page or panel says where its cards are
 * drawn once, and every card inside it reports that.
 *
 * `answer` is how an answer Arturo hands back is applied to this card: through the card's own submit path, so it
 * goes the same route as a tap, with the same send states.
 */
import { createContext, useContext, useEffect, useId, useLayoutEffect, useRef, type RefObject } from 'react';
import { screenDispatch, setAnswerer, type ScreenAnswer, type ScreenCard, type Surface } from '../screenCards';

export const CardSurface = createContext<Surface>('chat');

/** Part of a card in view, enough to count: a sliver at the edge of a scroller is not "on screen". */
const SEEN_RATIO = 0.25;

export function useScreenCard(ref: RefObject<HTMLElement | null>, card: ScreenCard | null,
  answer?: (a: ScreenAnswer) => Promise<string | null> | string | null) {
  const surface = useContext(CardSurface);
  const uid = useId();
  const key = card ? `${surface}:${card.id}:${uid}` : null;
  const json = card ? JSON.stringify(card) : '';

  // What the card is: re-reported only when it changed (the store ignores an identical report too).
  useEffect(() => {
    if (!key || !card) return;
    screenDispatch({ type: 'report', key, card, surface });
  }, [key, json, surface]); // eslint-disable-line react-hooks/exhaustive-deps

  // Gone when the card unmounts or becomes another card.
  useEffect(() => {
    if (!key) return;
    return () => screenDispatch({ type: 'gone', key });
  }, [key]);

  // In view, and touched.
  useEffect(() => {
    const el = ref.current;
    if (!key || !el) return;
    let io: IntersectionObserver | null = null;
    if (typeof IntersectionObserver !== 'undefined') {
      io = new IntersectionObserver((entries) => {
        for (const e of entries) {
          const shown = e.isIntersecting && (e.intersectionRatio >= SEEN_RATIO || e.intersectionRect.height >= 160);
          screenDispatch({ type: 'visible', key, visible: shown, now: Date.now() });
        }
      }, { threshold: [0, SEEN_RATIO, 0.5, 1] });
      io.observe(el);
    } else {
      screenDispatch({ type: 'visible', key, visible: true, now: Date.now() });
    }
    const touch = () => screenDispatch({ type: 'touch', key, now: Date.now() });
    el.addEventListener('pointerdown', touch);
    el.addEventListener('focusin', touch);
    el.addEventListener('keydown', touch);
    return () => {
      io?.disconnect();
      el.removeEventListener('pointerdown', touch);
      el.removeEventListener('focusin', touch);
      el.removeEventListener('keydown', touch);
    };
  }, [key, ref]);

  // How an answer from Arturo reaches this card: always the latest closure (the card's current part and state).
  const latest = useRef(answer);
  useLayoutEffect(() => { latest.current = answer; });
  useEffect(() => {
    if (!key) return;
    setAnswerer(key, (a) => (latest.current ? latest.current(a) : 'This card cannot be answered from here.'));
    return () => setAnswerer(key, null);
  }, [key]);
}
