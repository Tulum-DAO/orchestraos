/**
 * ConversationRail — the saved-conversations thread list (Phase 1).
 *
 * Lists past threads (title, preview, relative time, source-channel chips),
 * a search box (server `q`), a new-chat button, and resume-on-click
 * (→ store.loadThread, which hydrates the shared timeline incl. gate cards).
 *
 * QA-ability (AGY-sibling rules): deterministic data-testids on every item +
 * control, and a data-rail-state signature (loading|empty|ready|error) so an
 * automated user-agent can drive + assert states without racing on animation.
 */
import { useEffect, useState } from 'react';
import { clsx } from 'clsx';
import { Search, Plus, MessageSquare, Phone, Send, Monitor, Pin } from 'lucide-react';
import { listThreads } from '../../lib/assistant/threads';
import type { ThreadSummary, ConversationChannel } from '../../lib/assistant/contract';

interface Props {
  activeThread: string;
  onSelect: (thread: string) => void;
  onNew: () => void;
}

const CHANNEL_ICON: Record<ConversationChannel, typeof MessageSquare> = {
  bubble: MessageSquare,
  page: Monitor,
  telegram: Send,
  voice: Phone,
};

function relTime(ts: string): string {
  const diff = Date.now() - new Date(ts).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return 'now';
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h`;
  return `${Math.floor(h / 24)}d`;
}

export default function ConversationRail({ activeThread, onSelect, onNew }: Props) {
  const [q, setQ] = useState('');
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [state, setState] = useState<'loading' | 'empty' | 'ready' | 'error'>('loading');

  useEffect(() => {
    let cancelled = false;
    const ac = new AbortController();
    setState('loading');
    // debounce search a touch (does not affect token painting — this is list fetch)
    const t = setTimeout(() => {
      listThreads({ q: q.trim() || undefined, signal: ac.signal })
        .then((rows) => {
          if (cancelled) return;
          setThreads(rows);
          setState(rows.length === 0 ? 'empty' : 'ready');
        })
        .catch(() => { if (!cancelled) setState('error'); });
    }, 200);
    return () => { cancelled = true; ac.abort(); clearTimeout(t); };
  }, [q]);

  return (
    <div
      data-testid="conversation-rail"
      data-rail-state={state}
      className="flex flex-col h-full w-64 border-r border-neutral-800 bg-neutral-950 shrink-0"
    >
      <div className="p-2 border-b border-neutral-800 space-y-2 shrink-0">
        <button
          data-testid="new-chat"
          onClick={onNew}
          className="flex items-center gap-2 w-full px-2.5 py-2 rounded-lg text-sm bg-blue-500/15 text-blue-400 hover:bg-blue-500/25 transition-colors"
        >
          <Plus size={15} /> New chat
        </button>
        <div className="flex items-center gap-1.5 bg-neutral-900 border border-neutral-800 rounded-lg px-2.5 py-1.5 focus-within:border-neutral-600">
          <Search size={13} className="text-neutral-500 shrink-0" />
          <input
            data-testid="thread-search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search conversations…"
            className="flex-1 bg-transparent text-xs text-neutral-300 placeholder-neutral-600 focus:outline-none"
          />
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-1.5 space-y-1 min-h-0">
        {state === 'loading' && <p className="text-[11px] text-neutral-600 px-2 py-3">Loading…</p>}
        {state === 'error' && <p className="text-[11px] text-red-400/80 px-2 py-3">Couldn't load conversations.</p>}
        {state === 'empty' && <p className="text-[11px] text-neutral-600 px-2 py-3">No conversations{q ? ' match' : ' yet'}.</p>}
        {threads.map((t) => (
          <button
            key={t.thread}
            data-testid={`thread-item-${t.thread}`}
            data-active={t.thread === activeThread}
            onClick={() => onSelect(t.thread)}
            className={clsx(
              'w-full text-left px-2.5 py-2 rounded-lg transition-colors',
              t.thread === activeThread ? 'bg-neutral-800' : 'hover:bg-neutral-900',
            )}
          >
            <div className="flex items-center gap-1.5">
              {t.pinned && <Pin size={10} className="text-amber-400 shrink-0" />}
              <span className="text-xs font-medium text-neutral-200 truncate flex-1">{t.title}</span>
              <span className="text-[10px] text-neutral-600 shrink-0">{relTime(t.updatedAt)}</span>
            </div>
            <p className="text-[11px] text-neutral-500 truncate mt-0.5">{t.preview}</p>
            <div className="flex items-center gap-1 mt-1">
              {t.sourceChannels.map((c) => {
                const Icon = CHANNEL_ICON[c];
                return <Icon key={c} size={10} className="text-neutral-600" aria-label={c} />;
              })}
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}
