/** Settings state shared by the row kit (SettingsKit.tsx) and the shell: the search text and a row's save status.
 *  Kept apart from the components so React fast refresh keeps working (react-refresh/only-export-components). */
import { createContext, useEffect, useRef, useState } from 'react';

/** The search box's text; rows that do not match hide themselves. */
export const SettingsQuery = createContext('');

/** A row's grey description id (SettingRow), for its control's aria-describedby: Choice reads it from here; a plain
 *  select, input or switch is given descIdOf(key) directly. */
export const RowDescription = createContext<string | undefined>(undefined);
export const descIdOf = (id: string) => `setting-${id}-desc`;

export type RowStatus = { kind: 'idle' } | { kind: 'saved' } | { kind: 'error'; text: string };

/** A status that shows ✓ Saved for two seconds. */
export function useSavedFlash(): [RowStatus, (s: RowStatus) => void] {
  const [status, setStatus] = useState<RowStatus>({ kind: 'idle' });
  const timer = useRef<number | null>(null);
  useEffect(() => () => { if (timer.current) window.clearTimeout(timer.current); }, []);
  const flash = (s: RowStatus) => {
    if (timer.current) window.clearTimeout(timer.current);
    setStatus(s);
    if (s.kind === 'saved') timer.current = window.setTimeout(() => setStatus({ kind: 'idle' }), 2000);
  };
  return [status, flash];
}

/** ChoiceList's keys (ChoiceList.tsx). Where an arrow, Home or End key goes from `from` among the options that can be picked; undefined for any other key. */
export function choiceListTarget(enabled: readonly string[], from: string, key: string): string | undefined {
  const i = enabled.indexOf(from);
  const n = enabled.length;
  if (i < 0 || !n) return undefined;
  return key === 'ArrowDown' || key === 'ArrowRight' ? enabled[(i + 1) % n]
    : key === 'ArrowUp' || key === 'ArrowLeft' ? enabled[(i - 1 + n) % n]
    : key === 'Home' ? enabled[0] : key === 'End' ? enabled[n - 1] : undefined;
}
