import { Menu, Sun, Moon, Monitor, Brain, Siren } from 'lucide-react';
import { useTheme } from '../../theme/ThemeProvider';
import { GenChip } from '../GenChip';

interface TopBarProps {
  onMenuOpen: () => void;
  onBrainOpen: () => void;
  onReportOpen?: () => void;
  /** The seat this page belongs to; shown in the middle so the page says whose it is (and hosts the gen chip). */
  seat?: { id: string; generation?: unknown };
  /** Chat (the transcript) or Dev (the seat's live terminal). The toggle shows only with a handler. */
  mode?: 'chat' | 'dev';
  onModeChange?: (mode: 'chat' | 'dev') => void;
}

export function TopBar({ onMenuOpen, onBrainOpen, onReportOpen, seat, mode = 'chat', onModeChange }: TopBarProps) {
  const { theme, setTheme } = useTheme();

  const cycleTheme = () => {
    const themes: Array<'light' | 'dark' | 'system'> = ['light', 'dark', 'system'];
    const currentIndex = themes.indexOf(theme);
    const nextIndex = (currentIndex + 1) % themes.length;
    setTheme(themes[nextIndex]);
  };

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
        <button
          onClick={cycleTheme}
          className="p-2 text-foreground hover:bg-muted rounded-lg transition-colors"
          aria-label="Toggle theme"
        >
          {theme === 'light' && <Sun size={24} />}
          {theme === 'dark' && <Moon size={24} />}
          {theme === 'system' && <Monitor size={24} />}
        </button>
        {/* Report button — the front door of the RED ALERT / ticket system (the operator 2026-09-18) */}
        {onReportOpen && (
          <button
            onClick={onReportOpen}
            // NOT permanently red. This is an ACTION the operator can take, not a STATE of the
            // system: nothing is wrong because the button exists. A red that is always on is
            // the same defect as a badge that prints the same value on every row — it cannot
            // distinguish anything, so it stops being read, and then a real alert has no
            // colour left to use. It wears the alarm colour on hover and focus, at the moment
            // it is actually about to be used.
            className="p-2 text-foreground hover:text-red-400 hover:bg-red-500/10 focus-visible:text-red-400 rounded-lg transition-colors"
            aria-label="Report a problem"
            title="Report a crash, bug, improvement or suggestion"
          >
            <Siren size={24} />
          </button>
        )}
        <button
          onClick={onBrainOpen}
          className="p-2 text-foreground hover:bg-muted rounded-lg transition-colors"
          aria-label="Open brain"
        >
          <Brain size={24} />
        </button>
      </div>
    </div>
  );
}
