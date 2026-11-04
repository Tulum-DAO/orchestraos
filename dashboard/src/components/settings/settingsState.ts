/** Settings state shared by the row kit (SettingsKit.tsx) and the shell: the search text and a row's save status.
 *  Kept apart from the components so React fast refresh keeps working (react-refresh/only-export-components). */
import { createContext, useEffect, useRef, useState } from 'react';

/** The search box's text; rows that do not match hide themselves. */
export const SettingsQuery = createContext('');

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

