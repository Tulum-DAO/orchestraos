import { Menu, Sun, Moon, Monitor, Brain } from 'lucide-react';
import { useTheme } from '../../theme/ThemeProvider';

interface TopBarProps {
  onMenuOpen: () => void;
  onBrainOpen: () => void;
}

export function TopBar({ onMenuOpen, onBrainOpen }: TopBarProps) {
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

      <button
        onClick={cycleTheme}
        className="p-2 text-foreground hover:bg-muted rounded-lg transition-colors"
        aria-label="Toggle theme"
      >
        {theme === 'light' && <Sun size={24} />}
        {theme === 'dark' && <Moon size={24} />}
        {theme === 'system' && <Monitor size={24} />}
      </button>

      <button
        onClick={onBrainOpen}
        className="p-2 text-foreground hover:bg-muted rounded-lg transition-colors"
        aria-label="Open brain"
      >
        <Brain size={24} />
      </button>
    </div>
  );
}
