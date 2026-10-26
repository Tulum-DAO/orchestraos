/**
 * FloatingArturo — Arturo's one button, bottom right, on every page. Pinned to the SCREEN
 * (fixed), in the corner, or right above a bar docked at the bottom when that bar runs under the corner, so
 * the bar keeps its full width (lib/arturoPlacement.ts). It moves only when such a
 * bar appears, goes, or changes size, never on a timer. Published as --arturo-btn-bottom so Arturo's pane
 * opens right above the button.
 */
import { useEffect, useState } from 'react';
import { ArturoButton } from './ArturoButton';
import { buttonBottom } from '../../lib/arturoPlacement';

const KEYBOARD_MIN_PX = 80;   // the visible area this much shorter than the screen = a keyboard is up

export function FloatingArturo() {
  const [bottom, setBottom] = useState(12);
  useEffect(() => {
    const root = document.documentElement;
    let frame = 0;
    const measured = new Set<Element>();
    const ro = new ResizeObserver(() => schedule());
    const place = () => {
      frame = 0;
      // While the phone keyboard is up, nothing moves: the bars ride up on it, and following them would lift the
      // button and Arturo's pane above it with them (only an agent's dev mode rides the keyboard).
      const vv = window.visualViewport;
      if (vv && window.innerHeight - vv.height - vv.offsetTop > KEYBOARD_MIN_PX) return;
      const bars = Array.from(document.querySelectorAll('[data-arturo-avoid]'));
      for (const el of bars) if (!measured.has(el)) { measured.add(el); ro.observe(el); }
      const b = buttonBottom(window.innerWidth, window.innerHeight, bars.map((el) => el.getBoundingClientRect()));
      setBottom(b);
      root.style.setProperty('--arturo-btn-bottom', `${b}px`);
    };
    const schedule = () => { if (!frame) frame = requestAnimationFrame(place); };
    const mo = new MutationObserver(schedule);     // a bar mounted or unmounted
    mo.observe(document.body, { childList: true, subtree: true });
    window.addEventListener('resize', schedule);
    window.visualViewport?.addEventListener('resize', schedule);
    place();
    return () => {
      mo.disconnect(); ro.disconnect(); cancelAnimationFrame(frame);
      window.removeEventListener('resize', schedule);
      window.visualViewport?.removeEventListener('resize', schedule);
      root.style.removeProperty('--arturo-btn-bottom');
    };
  }, []);
  return (
    <div className="fixed right-3 z-[55]" style={{ bottom: `calc(env(safe-area-inset-bottom, 0px) + ${bottom}px)` }}>
      <ArturoButton small />
    </div>
  );
}
