import { useState } from 'react';
import { clsx } from 'clsx';
import { useInfiniteQuery } from '@tanstack/react-query';
import { X, ArrowUpDown } from 'lucide-react';
import { fetchPairMessages } from '../lib/api';
import { TYPE_FILTERS, matchesTypeFilter, truncateBody, type MessageTypeFilter } from '../lib/conversationPanel';

export interface ConversationPanelProps {
  a: string;
  b: string;
  onOpenAgent: (agentId: string) => void;
  onClose: () => void;
  /** Window and moment the header count is computed over. Threaded from the page so the
   *  panel header and the connection line are literally the same number even while the time
   *  bar is scrubbed — a panel pinned to 24h/live beside a scrubbed line is the 49-vs-40 bug
   *  wearing a different hat. */
  windowHours?: number;
  asof?: string | null;
}

const TYPE_BADGE: Record<string, string> = {
  task: 'bg-yellow-500/15 text-yellow-400',
  reply: 'bg-blue-500/15 text-blue-400',
  task_request: 'bg-orange-500/15 text-orange-400',
};
const typeBadgeClass = (t: string | null) => (t && TYPE_BADGE[t]) || 'bg-neutral-700/40 text-neutral-400';

export function ConversationPanel({
  a, b, onOpenAgent, onClose, windowHours: windowHoursProp = 24, asof = null,
}: ConversationPanelProps) {
  const [typeFilter, setTypeFilter] = useState<MessageTypeFilter>('All');
  const [newestFirst, setNewestFirst] = useState(true);
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());

  const { data, isLoading, isError, fetchNextPage, hasNextPage, isFetchingNextPage } = useInfiniteQuery({
    queryKey: ['messages-pair', a, b, windowHoursProp, asof],
    queryFn: ({ pageParam }) => fetchPairMessages(a, b, { limit: 40, before: pageParam, hours: windowHoursProp, asof }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => (lastPage.has_more ? lastPage.next_before ?? undefined : undefined),
  });

  const toggleExpand = (id: string) => setExpandedIds((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  // total_in_window (not total_all_time) is the header number — it must agree
  // with the connection-line count for the same window (build's instruction,
  // this being exactly what the 49-vs-40 bug was about). Labeled with
  // window_hours so it reads as windowed, not absolute.
  const firstPage = data?.pages[0];
  const totalInWindow = firstPage?.total_in_window ?? 0;
  const windowHours = firstPage?.window_hours ?? windowHoursProp;
  // review, 2026-09-30: total_all_time was returned and never rendered, which left a header
  // that could read "0 in the last 24h" above a body full of older messages. Both numbers are
  // now shown and both are labelled — the windowed one first, because that is the one that
  // matches the connection line.
  const totalAllTime = firstPage?.total_all_time ?? 0;
  const loaded = data?.pages.flatMap((p) => p.messages) ?? [];
  const ordered = newestFirst ? loaded : [...loaded].reverse();
  const filtered = ordered.filter((m) => matchesTypeFilter(m.type, typeFilter));

  return (
    <div className="flex flex-col h-full rounded-xl border border-neutral-800 bg-neutral-900">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-neutral-800 shrink-0">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 text-sm font-semibold flex-wrap">
            <button onClick={() => onOpenAgent(a)} className="text-neutral-100 hover:underline break-words">{a}</button>
            <span className="text-neutral-600">⇄</span>
            <button onClick={() => onOpenAgent(b)} className="text-neutral-100 hover:underline break-words">{b}</button>
          </div>
          <p className="text-[11px] text-neutral-500 mt-0.5">
            {totalInWindow} message{totalInWindow === 1 ? '' : 's'} in the last {windowHours}h{totalAllTime > totalInWindow ? ` · ${totalAllTime} total` : ''}
          </p>
        </div>
        <button onClick={onClose} className="p-1.5 text-neutral-500 hover:text-neutral-200 hover:bg-neutral-800 rounded-lg transition-colors shrink-0" aria-label="Close panel">
          <X size={16} />
        </button>
      </div>

      {/* Filter chips + order toggle */}
      <div className="flex items-center justify-between gap-2 px-4 py-2 border-b border-neutral-800 shrink-0 overflow-x-auto">
        <div className="flex gap-1.5">
          {TYPE_FILTERS.map((f) => (
            <button
              key={f}
              onClick={() => setTypeFilter(f)}
              className={clsx(
                'px-2.5 py-1 text-xs rounded-md font-medium transition-colors whitespace-nowrap',
                typeFilter === f ? 'bg-neutral-700 text-neutral-100' : 'bg-neutral-800 text-neutral-500 hover:text-neutral-300'
              )}
            >
              {f}
            </button>
          ))}
        </div>
        <button
          onClick={() => setNewestFirst((v) => !v)}
          className="flex items-center gap-1 text-xs text-neutral-500 hover:text-neutral-300 transition-colors shrink-0"
          title="Flip order"
        >
          <ArrowUpDown size={12} />
          {newestFirst ? 'Newest first' : 'Oldest first'}
        </button>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto min-h-0 p-3 space-y-2">
        {isLoading && <p className="text-sm text-neutral-500 p-2">Loading...</p>}
        {isError && <p className="text-sm text-red-400 p-2">Failed to load messages.</p>}
        {!isLoading && !isError && filtered.length === 0 && (
          <p className="text-sm text-neutral-600 p-2">No messages in this conversation.</p>
        )}
        {!isLoading && !isError && filtered.map((m) => {
          const { preview, truncated } = truncateBody(m.body || '');
          const expanded = expandedIds.has(m.id);
          return (
            <div key={m.id} className="rounded-lg border border-neutral-800 bg-neutral-950 p-3">
              <div className="flex items-center gap-2 flex-wrap text-[11px]">
                <span className={clsx('px-1.5 py-0.5 rounded font-medium', typeBadgeClass(m.type))}>{m.type || '—'}</span>
                <span className="text-neutral-500">{m.created_at ? new Date(m.created_at).toLocaleString() : '—'}</span>
              </div>
              {m.subject && <p className="text-sm font-medium text-neutral-200 mt-1.5 break-words">{m.subject}</p>}
              <p className="text-sm text-neutral-300 mt-1 whitespace-pre-wrap break-words">
                {expanded ? (m.body || '') : preview}
              </p>
              {truncated && (
                <button onClick={() => toggleExpand(m.id)} className="text-xs text-blue-400 hover:text-blue-300 mt-1">
                  {expanded ? 'collapse' : 'expand'}
                </button>
              )}
              <p className="text-[11px] text-neutral-600 mt-1.5">{m.from_agent} → {m.to_agent} · {m.status || '—'}</p>
            </div>
          );
        })}
        {!isLoading && hasNextPage && (
          <div className="pt-1 pb-2 text-center">
            <button
              onClick={() => fetchNextPage()}
              disabled={isFetchingNextPage}
              className="text-xs px-3 py-1.5 rounded-md bg-neutral-800 text-neutral-400 hover:text-neutral-200 transition-colors disabled:opacity-50"
            >
              {isFetchingNextPage ? 'Loading...' : 'Load older'}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
