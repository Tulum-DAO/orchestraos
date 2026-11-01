/** The live call's running time, "0:10" (stores/callStore.ts), ticking once a second. */
import { useEffect, useState } from 'react';
import { useCallStore } from '../../stores/callStore';
import { callClock } from '../../lib/callClock';

export default function CallTimer({ className }: { className?: string }) {
  const startedAt = useCallStore((s) => s.call?.startedAt ?? null);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (startedAt === null) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [startedAt]);
  if (startedAt === null) return null;
  return <span className={`tabular-nums ${className ?? ''}`}>{callClock(now - startedAt)}</span>;
}
