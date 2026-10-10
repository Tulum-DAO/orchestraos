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
      // touch-circle (index.css): 36px, 44px under the touch rule's own query, so never an oval.
      className={`touch-circle relative shrink-0 rounded-full flex items-center justify-center border transition-colors ${
        open ? 'bg-[#d97757] border-[#d97757] text-white' : 'border-neutral-700 bg-neutral-900 text-[#d97757] hover:bg-neutral-800'
      }`}
    >
      <Mic size={18} />
      {exchanges > 0 && !open && (
        <span className="absolute -top-0.5 -right-0.5 min-w-4 h-4 px-1 rounded-full bg-neutral-200 text-neutral-900 text-[10px] leading-4 font-semibold text-center">
          {exchanges}
        </span>
      )}
    </button>
  );
}
