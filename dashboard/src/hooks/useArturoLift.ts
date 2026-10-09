/**
 * useArturoLift — lift the corner "Ask Arturo" pill clear of a bottom-docked control row that it
 * would otherwise sit ON (it covered the Agents-page panel's key row on phones, the way it covered
 * the agent page's Send before #350).
 *
 * Only when the pill actually overlaps the box horizontally: on a desktop the panel is a centred
 * modal and the pill's corner is clear of it, so nothing moves there. While lifted it publishes
 *   --agent-composer-h on <html>  (arturo.css lifts the pill above the dock by this)
 *   --composer-h on the box        (TranscriptChatView pads by this, so the pill never covers the
 *                                   last message)
 * and removes both when inactive or unmounted, so no other page inherits a lift.
 */
import { useEffect, type RefObject } from 'react';

export function useArturoLift(boxRef: RefObject<HTMLElement | null>, dockSelector: string, active: boolean) {
  useEffect(() => {
    if (!active) return;
    const root = document.documentElement;
    const clear = () => {
      root.style.removeProperty('--agent-composer-h');
      boxRef.current?.style.removeProperty('--composer-h');
    };
    const apply = () => {
      const box = boxRef.current;
      const dock = box?.querySelector<HTMLElement>(dockSelector);
      const pill = document.querySelector<HTMLElement>('.arturo-pill');
      if (!box || !dock || !pill) { clear(); return; }
      const a = pill.getBoundingClientRect();
      const b = box.getBoundingClientRect();
      if (!(a.left < b.right && a.right > b.left)) { clear(); return; }
      const lift = Math.ceil(window.innerHeight - dock.getBoundingClientRect().top);
      root.style.setProperty('--agent-composer-h', `${Math.max(0, lift)}px`);
      box.style.setProperty('--composer-h', `${Math.ceil(a.height) + 8}px`);
    };
    apply();
    const ro = new ResizeObserver(apply);
    if (boxRef.current) ro.observe(boxRef.current);
    window.addEventListener('resize', apply);
    return () => { ro.disconnect(); window.removeEventListener('resize', apply); clear(); };
  }, [active, boxRef, dockSelector]);
}
