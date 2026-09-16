import { useState, useRef, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Bot, X, SendHorizontal } from 'lucide-react';
import { clsx } from 'clsx';
import { sendJarvisMessage, getJarvisHistory, fetchApprovals } from '../lib/api';
import { ASSISTANT_BUBBLE_V2_ENABLED } from '../lib/assistant/config';
import JarvisPanelV2 from './JarvisPanelV2';

const CHANNEL_COLORS: Record<string, string> = {
  telegram: 'bg-blue-500/20 text-blue-400',
  dashboard: 'bg-purple-500/20 text-purple-400',
  'jarvis-voice': 'bg-green-500/20 text-green-400',
};

const QUICK_ACTIONS = [
  "What's going on?",
  'Show approvals',
  'Agent status',
];

export default function JarvisPanel() {
  // B3: when the bubble-rewire flag is on, render the /v2 converse bubble.
  // Flag OFF → the legacy panel below (old Jarvis API) renders unchanged.
  if (ASSISTANT_BUBBLE_V2_ENABLED) return <JarvisPanelV2 />;
  return <JarvisPanelLegacy />;
}

function JarvisPanelLegacy() {
  const [expanded, setExpanded] = useState(false);
  const [inputValue, setInputValue] = useState('');
  const scrollRef = useRef<HTMLDivElement>(null);
  const queryClient = useQueryClient();

  const { data: history } = useQuery({
    queryKey: ['jarvis-history'],
    queryFn: () => getJarvisHistory(30),
    refetchInterval: 10_000,
  });

  const { data: approvals } = useQuery({
    queryKey: ['approvals'],
    queryFn: fetchApprovals,
    refetchInterval: 15_000,
  });

  const approvalCount = Array.isArray(approvals) ? approvals.filter((a: any) => a.status === 'pending').length : 0;

  const sendMutation = useMutation({
    mutationFn: (message: string) => sendJarvisMessage(message),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['jarvis-history'] });
      queryClient.invalidateQueries({ queryKey: ['approvals'] });
    },
  });

  const handleSend = (text?: string) => {
    const msg = text || inputValue.trim();
    if (!msg) return;
    sendMutation.mutate(msg);
    setInputValue('');
  };

  // Auto-scroll to bottom when history changes or panel opens
  useEffect(() => {
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        if (scrollRef.current) {
          scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
        }
      });
    });
  }, [history, expanded]);

  const messages = Array.isArray(history) ? history : [];

  return (
    <>
      {/* Expanded panel */}
      {expanded && (
        <div className="fixed bottom-20 right-6 z-50 w-[380px] max-h-[60vh] flex flex-col bg-neutral-900 border border-neutral-700 rounded-xl shadow-2xl overflow-hidden animate-in slide-in-from-bottom-2">
          {/* Header */}
          <div className="flex items-center justify-between px-4 py-3 border-b border-neutral-800 shrink-0">
            <div className="flex items-center gap-2">
              <span className="text-sm font-semibold text-neutral-100">Jarvis</span>
              <span className="w-2 h-2 rounded-full bg-green-500" />
            </div>
            <button
              onClick={() => setExpanded(false)}
              className="p-1 text-neutral-500 hover:text-neutral-200 hover:bg-neutral-800 rounded-lg transition-colors"
            >
              <X size={16} />
            </button>
          </div>

          {/* Conversation area */}
          <div ref={scrollRef} className="flex-1 overflow-y-auto px-3 py-3 space-y-3 min-h-0">
            {messages.length === 0 && (
              <p className="text-xs text-neutral-600 text-center py-6">No messages yet</p>
            )}
            {messages.map((msg: any, i: number) => {
              const isShaw = msg.role === 'operator' || msg.role === 'user';
              const channel = msg.channel || 'dashboard';
              return (
                <div key={i} className={clsx('flex flex-col gap-1', isShaw ? 'items-end' : 'items-start')}>
                  <div className="flex items-center gap-1.5">
                    <span className={clsx('text-[10px] px-1.5 py-0.5 rounded-full font-medium', CHANNEL_COLORS[channel] || 'bg-neutral-700/50 text-neutral-400')}>
                      {channel}
                    </span>
                    <span className="text-[10px] text-neutral-600">
                      {msg.role || 'system'}
                    </span>
                  </div>
                  <div className={clsx(
                    'text-xs px-3 py-2 rounded-lg max-w-[85%] leading-relaxed',
                    isShaw
                      ? 'bg-blue-500/15 text-blue-200'
                      : 'bg-neutral-800 text-neutral-300'
                  )}>
                    {msg.content || msg.message || msg.text || ''}
                  </div>
                </div>
              );
            })}
            {sendMutation.isPending && (
              <div className="flex flex-col gap-1 items-start">
                <div className="text-xs px-3 py-2 rounded-lg bg-neutral-800 text-neutral-500">
                  Thinking...
                </div>
              </div>
            )}
          </div>

          {/* Quick actions */}
          <div className="flex gap-1.5 px-3 py-2 border-t border-neutral-800 shrink-0 overflow-x-auto">
            {QUICK_ACTIONS.map((action) => (
              <button
                key={action}
                onClick={() => handleSend(action)}
                disabled={sendMutation.isPending}
                className="text-[11px] px-2.5 py-1 rounded-full bg-neutral-800 text-neutral-400 hover:text-neutral-200 hover:bg-neutral-700 transition-colors whitespace-nowrap shrink-0"
              >
                {action}
              </button>
            ))}
          </div>

          {/* Input */}
          <div className="flex items-center gap-2 px-3 py-2.5 border-t border-neutral-800 shrink-0">
            <input
              type="text"
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              placeholder="Message Jarvis..."
              className="flex-1 bg-neutral-950 border border-neutral-800 rounded-lg px-3 py-2 text-xs text-neutral-300 placeholder-neutral-600 focus:outline-none focus:border-neutral-600"
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  handleSend();
                }
              }}
              disabled={sendMutation.isPending}
            />
            <button
              onClick={() => handleSend()}
              disabled={sendMutation.isPending || !inputValue.trim()}
              className={clsx(
                'p-2 rounded-lg transition-colors',
                sendMutation.isPending || !inputValue.trim()
                  ? 'text-neutral-700 cursor-not-allowed'
                  : 'text-blue-400 hover:bg-blue-500/15'
              )}
            >
              <SendHorizontal size={16} />
            </button>
          </div>
        </div>
      )}

      {/* Floating button */}
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
