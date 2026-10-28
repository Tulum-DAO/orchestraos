import { createContext, useContext, useEffect, useLayoutEffect, useState, type ReactNode } from 'react';
import { readThemeChoice, writeThemeChoice, effectiveMode, THEME_COLORS, type ThemeChoice, type Mode } from '../lib/theme';

type Theme = ThemeChoice;

interface ThemeContextType {
  theme: Theme;
  setTheme: (theme: Theme) => void;
}

const ThemeContext = createContext<ThemeContextType | undefined>(undefined);

const OS_DARK = '(prefers-color-scheme: dark)';
const storage = () => { try { return window.localStorage; } catch { return null; } };
const osIsDark = () => typeof window !== 'undefined' && !!window.matchMedia?.(OS_DARK).matches;

/** The `dark` class on <html> (what every colour keys off) and the phone status bar's colour. On a switch,
 *  transitions are held for a frame, so hover fades do not play the change out as a colour sweep. */
function applyMode(mode: Mode) {
  const html = document.documentElement;
  if (html.classList.contains('dark') === (mode === 'dark')) { setThemeColor(mode); return; }
  const hold = document.createElement('style');
  hold.textContent = '*,*::before,*::after{transition:none!important}';
  document.head.appendChild(hold);
  html.classList.toggle('dark', mode === 'dark');
  setThemeColor(mode);
  void html.offsetHeight;                       // the new colours are computed with transitions off
  requestAnimationFrame(() => hold.remove());
}

function setThemeColor(mode: Mode) {
  document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]').forEach((m) => { m.content = THEME_COLORS[mode]; });
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  // Read synchronously: index.html already set the class from the same storage before the first paint, so
  // the first render agrees with it and nothing flips after mount.
  const [theme, setThemeState] = useState<Theme>(() => readThemeChoice(storage()));
  const [osDark, setOsDark] = useState(osIsDark);

  // Before paint, so a change shows in the same frame as the menu's new check.
  useLayoutEffect(() => { applyMode(effectiveMode(theme, osDark)); }, [theme, osDark]);

  // System follows the OS while the page is open (a scheduled dark mode at sunset, say).
  useEffect(() => {
    const mq = window.matchMedia?.(OS_DARK);
    if (!mq) return;
    const on = () => setOsDark(mq.matches);
    mq.addEventListener('change', on);
    return () => mq.removeEventListener('change', on);
  }, []);

  const setTheme = (newTheme: Theme) => {
    writeThemeChoice(storage(), newTheme);
    setThemeState(newTheme);
  };

  // Always provide the context — the first render must NOT drop it, or any child calling useTheme() on
  // first paint throws "useTheme must be used within a ThemeProvider" and the whole route renders blank.
  return (
    <ThemeContext.Provider value={{ theme, setTheme }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  const context = useContext(ThemeContext);
  if (context === undefined) {
    throw new Error('useTheme must be used within a ThemeProvider');
  }
  return context;
}
