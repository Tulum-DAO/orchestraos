/**
 * SidebarResizer — the desktop sidebar's right edge (Settings › Display › Sidebar width). Drag it for more or less
 * room, 200–360 px; text never resizes while dragging. Double-click puts back the text-driven default (240 × text
 * size, capped at 300). Remembered on this device (orchestra.sidebar.width). Arrow keys move it 8 px (Right/Up wider);
 * Home is the narrowest, End the widest.
 * While dragging only the CSS variable moves; the width is stored and every reader told once, on release.
 */
import { useRef, type PointerEvent } from 'react';
import { applyStoredDisplay, previewSidebarWidth, useDeviceSetting, useDisplay } from '../lib/deviceSettings';
import { clampSidebarWidth, defaultSidebarWidth, SIDEBAR_MAX, SIDEBAR_MIN } from '../lib/displaySettings';

export function SidebarResizer() {
  const [width, set] = useDeviceSetting('sidebarWidth');
  const { textScale } = useDisplay();
  const current = width ?? defaultSidebarWidth(textScale);
  const drag = useRef<{ x: number; w: number; now: number } | null>(null);
  const down = (e: PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = { x: e.clientX, w: current, now: current };
  };
  const move = (e: PointerEvent<HTMLDivElement>) => {
    if (!drag.current) return;
    const w = clampSidebarWidth(drag.current.w + e.clientX - drag.current.x);
    if (w === drag.current.now) return;
    drag.current.now = w;
    previewSidebarWidth(w);
    e.currentTarget.setAttribute('aria-valuenow', String(w));
  };
  const end = () => {
    const d = drag.current;
    drag.current = null;
    if (d && d.now !== d.w) set(d.now);
  };
  const cancel = () => { drag.current = null; applyStoredDisplay(); };   // put back the stored width
  return (
    <div role="separator" aria-orientation="vertical" aria-label="Sidebar width" tabIndex={0}
         aria-valuemin={SIDEBAR_MIN} aria-valuemax={SIDEBAR_MAX} aria-valuenow={current}
         title="Drag to resize · double-click to reset" data-sidebar-resizer
         onPointerDown={down} onPointerMove={move} onPointerUp={end} onPointerCancel={cancel}
         onDoubleClick={() => set(null)}
         onKeyDown={(e) => {
           const next = e.key === 'Home' ? SIDEBAR_MIN : e.key === 'End' ? SIDEBAR_MAX
             : e.key === 'ArrowRight' || e.key === 'ArrowUp' ? current + 8 : e.key === 'ArrowLeft' || e.key === 'ArrowDown' ? current - 8 : null;
           if (next === null) return;
           e.preventDefault();
           set(clampSidebarWidth(next));
         }}
         className="absolute inset-y-0 -right-1 z-10 w-2 cursor-col-resize outline-none hover:bg-neutral-300/60 dark:hover:bg-neutral-700/60 focus-visible:bg-neutral-400/60" />
  );
}
