import { useState, useMemo } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { clsx } from 'clsx';
import {
  MessageSquare,
  Layers,
  User,
  ChevronDown,
  ChevronUp,
  Send,
  Hash,
  Smartphone,
  Monitor,
} from 'lucide-react';

// ── Types ──────────────────────────────────────────────────────────────

interface Conversation {
  id: string;
  subject: string;
  participants: string[];
  mode: string;
  status: string;
  tenant_id: string;
  iteration_count: number;
  created_at: string;
  updated_at: string;
  last_body: string;
  from_agent: string;
  to_agent: string;
  msg_type: string;
  last_msg_at: string;
  channel: string;
  message_count?: number;
}

interface ProjectGroup {
  project: string;
  conversations: Conversation[];
}

interface ThreadMessage {
  id: string;
  from_agent: string;
  to_agent: string;
  subject?: string;
  body: string;
  type?: string;
  status?: string;
  created_at: string;
  channel: string;
}

type ViewMode = 'unified' | 'projects' | 'operator';

const BASE = '/api';

async function fetchChatHistory(view: ViewMode, project?: string) {
  const params = new URLSearchParams({ view, limit: '50' });
  if (project) params.set('project', project);
  const res = await fetch(`${BASE}/chat-history?${params}`);
  if (!res.ok) throw new Error(`chat-history: ${res.status}`);
  return res.json();
}

async function fetchThread(conversationId: string) {
  const res = await fetch(`${BASE}/chat-history/thread/${conversationId}`);
  if (!res.ok) throw new Error(`thread: ${res.status}`);
  return res.json();
}

async function sendMessage(payload: {
  conversation_id: string;
  from: string;
  to: string;
  subject: string;
  body: string;
}) {
  const res = await fetch(`${BASE}/messages/send`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      from: payload.from,
      to: payload.to,
      subject: payload.subject,
      body: payload.body,
      conversation_id: payload.conversation_id,
    }),
  });
  if (!res.ok) throw new Error(`send: ${res.status}`);
  return res.json();
}

// ── Helpers ──────────────────────────────────────────────────────────

