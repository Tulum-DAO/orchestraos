import { useContext, type Ref } from 'react';
import { Menu, MessageSquare, Settings } from 'lucide-react';
import { HeaderEndSlotContext } from '../../lib/headerSlot';
import { GenChip } from '../GenChip';
import AgentIdentityPill from '../avatar/AgentIdentityPill';
import { useAgentRecord } from '../avatar/agentState';

interface TopBarProps {
  onMenuOpen: () => void;
  /** The seat this page belongs to: its face and name in a pill in the middle, as on Arturo's page (with the gen
   *  chip beside it). Tapping the pill opens the agent's panel, as the gear does. */
  seat?: { id: string; generation?: unknown; name?: string };
  /** Chat (the transcript) or Dev (the seat's live terminal), chosen in the agent's panel. */
  mode?: 'chat' | 'dev';
  /** The gear: opens the agent's panel (name, avatar, voice, model, chat style ...). Shown only with a handler. */
  onSettings?: () => void;
  settingsOpen?: boolean;
  settingsRef?: Ref<HTMLButtonElement>;
  /** Dev only: one clear way back to the chat (the chat style goes back to its default). */
  onBackToChat?: () => void;
}

export function TopBar({ onMenuOpen, seat, mode = 'chat', onSettings, settingsOpen, settingsRef, onBackToChat }: TopBarProps) {
  const registerEndSlot = useContext(HeaderEndSlotContext);
  const rec = useAgentRecord(seat?.id ?? '');


  return (
    // Three columns: the menu button, the seat's pill, the controls. The two ends are equal (1fr each) while they fit,
    // so the pill sits in the middle of the bar's full width, phone and desktop; neither end ever goes below its own
    // width, so with a long name the pill truncates in the room left between them rather than covering them.
    // Even 6px top and bottom (53px with the 40px row and the border: the height it always had, now centred).
    // No safe-top here: it REPLACED the top padding (0 on a desktop, so the row sat flush at the top) and <main>
    // already adds the notch inset on a phone (here it was counted twice).
    <div className="touch-compact sticky top-0 z-20 grid grid-cols-[minmax(max-content,1fr)_minmax(0,auto)_minmax(max-content,1fr)] items-center gap-2 px-4 py-1.5 border-b bg-background border-border relative"
         data-top-bar>
      <div className="flex items-center justify-self-start">
      {/* The desktop sidebar is permanently visible (DashboardLayout renders it at md+), so this
          drawer trigger is a SECOND nav entry there. It is the mobile affordance only. */}
      <button
        onClick={onMenuOpen}
        className="md:hidden p-2 text-foreground hover:bg-muted rounded-lg transition-colors"
        aria-label="Open menu"
      >
        <Menu size={24} />
      </button>
      </div>

      {/* The middle column: centred on the bar, as Arturo's title is, and truncating to fit between the ends. */}
      {seat ? (
        <div className="flex items-center justify-center gap-2 min-w-0" data-seat-header={seat.id}>
          <AgentIdentityPill agentId={seat.id} name={seat.name || seat.id} size={32} className="min-w-0"
            title={seat.name && seat.name !== seat.id ? seat.id : undefined}
            onClick={onSettings} aria-label={`${seat.name || seat.id}: open settings`} aria-expanded={!!settingsOpen}
            aria-controls="arturo-panel"
            avatar={{ agentId: `${seat.id}:pill`, sleepAfterMs: 0, state: rec.status === 'working' ? 'working' : 'idle',
                      shape: rec.shape, color: rec.color, eyeColor: rec.eyeColor, provider: rec.provider }} />
          <GenChip generation={seat.generation} />
        </div>
      ) : <div />}


      {/* DashboardLayout owns the one shared notification bell (#400) and portals it into the
          end slot below on desktop, so it is a real member of this row: same flex line, same
          centre as the icons beside it. (It used to be a FIXED box at top-4, with right padding
          here reserving room for it; this bar's top padding is env(safe-area-inset-top), not py-3's
          12px, so the fixed bell sat 16px below the row.) */}
      <div className="justify-self-end flex items-center gap-1">
        {/* No Chat/Dev switch up here: most people never want the terminal, so it is a chat style (Modern,
            Bubbles or Dev) chosen in the agent's panel, behind the gear. */}
        {seat && mode === 'dev' && onBackToChat && (
          <button type="button" onClick={onBackToChat} data-back-to-chat
                  className="mr-1 inline-flex items-center gap-1.5 rounded-full border border-border px-3 py-1.5 text-[13px] font-medium text-foreground hover:bg-muted">
            <MessageSquare size={15} aria-hidden /> Back to chat
          </button>
        )}
        {/* Theme, red alert and brain are gone from agent panes (Shaw, 2026-10-11: they did nothing
            here and crowded the seat name off a phone). The bell sits where the brain was. */}
        {/* The shared bell's home on this page, phone and desktop (filled by DashboardLayout). */}
        <div ref={registerEndSlot ?? undefined} data-header-end-slot className="flex items-center" />
        {/* The gear, beside the bell as on Arturo's page: the agent's own panel. */}
        {onSettings && seat && (
          <button ref={settingsRef} type="button" onClick={onSettings} aria-expanded={!!settingsOpen} aria-controls="arturo-panel"
                  aria-label={`Settings for ${seat.name || seat.id}`} data-agent-settings
                  className="hit-44 p-2 text-muted-foreground hover:text-foreground hover:bg-muted rounded-lg transition-colors">
            <Settings size={20} />
          </button>
        )}
      </div>
    </div>
  );
}
