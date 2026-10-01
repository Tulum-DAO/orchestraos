import { clsx } from 'clsx';
import { dotColor, tickerLabel, type TrafficMessage } from '../lib/fleetTraffic';
import { shortAgo } from '../lib/topologyLines';

export interface FleetTickerProps {
  /** Newest first — rendered left to right, so newest sits on the left (spec §3). */
  messages: TrafficMessage[];
  /** Ids that arrived on the latest poll; those entries get a brief highlight so the ticker
   *  and the travelling dots are visibly the same event rather than two unrelated animations. */
  freshIds?: Set<string>;
  /** Clicking an entry opens that pair's conversation. */
  onSelectConnection?: (a: string, b: string) => void;
}

/**
 * Bottom live ticker (spec §3): "gm → telegram · reply" style, newest on the left.
 *
 * Deliberately NOT a marquee. Auto-scrolling text is unreadable and it is exactly the
 * "calm by default" rule (§2) inverted — it moves when nothing has happened. This scrolls
 * horizontally under the reader's own control and changes only when a message actually lands.
 *
 * The time bar that shares this row is build-order step 9 and is not here yet.
 */
export function FleetTicker({ messages, freshIds, onSelectConnection }: FleetTickerProps) {
  if (messages.length === 0) {
    return (
      <div className="text-xs text-neutral-600 px-1 py-2">No messages yet.</div>
    );
  }
  return (
    <div
      className="flex items-center gap-2 overflow-x-auto scrollbar-hide py-2"
      style={{ WebkitOverflowScrolling: 'touch' }}
      aria-label="Recent fleet messages, newest first"
    >
      {messages.map((m) => {
        const fresh = freshIds?.has(m.id) ?? false;
        const label = tickerLabel(m);
        const when = shortAgo(m.created_at);
        const inner = (
          <>
            <span className={clsx('w-1.5 h-1.5 rounded-full shrink-0', dotColor(m.type))} aria-hidden />
            <span className="truncate max-w-[200px]">{label}</span>
            <span className="text-neutral-600 shrink-0">{when}</span>
          </>
        );
        const cls = clsx(
          'flex items-center gap-1.5 shrink-0 text-[11px] px-2 py-1 rounded-lg border transition-colors',
          fresh
            ? 'border-sky-800 bg-sky-950/40 text-neutral-200'
            : 'border-neutral-800 bg-neutral-900 text-neutral-400',
        );
        return onSelectConnection ? (
          <button
            key={m.id}
            type="button"
            title={`${label} · ${when}`}
            onClick={() => onSelectConnection(m.from_agent, m.to_agent)}
            className={clsx(cls, 'hover:bg-neutral-800 hover:text-neutral-100 focus:outline-none focus:ring-1 focus:ring-sky-500')}
          >
            {inner}
          </button>
        ) : (
          <span key={m.id} title={`${label} · ${when}`} className={cls}>{inner}</span>
        );
      })}
    </div>
  );
}
