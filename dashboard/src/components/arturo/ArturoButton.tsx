/**
 * ArturoButton — Arturo's one spot: a circle in the top bar, next to the notification bell, on
 * every page (Shaw, 2026-10-10). It replaced the floating "Ask Arturo" pill, which covered the
 * text and the composer on phones however it was nudged. Tapping it opens the pane from the top.
 */
import { Mic } from 'lucide-react';
import { useArturoUi } from '../../stores/arturoUi';

export function ArturoButton() {
  const open = useArturoUi((s) => s.open);
  const setOpen = useArturoUi((s) => s.setOpen);
  const exchanges = useArturoUi((s) => s.exchanges);
  return (
    <button
      type="button"
      onClick={() => setOpen(!open)}
      aria-label={open ? 'Close Arturo' : 'Ask Arturo'}
      aria-expanded={open}
      title="Ask Arturo"
      // 40px on desktop, the same box as the top-bar icons beside it; 44px under the touch rule
      // (touch-circle), so never an oval. Its colour, not its size, is what sets it apart.
      className={`touch-circle min-[769px]:h-10 min-[769px]:w-10 relative shrink-0 rounded-full flex items-center justify-center border transition-colors ${
        // Arturo's own colour, filled, in both themes: the one control in the row that is not a plain
        // icon. Open adds a ring (the pane below is its).
        open ? 'bg-[#d97757] border-transparent text-white ring-2 ring-[#d97757]/40 ring-offset-2 ring-offset-background' : 'bg-[#d97757] border-transparent text-white hover:bg-[#c96747]'
      }`}
    >
      <Mic size={20} />
      {exchanges > 0 && !open && (
        <span className="absolute -top-0.5 -right-0.5 min-w-4 h-4 px-1 rounded-full bg-neutral-200 text-neutral-900 text-[10px] leading-4 font-semibold text-center">
          {exchanges}
        </span>
      )}
    </button>
  );
}
