import { Menu, Sun, Moon, Monitor, Brain, Siren } from 'lucide-react';
import { useTheme } from '../../theme/ThemeProvider';
import { GenChip } from '../GenChip';

interface TopBarProps {
  onMenuOpen: () => void;
  onBrainOpen: () => void;
  onReportOpen?: () => void;
  /** The seat this page belongs to; shown in the middle so the page says whose it is (and hosts the gen chip). */
  seat?: { id: string; generation?: unknown };
}

export function TopBar({ onMenuOpen, onBrainOpen, onReportOpen, seat }: TopBarProps) {
  const { theme, setTheme } = useTheme();

  const cycleTheme = () => {
    const themes: Array<'light' | 'dark' | 'system'> = ['light', 'dark', 'system'];
    const currentIndex = themes.indexOf(theme);
    const nextIndex = (currentIndex + 1) % themes.length;
    setTheme(themes[nextIndex]);
  };

  return (
    <div className="sticky top-0 z-20 flex items-center justify-between px-4 py-3 border-b bg-background border-border safe-top">
      <button
        onClick={onMenuOpen}
        className="p-2 text-foreground hover:bg-muted rounded-lg transition-colors"
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

      <button
        onClick={cycleTheme}
        className="p-2 text-foreground hover:bg-muted rounded-lg transition-colors"
        aria-label="Toggle theme"
      >
        {theme === 'light' && <Sun size={24} />}
        {theme === 'dark' && <Moon size={24} />}
        {theme === 'system' && <Monitor size={24} />}
      </button>

      <div className="flex items-center gap-1">
        {/* Report button — the front door of the RED ALERT / ticket system (the operator 2026-09-18) */}
        {onReportOpen && (
          <button
            onClick={onReportOpen}
            className="p-2 text-red-400 hover:bg-red-500/10 rounded-lg transition-colors"
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
