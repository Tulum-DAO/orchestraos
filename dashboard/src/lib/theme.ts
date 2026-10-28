/**
 * theme.ts — the Appearance choice (System, Light, Dark) as plain functions, so the menu's logic is tested
 * without a browser. ThemeProvider owns the state; index.html runs the same rules before the first paint,
 * so a reload never flashes the wrong mode. Keep the two in step.
 *
 * The choice is per device (localStorage). System follows the OS setting, live.
 */
export type ThemeChoice = 'system' | 'light' | 'dark';
export type Mode = 'light' | 'dark';

export const THEME_KEY = 'orchestra.theme';
export const DEFAULT_THEME: ThemeChoice = 'system';
/** The phone status bar's colour for each mode: the page background behind it. */
export const THEME_COLORS: Record<Mode, string> = { dark: '#0a0a0a', light: '#ffffff' };

export const APPEARANCE: { id: ThemeChoice; label: string }[] = [
  { id: 'system', label: 'System' },
  { id: 'light', label: 'Light' },
  { id: 'dark', label: 'Dark' },
];

type Store = Pick<Storage, 'getItem' | 'setItem'> | null | undefined;
const isChoice = (v: unknown): v is ThemeChoice => v === 'system' || v === 'light' || v === 'dark';

export function readThemeChoice(storage: Store): ThemeChoice {
  try {
    const v = storage?.getItem(THEME_KEY);
    return isChoice(v) ? v : DEFAULT_THEME;
  } catch { return DEFAULT_THEME; }             // storage blocked (private mode): the default, not an error
}

export function writeThemeChoice(storage: Store, choice: ThemeChoice): void {
  try { storage?.setItem(THEME_KEY, choice); } catch { /* not saved; this page still switches */ }
}

export function effectiveMode(choice: ThemeChoice, osDark: boolean): Mode {
  return choice === 'system' ? (osDark ? 'dark' : 'light') : choice;
}

/** The menu's rows, the current choice checked. */
export function appearanceRows(current: ThemeChoice): { id: ThemeChoice; label: string; checked: boolean }[] {
  return APPEARANCE.map((o) => ({ ...o, checked: o.id === current }));
}
