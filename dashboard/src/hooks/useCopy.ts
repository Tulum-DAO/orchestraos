import { useEffect, useRef, useState } from 'react';

/** Copy `text` to the clipboard; `copied` is true for a moment after it lands. When the browser refuses the
 *  clipboard nothing is claimed. */
export function useCopy(text: string) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), 1500);
    } catch { /* the browser refused the clipboard */ }
  };
  return { copied, copy };
}
