/**
 * ArturoButton — Arturo's one spot, on every page. It replaced the floating "Ask Arturo" pill,
 * which covered the text and the composer on phones however it was nudged. Tapping it opens Arturo's pane.
 * The button IS Arturo: his own live avatar (the look and brain badge set for him, the same
 * face as his sidebar row, gestures and all), not an orange mic circle.
 */
import { useArturoUi } from '../../stores/arturoUi';
import { useAssistantName } from '../../lib/assistantName';
import { AgentAvatar } from '../avatar/AgentAvatar';
import { ARTURO_ID } from '../avatar/agentState';

export function ArturoButton({ small = false }: { small?: boolean } = {}) {
  const open = useArturoUi((s) => s.open);
  const aname = useAssistantName();
  const setOpen = useArturoUi((s) => s.setOpen);
  const exchanges = useArturoUi((s) => s.exchanges);
  const size = small ? 48 : 40;
  return (
    <button
      data-arturo-toggle
      type="button"
      onClick={() => setOpen(!open)}
      aria-label={open ? `Close ${aname}` : `Ask ${aname}`}
      aria-expanded={open}
      title={`Ask ${aname}`}
      // No fill of its own: the avatar is the button. A soft shadow lifts it off the page; open adds a ring
      // (the pane above is his).
      style={{ width: size, height: size }}
      className={`relative shrink-0 rounded-full flex items-center justify-center bg-transparent p-0 ${
        small ? 'drop-shadow-[0_4px_10px_rgba(0,0,0,0.35)]' : ''
      } ${open ? 'ring-2 ring-[#d97757]/50 ring-offset-2 ring-offset-background' : ''}`}
    >
      {/* 'drawer': his look and badge, animated, no status dot; selected = the eyes wander and follow the pointer */}
      <AgentAvatar agentId={ARTURO_ID} role="drawer" size={size} selected label={aname} className="pointer-events-none" />
      {exchanges > 0 && !open && (
        <span className="absolute -top-0.5 -right-0.5 min-w-4 h-4 px-1 rounded-full bg-tone-200 text-tone-900 text-[10px] leading-4 font-semibold text-center">
          {exchanges}
        </span>
      )}
    </button>
  );
}