function relativeTime(ts: string): string {
  if (!ts) return '';
  const diff = Date.now() - new Date(ts).getTime();
  const seconds = Math.floor(diff / 1000);
  if (seconds < 5) return 'just now';
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function formatTime(ts: string): string {
  if (!ts) return '';
  try {
    return new Date(ts).toLocaleString(undefined, {
      month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  } catch { return ts; }
}

const AGENT_COLORS = [
  'bg-blue-600', 'bg-emerald-600', 'bg-purple-600', 'bg-amber-600',
  'bg-rose-600', 'bg-cyan-600', 'bg-indigo-600', 'bg-pink-600',
];
function agentColor(name: string): string {
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = name.charCodeAt(i) + ((hash << 5) - hash);
  return AGENT_COLORS[Math.abs(hash) % AGENT_COLORS.length];
}
function agentInitial(name: string): string {
  return name ? name.charAt(0).toUpperCase() : '?';
}

function ChannelBadge({ channel }: { channel: string }) {
  if (channel === 'telegram') {
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-blue-900/60 text-blue-300 border border-blue-800/50">
        <Smartphone size={10} /> Telegram
      </span>
    );
  }
  if (channel === 'voice') {
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-purple-900/60 text-purple-300 border border-purple-800/50">
        Voice
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-neutral-800 text-neutral-400 border border-neutral-700">
      <Monitor size={10} /> Dashboard
    </span>
  );
}

function StatusDot({ status }: { status: string }) {
  const color =
    status === 'open' ? 'bg-green-500' :
    status === 'closed' ? 'bg-neutral-600' :
    status === 'pending' ? 'bg-amber-500' :
    'bg-neutral-600';
  return <span className={clsx('inline-block w-1.5 h-1.5 rounded-full', color)} title={status} />;
}

// ── Thread panel ───────────────────────────────────────────────────────

function ThreadPanel({
  conversationId,
  conversation,
}: {
  conversationId: string;
  conversation: Conversation;
}) {
  const [replyText, setReplyText] = useState('');
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState('');
  const [sentOk, setSentOk] = useState(false);
  const queryClient = useQueryClient();

  const { data, isLoading } = useQuery({
    queryKey: ['chat-thread', conversationId],
    queryFn: () => fetchThread(conversationId),
    refetchInterval: 5000,
  });

  const messages: ThreadMessage[] = data?.messages ?? [];

  const handleSend = async () => {
    if (!replyText.trim()) return;
    setSending(true);
    setSendError('');
    try {
      const to = conversation.participants.find(p => p !== 'operator') ||
        conversation.from_agent !== 'operator' ? conversation.from_agent : conversation.to_agent;
      await sendMessage({
        conversation_id: conversationId,
        from: 'operator',
        to: to || 'gm',
        subject: conversation.subject || 'Reply',
        body: replyText.trim(),
      });
      setReplyText('');
      setSentOk(true);
      setTimeout(() => setSentOk(false), 3000);
      queryClient.invalidateQueries({ queryKey: ['chat-thread', conversationId] });
      queryClient.invalidateQueries({ queryKey: ['chat-history'] });
    } catch (err: any) {
      setSendError(err.message);
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="mt-2 rounded-lg border border-neutral-700 bg-neutral-950 overflow-hidden">
      {/* Thread messages */}
      <div className="max-h-80 overflow-y-auto divide-y divide-neutral-800/50">
        {isLoading && (
          <div className="text-center text-neutral-600 py-6 text-sm">Loading thread...</div>
        )}
        {!isLoading && messages.length === 0 && (
          <div className="text-center text-neutral-600 py-6 text-sm">No messages yet</div>
        )}
        {messages.map((msg) => {
          const isOperator = msg.from_agent === 'operator';
          return (
            <div key={msg.id} className="flex gap-3 px-4 py-3">
              <div
                className={clsx(
                  'w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-bold text-white shrink-0 mt-0.5',
                  agentColor(msg.from_agent)
                )}
                title={msg.from_agent}
              >
                {agentInitial(msg.from_agent)}
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-0.5">
                  <span className={clsx('text-xs font-semibold', isOperator ? 'text-violet-400' : 'text-neutral-300')}>
                    {msg.from_agent}
                  </span>
                  {msg.to_agent && (
                    <span className="text-xs text-neutral-600">to {msg.to_agent}</span>
                  )}
                  <span className="text-[10px] text-neutral-700 font-mono ml-auto">
                    {formatTime(msg.created_at)}
                  </span>
                </div>
                <p className="text-sm text-neutral-300 whitespace-pre-wrap break-words">
                  {msg.body || msg.subject || '(empty)'}
                </p>
              </div>
            </div>
          );
        })}
      </div>

      {/* Reply input — only for non-telegram (telegram is read-only display) */}
      {conversationId !== 'telegram' && (
        <div className="border-t border-neutral-800 p-3 flex gap-2 bg-neutral-900/50">
          <input
            type="text"
            value={replyText}
            onChange={(e) => setReplyText(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); } }}
            placeholder="Continue this conversation..."
            className="flex-1 bg-neutral-800 border border-neutral-700 text-neutral-200 text-sm rounded-lg px-3 py-2 focus:outline-none focus:border-neutral-500 placeholder-neutral-600"
          />
          <button
            onClick={handleSend}
            disabled={sending || !replyText.trim()}
            className="px-3 py-2 rounded-lg bg-violet-600 hover:bg-violet-500 disabled:opacity-40 disabled:cursor-not-allowed text-white text-sm font-medium flex items-center gap-1.5 transition-colors"
          >
            <Send size={14} />
            {sending ? 'Sending...' : 'Send'}
          </button>
        </div>
      )}
      {sendError && (
        <p className="text-xs text-red-400 px-4 pb-2">{sendError}</p>
      )}
      {sentOk && (
        <p className="text-xs text-green-400 px-4 pb-2">Message sent</p>
      )}
    </div>
  );
}

// ── Conversation card ──────────────────────────────────────────────────

function ConversationCard({ conv }: { conv: Conversation }) {
  const [expanded, setExpanded] = useState(false);

  const participants = Array.isArray(conv.participants)
    ? conv.participants.filter(Boolean)
    : [];

  const displaySubject = conv.subject && conv.subject !== '(no subject)'
    ? conv.subject
    : conv.from_agent && conv.to_agent
      ? `${conv.from_agent} → ${conv.to_agent}`
      : conv.id;

  const preview = (conv.last_body || '').slice(0, 120) + (conv.last_body?.length > 120 ? '…' : '');

  return (
    <div className={clsx(
      'border border-neutral-800 rounded-xl overflow-hidden transition-all hover:border-neutral-700',
      expanded && 'border-neutral-600'
    )}>
      <button
        className="w-full text-left px-4 py-3 bg-neutral-900/50 hover:bg-neutral-900 transition-colors"
        onClick={() => setExpanded(v => !v)}
      >
        <div className="flex items-start gap-3">
          {/* Agent avatar */}
          <div
            className={clsx(
              'w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold text-white shrink-0 mt-0.5',
              agentColor(conv.from_agent || conv.tenant_id || 'x')
            )}
          >
            {agentInitial(conv.from_agent || conv.tenant_id || 'x')}
          </div>

          {/* Main content */}
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-sm font-medium text-white truncate">
                {displaySubject}
              </span>
              <StatusDot status={conv.status} />
              <ChannelBadge channel={conv.channel} />
              {conv.tenant_id && (
                <span className="text-[10px] text-neutral-600 font-mono">
                  {conv.tenant_id}
                </span>
              )}
            </div>

            {/* Participants */}
            {participants.length > 0 && (
              <div className="flex items-center gap-1 mt-0.5 flex-wrap">
                {participants.slice(0, 4).map(p => (
                  <span key={p} className="text-[10px] text-neutral-500 bg-neutral-800 px-1.5 py-0.5 rounded">
                    {p}
                  </span>
                ))}
                {participants.length > 4 && (
                  <span className="text-[10px] text-neutral-600">+{participants.length - 4} more</span>
                )}
              </div>
            )}

            {/* Preview */}
            {preview && (
              <p className="text-xs text-neutral-500 mt-1 line-clamp-1">{preview}</p>
            )}
          </div>

          {/* Right meta */}
          <div className="flex flex-col items-end gap-1 shrink-0 text-right">
            <span className="text-[10px] text-neutral-600 font-mono whitespace-nowrap">
              {relativeTime(conv.last_msg_at || conv.updated_at)}
            </span>
            {conv.iteration_count > 0 && (
              <span className="text-[10px] text-neutral-700">{conv.iteration_count} msgs</span>
            )}
            {expanded ? (
              <ChevronUp size={14} className="text-neutral-600" />
            ) : (
              <ChevronDown size={14} className="text-neutral-600" />
            )}
          </div>
        </div>
      </button>

      {expanded && (
        <div className="px-4 pb-4 bg-neutral-950">
          <ThreadPanel
            conversationId={conv.id}
            conversation={conv}
          />
        </div>
      )}
    </div>
  );
}

// ── the operator message card (for operator view) ─────────────────────────────────

function OperatorMessageCard({ msg }: { msg: any }) {
  return (
    <div className="flex gap-3 px-4 py-3 border border-neutral-800 rounded-xl bg-neutral-900/40 hover:bg-neutral-900/70 transition-colors">
      <div
        className={clsx(
          'w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold text-white shrink-0 mt-0.5',
          agentColor(msg.from || 'operator')
        )}
      >
        {agentInitial(msg.from || 'operator')}
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-0.5 flex-wrap">
          <span className="text-xs font-semibold text-neutral-300">{msg.from}</span>
          {msg.to && <span className="text-xs text-neutral-600">to {msg.to}</span>}
          <ChannelBadge channel={msg.channel} />
          <span className="text-[10px] text-neutral-700 font-mono ml-auto">
            {relativeTime(msg.created_at)}
          </span>
        </div>
        <p className="text-sm text-neutral-400 line-clamp-2">{msg.body || '(empty)'}</p>
      </div>
    </div>
  );
}

// ── Main page ──────────────────────────────────────────────────────────

const VIEWS: { id: ViewMode; label: string; icon: React.ComponentType<any> }[] = [
  { id: 'unified', label: 'All Channels', icon: Layers },
  { id: 'projects', label: 'By Project', icon: Hash },
  { id: 'operator', label: 'My Messages', icon: User },
];

export default function ChatHistory() {
  const [view, setView] = useState<ViewMode>('unified');
  const [channelFilter, setChannelFilter] = useState('all');
  const [projectFilter, setProjectFilter] = useState('');

  const { data, isLoading, error } = useQuery({
    queryKey: ['chat-history', view, projectFilter],
    queryFn: () => fetchChatHistory(view, projectFilter || undefined),
    refetchInterval: 15_000,
  });

  // Derive conversations list based on view
  const conversations: Conversation[] = useMemo(() => {
    if (!data) return [];
    if (view === 'projects') {
      const groups: ProjectGroup[] = data.groups || [];
      return groups.flatMap(g => g.conversations);
    }
    return data.conversations || [];
  }, [data, view]);

  const groups: ProjectGroup[] = useMemo(() => {
    if (view !== 'projects' || !data) return [];
    return data.groups || [];
  }, [data, view]);

  const operatorMessages: any[] = useMemo(() => {
    if (view !== 'operator' || !data) return [];
    return data.messages || [];
  }, [data, view]);

  // Channel filter options derived from unified data
  const channels = useMemo(() => {
    const set = new Set<string>();
    conversations.forEach(c => set.add(c.channel || 'dashboard'));
    return Array.from(set);
  }, [conversations]);

  const filteredConversations = useMemo(() => {
    if (channelFilter === 'all') return conversations;
    return conversations.filter(c => (c.channel || 'dashboard') === channelFilter);
  }, [conversations, channelFilter]);

  // All unique projects from unified data for filter dropdown
  const projects = useMemo(() => {
    const set = new Set<string>();
    conversations.forEach(c => { if (c.tenant_id) set.add(c.tenant_id); });
    return Array.from(set).sort();
  }, [conversations]);

  return (
    <div className="p-6 space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <h1 className="text-2xl font-bold text-white">Chat History</h1>
        <div className="flex items-center gap-2 text-sm text-neutral-500">
          <MessageSquare size={14} />
          <span>
            {view === 'operator'
              ? `${operatorMessages.length} messages`
              : `${filteredConversations.length} conversations`
            }
          </span>
        </div>
      </div>

      {/* View tabs */}
      <div className="flex gap-1 bg-neutral-900 rounded-xl p-1 w-fit border border-neutral-800">
        {VIEWS.map(v => {
          const Icon = v.icon;
          const active = view === v.id;
          return (
            <button
              key={v.id}
              onClick={() => setView(v.id)}
              className={clsx(
                'flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors',
                active
                  ? 'bg-neutral-800 text-white shadow-sm'
                  : 'text-neutral-500 hover:text-neutral-300'
              )}
            >
              <Icon size={14} />
              {v.label}
            </button>
          );
        })}
      </div>

      {/* Filters row */}
      <div className="flex items-center gap-3 flex-wrap">
        {view === 'unified' && (
          <>
            {/* Channel filter pills */}
            <div className="flex gap-1.5">
              {['all', ...channels].map(ch => (
                <button
                  key={ch}
                  onClick={() => setChannelFilter(ch)}
                  className={clsx(
                    'px-3 py-1 rounded-full text-xs font-medium transition-colors',
                    channelFilter === ch
                      ? 'bg-neutral-700 text-white'
                      : 'bg-neutral-900 text-neutral-500 border border-neutral-800 hover:border-neutral-700'
                  )}
                >
                  {ch === 'all' ? 'All' : ch}
                </button>
              ))}
            </div>
            {/* Project filter */}
            {projects.length > 0 && (
              <select
                value={projectFilter}
                onChange={(e) => setProjectFilter(e.target.value)}
                className="bg-neutral-900 border border-neutral-800 text-neutral-300 text-sm rounded-lg px-3 py-1.5 focus:outline-none focus:border-neutral-600"
              >
                <option value="">All projects</option>
                {projects.map(p => (
                  <option key={p} value={p}>{p}</option>
                ))}
              </select>
            )}
          </>
        )}

        {view === 'projects' && (
          <select
            value={projectFilter}
            onChange={(e) => setProjectFilter(e.target.value)}
            className="bg-neutral-900 border border-neutral-800 text-neutral-300 text-sm rounded-lg px-3 py-1.5 focus:outline-none focus:border-neutral-600"
          >
            <option value="">All projects</option>
            {groups.map(g => (
              <option key={g.project} value={g.project}>{g.project}</option>
            ))}
          </select>
        )}
      </div>

      {/* Error state */}
      {error && (
        <div className="rounded-xl border border-red-900/50 bg-red-950/20 px-4 py-3 text-sm text-red-400">
          Failed to load chat history: {(error as Error).message}
        </div>
      )}

      {/* Loading */}
      {isLoading && (
        <div className="text-neutral-600 text-sm py-8 text-center">Loading conversations...</div>
      )}

      {/* ── Unified / Projects view ── */}
      {!isLoading && view !== 'operator' && (
        <>
          {view === 'projects' ? (
            /* Grouped by project */
            <div className="space-y-6">
              {(projectFilter
                ? groups.filter(g => g.project === projectFilter)
                : groups
              ).map(group => (
                <div key={group.project}>
                  <div className="flex items-center gap-2 mb-3">
                    <Hash size={14} className="text-neutral-600" />
                    <span className="text-sm font-semibold text-neutral-400 uppercase tracking-wide">
                      {group.project}
                    </span>
                    <span className="text-xs text-neutral-700">
                      {group.conversations.length} thread{group.conversations.length !== 1 ? 's' : ''}
                    </span>
                  </div>
                  <div className="space-y-2">
                    {group.conversations.map(conv => (
                      <ConversationCard key={conv.id} conv={conv} />
                    ))}
                  </div>
                </div>
              ))}
              {groups.length === 0 && (
                <div className="text-center text-neutral-600 py-12 text-sm">
                  No conversations found
                </div>
              )}
            </div>
          ) : (
            /* Unified list */
            <div className="space-y-2">
              {filteredConversations.length === 0 && (
                <div className="text-center text-neutral-600 py-12 text-sm">
                  No conversations match the current filters
                </div>
              )}
              {filteredConversations.map(conv => (
                <ConversationCard key={conv.id} conv={conv} />
              ))}
            </div>
          )}
        </>
      )}

      {/* ── the operator view ── */}
      {!isLoading && view === 'operator' && (
        <div className="space-y-2">
          {operatorMessages.length === 0 && (
            <div className="text-center text-neutral-600 py-12 text-sm">
              No messages found
            </div>
          )}
          {operatorMessages.map((msg: any) => (
            <OperatorMessageCard key={msg.id} msg={msg} />
          ))}
        </div>
      )}
    </div>
  );
}
