import { useEffect, useRef } from 'react';
import { useOrchestraStore } from '../stores/useOrchestraStore';
import { connectActivitySSE } from '../lib/api';
import { ActivityEvent } from './ActivityEvent';

export function LiveFeed({ limit = 30 }: { limit?: number }) {
  const { activity, addActivity } = useOrchestraStore();
  const initialized = useRef(false);

  useEffect(() => {
    if (initialized.current) return;
    initialized.current = true;
    const source = connectActivitySSE((event) => {
      if (event.type !== 'connected') addActivity(event);
    });
    return () => source.close();
  }, [addActivity]);

  return (
    <div className="space-y-0">
      {activity.slice(0, limit).map((e, i) => <ActivityEvent key={i} event={e} />)}
      {activity.length === 0 && <p className="text-neutral-600 text-sm">No activity yet</p>}
    </div>
  );
}
