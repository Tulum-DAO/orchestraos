/**
 * AssistantView — OrchestraOS V2 converse UI (B3).
 *
 * Consumes the SHARED zustand converse store (via useConverse), so the full
 * page and the bubble share ONE (user, thread) conversation. When empty it
 * shows a premium "everything-search" hero composer; once a turn starts the
 * composer docks to the bottom and the stream paints token-by-token with an
 * honest ActivityPill. Renders MessageBubble + AssistantToolCall (the D1–D8
 * gate). Read-only converse — no mutating tools here.
 */
import { useEffect, useRef, useState } from 'react';
import { clsx } from 'clsx';
import { Search, PanelLeft } from 'lucide-react';
import MessageBubble from '../chat/MessageBubble';
import AssistantToolCall from './AssistantToolCall';
import AssistantInput from './AssistantInput';
import ActivityPill from './ActivityPill';
import ConversationRail from './ConversationRail';
import { useConverse } from '../../hooks/useConverse';
import { useConverseStore } from '../../stores/useConverseStore';

interface Props {
  /** Render surface — passed per send; never scopes state. */
  channel?: string;
}

const SUGGESTIONS = ["What's going on?", 'Agent status', 'Show approvals'];

export default function AssistantView({ channel = 'page' }: Props) {
  const { items, streaming, status, error, activity, loadingThread, thread, send, cancel, loadThread, newThread } =
    useConverse({ channel });
  const user = useConverseStore((s) => s.user);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [heroText, setHeroText] = useState('');
  const [railOpen, setRailOpen] = useState(true);

  // Stable state signature for the AGY visual-QA bot to assert against.
  const convState = loadingThread ? 'loading' : status === 'error' ? 'error' : streaming ? 'streaming' : 'idle';

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
    if (nearBottom) el.scrollTop = el.scrollHeight;
  }, [items]);

  const submitHero = (text?: string) => {
    const t = (text ?? heroText).trim();
    if (!t) return;
    send(t); // store guards double-submit
    setHeroText('');
  };

  const isEmpty = items.length === 0;

  return (
    <div
      data-testid="assistant-view"
      data-conv-state={convState}
      className="flex h-[calc(100vh-8rem)] max-w-5xl mx-auto rounded-xl border border-neutral-800 bg-neutral-950 overflow-hidden"
    >
      {railOpen && (
        <ConversationRail activeThread={thread} onSelect={loadThread} onNew={newThread} />
      )}

      <div className="flex flex-col flex-1 min-w-0">
      <div className="px-4 py-3 border-b border-neutral-800 flex items-center gap-2 shrink-0">
        <button
          data-testid="toggle-rail"
          onClick={() => setRailOpen((o) => !o)}
          className="p-1 -ml-1 text-neutral-500 hover:text-neutral-200 rounded transition-colors"
          title={railOpen ? 'Hide conversations' : 'Show conversations'}
        >
          <PanelLeft size={16} />
        </button>
        <span className="text-sm font-semibold text-white">Assistant</span>
        <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-400">V2 · live · read-only</span>
        {!isEmpty && <ActivityPill activity={activity} className="ml-1" />}
        <span className="ml-auto text-[10px] text-neutral-600 font-mono">
          {user}/{thread} · {channel}
        </span>
      </div>

      {loadingThread ? (
        <div className="flex-1 flex items-center justify-center text-sm text-neutral-500" data-testid="thread-loading">
          <span className="w-2 h-2 bg-neutral-500 rounded-full animate-pulse mr-2" /> Loading conversation…
        </div>
      ) : isEmpty ? (
        /* ---- Everything-search hero ---- */
        <div className="flex-1 flex flex-col items-center justify-center px-6 gap-5">
          <div className="text-center">
            <p className="text-lg font-semibold text-white">OrchestraOS Assistant</p>
            <p className="text-sm text-neutral-500 mt-1 max-w-md">
              Ask anything about your agents, clients, and system — read-only visibility,
              streamed live.
            </p>
          </div>
          <div className="w-full max-w-xl">
            <div className="flex items-center gap-2 bg-neutral-900 border border-neutral-700 rounded-2xl px-4 py-3 focus-within:border-blue-500/60 focus-within:ring-2 focus-within:ring-blue-500/20 transition-all shadow-lg">
              <Search size={18} className="text-neutral-500 shrink-0" />
              <input
                autoFocus
                value={heroText}
                onChange={(e) => setHeroText(e.target.value)}
                placeholder="Ask the assistant anything…"
                className="flex-1 bg-transparent text-sm text-neutral-200 placeholder-neutral-600 focus:outline-none"
                onKeyDown={(e) => {
                  if (e.key === 'Enter') { e.preventDefault(); submitHero(); }
                }}
              />
              <button
                onClick={() => submitHero()}
                disabled={!heroText.trim()}
                className={clsx(
                  'text-xs px-3 py-1.5 rounded-lg font-medium transition-colors',
                  heroText.trim()
                    ? 'bg-blue-500/15 text-blue-400 hover:bg-blue-500/25'
                    : 'bg-neutral-800 text-neutral-600 cursor-not-allowed',
                )}
              >
                Ask
              </button>
            </div>
            <div className="flex flex-wrap gap-1.5 mt-3 justify-center">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  onClick={() => submitHero(s)}
                  className="text-[11px] px-2.5 py-1 rounded-full bg-neutral-900 border border-neutral-800 text-neutral-400 hover:text-neutral-200 hover:border-neutral-700 transition-colors"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        </div>
      ) : (
        /* ---- Conversation ---- */
        <>
          <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 space-y-2.5 min-h-0">
            {items.map((it) =>
              it.type === 'tool_call' ? (
                <AssistantToolCall key={it.id} item={it} />
              ) : (
                <MessageBubble key={it.id} event={it} />
              ),
            )}
            {streaming && (
              <div className="px-1 py-1">
                <ActivityPill activity={activity} />
              </div>
            )}
            {status === 'error' && error && (
              <div className="text-xs px-3 py-1.5 rounded-lg bg-red-600/10 text-red-300">{error}</div>
            )}
          </div>
          <AssistantInput onSend={send} onCancel={cancel} streaming={streaming} />
        </>
      )}
      </div>
    </div>
  );
}
