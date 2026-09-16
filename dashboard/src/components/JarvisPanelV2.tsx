/**
 * JarvisPanelV2 — the bottom-right bubble rewired onto the /v2 converse SSE
 * (B3). Shares the SAME zustand conversation as the /assistant full page, so a
 * turn started here continues there (and vice-versa) and an in-flight stream
 * keeps painting across both. Streamed token-by-token with the honest
 * ActivityPill; renders MessageBubble + AssistantToolCall (the D1–D8 gate).
 *
 * Reuses the existing bot-icon launcher + approvals badge. On phones it opens
 * as a near-fullscreen bottom sheet. Gated behind ASSISTANT_BUBBLE_V2_ENABLED
 * by the JarvisPanel wrapper; the legacy panel (old Jarvis API) is untouched
 * when the flag is off.
 *
 * Read-only converse only — no mutating tools wired here.
 */
import { useState, useEffect, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { Bot, X, SendHorizontal, Maximize2 } from 'lucide-react';
import { clsx } from 'clsx';
import { fetchApprovals } from '../lib/api';
import { useConverse } from '../hooks/useConverse';
import { ASSISTANT_V2_ENABLED } from '../lib/assistant/config';
import MessageBubble from './chat/MessageBubble';
import AssistantToolCall from './assistant/AssistantToolCall';
import ActivityPill from './assistant/ActivityPill';

const QUICK_ACTIONS = ["What's going on?", 'Show approvals', 'Agent status'];

export default function JarvisPanelV2() {
  const [expanded, setExpanded] = useState(false);
  const [input, setInput] = useState('');
  const scrollRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();

  const { items, streaming, activity, error, status, send } = useConverse({ channel: 'bubble' });

  const { data: approvals } = useQuery({
    queryKey: ['approvals'],
    queryFn: fetchApprovals,
    refetchInterval: 15_000,
  });
  const approvalCount = Array.isArray(approvals)
    ? approvals.filter((a: any) => a.status === 'pending').length
    : 0;

  useEffect(() => {
    requestAnimationFrame(() => {
      if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    });
  }, [items, expanded]);

  const submit = (text?: string) => {
    const t = (text ?? input).trim();
    if (!t) return;
    send(t); // store guards double-submit across surfaces
    setInput('');
  };

  const goFullPage = () => {
    setExpanded(false);
    navigate('/assistant');
  };

  return (
    <>
      {expanded && (
        <div
          className={clsx(
            'fixed z-50 flex flex-col bg-neutral-900 border border-neutral-700 shadow-2xl overflow-hidden',
            // Mobile: near-fullscreen bottom sheet. Desktop: anchored panel.
            'inset-x-2 bottom-2 top-16 rounded-2xl',
            'sm:inset-auto sm:bottom-20 sm:right-6 sm:top-auto sm:w-[380px] sm:max-h-[60vh] sm:rounded-xl',
          )}
        >
          <div className="flex items-center justify-between px-4 py-3 border-b border-neutral-800 shrink-0">
            <div className="flex items-center gap-2 min-w-0">
              <span className="text-sm font-semibold text-neutral-100">Jarvis</span>
              <span className="w-2 h-2 rounded-full bg-green-500 shrink-0" />
              {streaming && <ActivityPill activity={activity} />}
            </div>
            <div className="flex items-center gap-1 shrink-0">
              {ASSISTANT_V2_ENABLED && (
                <button
                  onClick={goFullPage}
                  title="Open full page"
                  className="p-1 text-neutral-500 hover:text-neutral-200 hover:bg-neutral-800 rounded-lg transition-colors"
                >
                  <Maximize2 size={15} />
                </button>
              )}
              <button
                onClick={() => setExpanded(false)}
                className="p-1 text-neutral-500 hover:text-neutral-200 hover:bg-neutral-800 rounded-lg transition-colors"
              >
                <X size={16} />
              </button>
            </div>
          </div>

          <div ref={scrollRef} className="flex-1 overflow-y-auto px-3 py-3 space-y-2.5 min-h-0">
            {items.length === 0 && (
              <p className="text-xs text-neutral-600 text-center py-6">
                Ask about agents, clients, or system state.
              </p>
            )}
            {items.map((it) =>
              it.type === 'tool_call' ? (
                <AssistantToolCall key={it.id} item={it} />
              ) : (
                <MessageBubble key={it.id} event={it} />
              ),
            )}
            {streaming && (
              <div className="px-1">
                <ActivityPill activity={activity} />
              </div>
            )}
            {status === 'error' && error && (
              <div className="text-xs px-3 py-1.5 rounded-lg bg-red-600/10 text-red-300">{error}</div>
            )}
          </div>

          <div className="flex gap-1.5 px-3 py-2 border-t border-neutral-800 shrink-0 overflow-x-auto">
            {QUICK_ACTIONS.map((action) => (
              <button
                key={action}
                onClick={() => submit(action)}
                disabled={streaming}
                className="text-[11px] px-2.5 py-1 rounded-full bg-neutral-800 text-neutral-400 hover:text-neutral-200 hover:bg-neutral-700 disabled:opacity-40 transition-colors whitespace-nowrap shrink-0"
              >
                {action}
              </button>
            ))}
          </div>

          <div className="flex items-center gap-2 px-3 py-2.5 border-t border-neutral-800 shrink-0">
            <input
              type="text"
              data-testid="bubble-input"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={streaming ? 'Responding…' : 'Message Jarvis…'}
              className="flex-1 bg-neutral-950 border border-neutral-800 rounded-lg px-3 py-2 text-xs text-neutral-300 placeholder-neutral-600 focus:outline-none focus:border-neutral-600"
              onKeyDown={(e) => {
                if (e.key === 'Enter') { e.preventDefault(); submit(); }
              }}
            />
            <button
              onClick={() => submit()}
              disabled={streaming || !input.trim()}
              className={clsx(
                'p-2 rounded-lg transition-colors',
                streaming || !input.trim()
                  ? 'text-neutral-700 cursor-not-allowed'
                  : 'text-blue-400 hover:bg-blue-500/15',
              )}
            >
              <SendHorizontal size={16} />
            </button>
          </div>
        </div>
      )}

      <button
        onClick={() => setExpanded(!expanded)}
        className="fixed bottom-6 right-6 z-50 w-12 h-12 rounded-full bg-neutral-800 hover:bg-neutral-700 text-neutral-300 hover:text-white flex items-center justify-center shadow-lg transition-colors"
        aria-label="Toggle Jarvis"
      >
        <Bot size={22} />
        {approvalCount > 0 && (
          <span className="absolute -top-1 -right-1 w-5 h-5 rounded-full bg-red-500 text-white text-[10px] font-bold flex items-center justify-center">
            {approvalCount > 9 ? '9+' : approvalCount}
          </span>
        )}
      </button>
    </>
  );
}
