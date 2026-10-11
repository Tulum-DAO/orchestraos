/**
 * FloatingArturo — Arturo's one button: a small circle, bottom right, on every page (Shaw,
 * 2026-10-11). It sits just above whatever CLI is docked at the bottom (any element marked
 * data-arturo-avoid: the agent page's composer pill, a Dev key bar, the Agents-list panel's input),
 * so it never covers it. The lift is measured, not passed in: a page only marks its CLI.
 * Published as --arturo-lift so Arturo's pane opens right above the button.
 */
import { useEffect, useRef } from 'react';
import { ArturoButton } from './ArturoButton';
import { arturoLift } from '../../lib/arturoLift';

export function FloatingArturo() {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const root = document.documentElement;
    let timer = 0;
    const place = () => {
      const host = el.offsetParent as HTMLElement | null;
      const hostBottom = host ? host.getBoundingClientRect().bottom : window.innerHeight;
      const tops = Array.from(document.querySelectorAll<HTMLElement>('[data-arturo-avoid]'))
        .map((n) => n.getBoundingClientRect())
        .filter((r) => r.height > 0 && r.bottom > 0)
        .map((r) => r.top);
      root.style.setProperty('--arturo-lift', `${arturoLift(hostBottom, tops)}px`);
    };
    // Cheap and robust: a CLI appears, grows, or moves with the keyboard without telling anyone.
    const tick = () => { place(); timer = window.setTimeout(tick, 250); };
    tick();
    return () => { window.clearTimeout(timer); root.style.removeProperty('--arturo-lift'); };
  }, []);
  return (
    <div ref={ref} className="absolute right-3 z-[55]"
      style={{ bottom: 'calc(max(var(--arturo-lift, 0px), env(safe-area-inset-bottom, 0px)) + 4px)' }}>
      <ArturoButton small />
    </div>
  );
}
