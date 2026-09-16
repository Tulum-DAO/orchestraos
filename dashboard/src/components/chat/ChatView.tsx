/**
 * ChatView — ChatGPT-style conversation interface for Claude Code agents.
 *
 * Polls agent output, feeds through Protocol Translator, renders
 * structured events as a conversation with rich widgets.
 */
import { useState, useEffect, useRef, useCallback } from 'react';
import { ClaudeCodeProtocol, stripAnsi, type ProtocolEvent, type AgentState } from '../../lib/claude-code-protocol';
import { getAgentOutput, injectToAgent, sendKeyToAgent } from '../../lib/api';
import MessageBubble from './MessageBubble';
import ToolCallCard from './ToolCallCard';
import ToolApprovalCard from './ToolApprovalCard';
import OptionsCard from './OptionsCard';
import ExpandableCard from './ExpandableCard';
import StatusIndicator from './StatusIndicator';
import ChatInput from './ChatInput';
import ActionBar from '../ActionBar';

interface ChatViewProps {
  agentId: string;
  compact?: boolean; // true = max-h-64 for inline expanded card, false = flex-1 for full modal
}

export default function ChatView({ agentId, compact }: ChatViewProps) {
  const protocol = useRef(new ClaudeCodeProtocol());
  const [events, setEvents] = useState<ProtocolEvent[]>([]);
  const [agentState, setAgentState] = useState<AgentState>('idle');
  const [rawLines, setRawLines] = useState<string[]>([]);
  const [waitingForOutput, setWaitingForOutput] = useState(false);
  const [loading, setLoading] = useState(true);
  const [lineCount, setLineCount] = useState(50);
  const [loadingMore, setLoadingMore] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const firstLoad = useRef(true);

  // Load more when user scrolls to top
  const handleScroll = useCallback(() => {
    if (!scrollRef.current || loadingMore) return;
    if (scrollRef.current.scrollTop === 0 && lineCount < 500) {
      setLoadingMore(true);
      const prevHeight = scrollRef.current.scrollHeight;
      setLineCount((prev) => Math.min(prev + 100, 500));
      // Preserve scroll position after new content loads
      requestAnimationFrame(() => {
        if (scrollRef.current) {
          scrollRef.current.scrollTop = scrollRef.current.scrollHeight - prevHeight;
        }
        setLoadingMore(false);
      });
    }
  }, [loadingMore, lineCount]);

  // Poll agent output
  useEffect(() => {
    let cancelled = false;

    const fetchAndParse = async () => {
      try {
        const data = await getAgentOutput(agentId, lineCount);
        if (cancelled) return;

        const cleaned = (data.lines || [])
          .map(stripAnsi)
          .filter((l: string) => {
            const t = l.trim();
            if (!t) return false;
            // Filter tmux chrome
            if (/^[─━═╌╍┄┅┈┉-]{4,}$/.test(t) && !/[┼├┤┬┴┌┐└┘│|+]/.test(t)) return false;
            if (/^[⏵⏴▶◀►◄]{2,}/.test(t) && /bypass permissions/.test(t)) return false;
            if (/^⬆/.test(t) && /context\)/.test(t)) return false;
            return true;
          });

        // Full reparse on each poll (protocol handles dedup)
        protocol.current.reset();
        const newEvents = protocol.current.parse(cleaned);
        setEvents(newEvents);
        setAgentState(protocol.current.getState());
        setRawLines(cleaned);
        setLoading(false);

        // Auto-scroll on first load — defer to after React renders the events
        if (firstLoad.current) {
          firstLoad.current = false;
          requestAnimationFrame(() => {
            requestAnimationFrame(() => {
              if (scrollRef.current) {
                scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
              }
            });
          });
        }
      } catch {
        // ignore polling errors
      }
    };

    firstLoad.current = true;
    setLoading(true);
    fetchAndParse();
    const interval = setInterval(fetchAndParse, 4000);
    return () => { cancelled = true; clearInterval(interval); };
  }, [agentId, lineCount]);

  // Auto-scroll when new events arrive (if near bottom)
  useEffect(() => {
    if (scrollRef.current && !firstLoad.current) {
      const el = scrollRef.current;
      const isNearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 100;
      if (isNearBottom) {
        el.scrollTop = el.scrollHeight;
      }
    }
  }, [events]);

  const handleInject = useCallback((text: string) => {
    setWaitingForOutput(true);
    injectToAgent(agentId, text).finally(() => {
      // The inject endpoint now polls for output, so by the time it returns
      // the next poll cycle should pick up new content
      setTimeout(() => setWaitingForOutput(false), 2000);
    });
  }, [agentId]);

  const handleSendKey = useCallback((key: string) => {
    sendKeyToAgent(agentId, key);
  }, [agentId]);

  // Find the last status event id so only it animates
  const lastStatusId = (() => {
    for (let i = events.length - 1; i >= 0; i--) {
      if (events[i].type === 'status') return events[i].id;
    }
    return null;
  })();

  const renderEvent = (event: ProtocolEvent) => {
    switch (event.type) {
      case 'user_message':
      case 'assistant_message':
        return <MessageBubble key={event.id} event={event} />;
      case 'tool_call':
        return <ToolCallCard key={event.id} event={event} onExpandToggle={event.expandable ? () => handleSendKey('ctrl-o') : undefined} />;
      case 'tool_request':
        return <ToolApprovalCard key={event.id} event={event} onAction={handleInject} />;
      case 'options':
        return <OptionsCard key={event.id} event={event} onSelect={handleInject} />;
      case 'expandable':
        return <ExpandableCard key={event.id} event={event} onToggle={() => handleSendKey('ctrl-o')} />;
      case 'status':
        return <StatusIndicator key={event.id} event={event} isActive={event.id === lastStatusId} />;
      case 'completion':
        return (
          <div key={event.id} className="flex justify-start">
            <div className="text-xs px-3 py-1.5 rounded-lg bg-green-600/10 text-green-300">
              {event.summary}
            </div>
          </div>
        );
      case 'error':
        return (
          <div key={event.id} className="flex justify-start">
            <div className="text-xs px-3 py-1.5 rounded-lg bg-red-600/10 text-red-300">
              {event.message}
            </div>
          </div>
        );
      default:
        return null;
    }
  };

  return (
    <div className={compact ? 'space-y-2' : 'flex flex-col flex-1 min-h-0'}>
      {/* Message area */}
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className={
          compact
            ? 'bg-neutral-950 rounded-lg p-2.5 max-h-64 overflow-y-auto space-y-2'
            : 'flex-1 overflow-y-auto p-4 space-y-2.5 min-h-0 overscroll-y-contain'
        }
      >
        {/* Load more indicator */}
        {loadingMore && (
          <div className="flex items-center justify-center py-2">
            <div className="w-2 h-2 bg-neutral-500 rounded-full animate-pulse" />
            <span className="text-xs text-neutral-500 ml-2">Loading more...</span>
          </div>
        )}
        {lineCount > 50 && !loadingMore && (
          <div className="text-center py-1">
            <span className="text-[10px] text-neutral-600">Showing {lineCount} lines</span>
          </div>
        )}
        {events.length > 0 ? (
          <>
            {events.map(renderEvent)}
            {waitingForOutput && (
              <div className="flex items-center gap-2 px-3 py-2">
                <div className="w-2 h-2 bg-blue-400 rounded-full animate-pulse" />
                <span className="text-xs text-neutral-500">Agent is processing...</span>
              </div>
            )}
          </>
        ) : waitingForOutput ? (
          <div className="flex items-center gap-2 px-3 py-2">
            <div className="w-2 h-2 bg-blue-400 rounded-full animate-pulse" />
            <span className="text-xs text-neutral-500">Agent is processing...</span>
          </div>
        ) : loading ? (
          <div className="flex items-center gap-2 px-3 py-2">
            <div className="w-2 h-2 bg-neutral-500 rounded-full animate-pulse" />
            <span className="text-xs text-neutral-500">Loading chat...</span>
          </div>
        ) : (
          <span className="text-xs text-neutral-600">No output captured</span>
        )}
      </div>

      {/* ActionBar — context-aware buttons + special keys */}
      {!compact && (
        <div className="px-3 py-1.5 border-t border-neutral-800/50">
          <ActionBar
            agentId={agentId}
            outputLines={rawLines}
            onInject={handleInject}
            onSendKey={handleSendKey}
            injectMode={true}
            devMode={false}
          />
        </div>
      )}

      {/* Input — only in non-compact (modal) mode */}
      {!compact && (
        <ChatInput
          agentId={agentId}
          disabled={agentState === 'working'}
          placeholder={agentState === 'working' ? 'Agent is working...' : 'Message your agent...'}
        />
      )}
    </div>
  );
}
