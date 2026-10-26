import { useEffect, useRef, useState, type ReactNode } from 'react';
import { X } from 'lucide-react';

/**
 * A window over the page: drag it by its title bar, resize it from its corner, click it to bring it to the
 * front. Several can be open at once (the project files viewer opens one per file). On a phone it is a
 * full-screen sheet instead (no room to drag).
 */
export default function FloatingWindow({ title, subtitle, x, y, z, onFocus, onClose, children }: {
  title: string; subtitle?: string; x: number; y: number; z: number;
  onFocus: () => void; onClose: () => void; children: ReactNode;
}) {
  const [pos, setPos] = useState({ x, y });
  const drag = useRef<{ dx: number; dy: number } | null>(null);
  const narrow = typeof window !== 'undefined' && window.innerWidth < 640;

  useEffect(() => {
    const move = (e: PointerEvent) => {
      if (!drag.current) return;
      const nx = Math.max(-200, Math.min(window.innerWidth - 120, e.clientX - drag.current.dx));
      const ny = Math.max(0, Math.min(window.innerHeight - 40, e.clientY - drag.current.dy));
      setPos({ x: nx, y: ny });
    };
    const up = () => { drag.current = null; };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    return () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); };
  }, []);

  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    if (narrow) { window.addEventListener('keydown', esc); return () => window.removeEventListener('keydown', esc); }
  }, [narrow, onClose]);

  const style = narrow
    ? { zIndex: z, inset: 0 }
    : { zIndex: z, left: pos.x, top: pos.y, width: 640, height: 480, resize: 'both' as const, minWidth: 320, minHeight: 200 };

  return (
    <div role="dialog" aria-label={title} onPointerDown={onFocus} style={style}
         className="fixed flex flex-col overflow-hidden rounded-xl border border-neutral-700 bg-neutral-900 shadow-2xl shadow-black/60">
      <div className="flex items-center gap-2 px-3 py-2 border-b border-neutral-800 bg-neutral-900/95 select-none cursor-move touch-none"
           onPointerDown={(e) => { if (!narrow) drag.current = { dx: e.clientX - pos.x, dy: e.clientY - pos.y }; }}>
        <div className="min-w-0 flex-1">
          <div className="text-sm text-neutral-100 font-medium truncate">{title}</div>
          {subtitle && <div className="text-[11px] text-neutral-500 font-mono truncate">{subtitle}</div>}
        </div>
        <button aria-label={`Close ${title}`} onPointerDown={(e) => e.stopPropagation()} onClick={onClose}
                className="p-1 rounded text-neutral-400 hover:text-white hover:bg-neutral-800"><X size={16} /></button>
      </div>
      <div className="flex-1 min-h-0 overflow-auto">{children}</div>
    </div>
  );
}
