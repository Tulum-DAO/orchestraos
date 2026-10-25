import { useContext } from 'react';
import { Menu } from 'lucide-react';
import { HeaderEndSlotContext } from '../../lib/headerSlot';
import { GenChip } from '../GenChip';
import { ExportMenu } from './ExportMenu';

interface TopBarProps {
  onMenuOpen: () => void;
  /** The seat this page belongs to; shown in the middle so the page says whose it is (and hosts the gen chip). */
  seat?: { id: string; generation?: unknown };
  /** Chat (the transcript) or Dev (the seat's live terminal). The toggle shows only with a handler. */
  mode?: 'chat' | 'dev';
  onModeChange?: (mode: 'chat' | 'dev') => void;
}

export function TopBar({ onMenuOpen, seat, mode = 'chat', onModeChange }: TopBarProps) {
  const registerEndSlot = useContext(HeaderEndSlotContext);


  return (
    // `justify-between` across THREE children is what put the theme toggle in the middle of the
    // bar with nothing either side of it. The bar now has exactly two groups — the seat identity
    // on the left and every control on the right — so nothing floats unanchored.
    <div className="sticky top-0 z-20 flex items-center gap-2 px-4 py-3 border-b bg-background border-border safe-top">
      {/* The desktop sidebar is permanently visible (DashboardLayout renders it at md+), so this
          drawer trigger is a SECOND nav entry there. It is the mobile affordance only. */}
      <button
        onClick={onMenuOpen}
        className="md:hidden p-2 text-foreground hover:bg-muted rounded-lg transition-colors"
        aria-label="Open menu"
      >
        <Menu size={24} />
      </button>

      {seat && (
        <div className="flex items-center gap-2 min-w-0 px-2" data-seat-header={seat.id}>
          <span className="font-semibold text-foreground truncate">{seat.id}</span>
          <GenChip generation={seat.generation} />
        </div>
      )}


      {/* DashboardLayout owns the one shared notification bell (#400) and portals it into the
          end slot below on desktop, so it is a real member of this row: same flex line, same
          centre as the icons beside it. (It used to be a FIXED box at top-4, with right padding
          here reserving room for it; this bar's top padding is env(safe-area-inset-top), not py-3's
          12px, so the fixed bell sat 16px below the row.) */}
      <div className="ml-auto flex items-center gap-1">
        {/* Chat / Dev, the same switch the Agents page's panel has: this page is where the rail and
            Arturo send you for a seat, and without it the seat's terminal could not be reached. */}
        {onModeChange && (
          <div className="flex rounded-md border border-border overflow-hidden mr-1" role="group" aria-label="View">
            {(['chat', 'dev'] as const).map((m) => (
              <button
                key={m}
                onClick={() => onModeChange(m)}
                aria-pressed={mode === m}
                className={
                  'text-xs px-2.5 py-1 transition-colors ' +
                  (mode === m
                    ? m === 'dev' ? 'bg-green-800 text-green-300' : 'bg-muted text-foreground'
                    : 'text-muted-foreground hover:text-foreground')
                }
              >
                {m === 'chat' ? 'Chat' : 'Dev'}
              </button>
            ))}
          </div>
        )}
        {/* Export the conversation as Markdown (one icon; chat view only). */}
        {seat && mode === 'chat' && <ExportMenu agentId={seat.id} />}
        {/* Theme, red alert and brain are gone from agent panes (Shaw, 2026-10-11: they did nothing
            here and crowded the seat name off a phone). The bell sits where the brain was. */}
        {/* The shared bell's home on this page, phone and desktop (filled by DashboardLayout). */}
        <div ref={registerEndSlot ?? undefined} data-header-end-slot className="flex items-center" />
      </div>
    </div>
  );
}
