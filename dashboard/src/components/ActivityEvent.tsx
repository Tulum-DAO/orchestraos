export function ActivityEvent({ event }: { event: any }) {
  const time = new Date(event.timestamp).toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
  return (
    <div className="flex items-start gap-3 py-1.5 text-sm">
      <span className="text-neutral-600 font-mono text-xs w-16 shrink-0">{time}</span>
      <span className="text-neutral-300 font-medium w-28 shrink-0 truncate">{event.agent}</span>
      <span className="text-neutral-500 truncate">{event.detail}</span>
    </div>
  );
}
