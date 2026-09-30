import { useEffect, useRef, useState } from 'react';
import { clsx } from 'clsx';
import { X, FileText, AlertTriangle, Link2, Copy, Check, ArrowDown, WifiOff, Send } from 'lucide-react';
import { normalizeAgentState, STATE_STYLE } from '../lib/agentStatus';
import { TierBadge } from './TierBadge';
import { buildRenderList, toolSummary, fetchTranscript, type RenderNode } from '../lib/transcript';
import { subscribeTranscriptStream } from '../lib/transcriptStream';
import { isNearBottom } from '../lib/liveFeedScroll';
import { sendToAgent, isDelivered, isHeld, isQueued, isComposerHold, describeSendState, type SendState } from '../lib/agentSend';

export interface AgentDetailPanelProps {
  agent: {
    id: string; tier?: string; role?: string; status?: string; alive?: boolean;
    session?: string; cwd?: string; machine?: string;
    /** Path to the seat's prompt file. The live /api/agents payload calls this
     *  `system_prompt` (e.g. "prompts/build.md") — there is no `prompt_file` field on any
     *  row, which is what an earlier version of this interface asked for and never got.
     *  Caught by curling the endpoint rather than reading the spec. */
    system_prompt?: string;
    last_seen?: string; current_task?: string;
  };
  connectionCount: number;
  /** Peers this agent has traffic with, busiest first. `onGraph` says whether the peer is a
   *  node in the topology tree — telegram, operator and the beats are real correspondents but
   *  are not drawn, and review's browser pass flagged them leaking into a list that reads as
   *  "connections in the graph". They are KEPT rather than filtered out (gm<->telegram is the
   *  operator's own channel, and its conversation opens fine) and marked instead, because
   *  hiding real traffic costs more than the inconsistency does. Optional: without it the
   *  panel shows the count alone rather than an empty list that reads as "none". */
  connections?: { id: string; onGraph: boolean }[];
  onOpenConversation: (otherAgentId: string) => void;
  onClose: () => void;
  recentErrors?: { at: string; text: string }[];
}

type Liveness = 'live' | 'stale' | 'down';

// Section 16 lock: liveness reads from agent.alive (the /api/agents deduped
// field) only — never tmux/session fields, never /api/system. 'stale' reuses
// the detector's own 'stalled' classification (agentStatus.ts: 600s quiet
// while working) rather than inventing a second staleness definition.
function computeLiveness(agent: AgentDetailPanelProps['agent']): Liveness {
  if (agent.alive === false) return 'down';
  return normalizeAgentState(agent.status) === 'stalled' ? 'stale' : 'live';
}

const LIVENESS_LABEL: Record<Liveness, string> = { live: 'live', stale: 'stale', down: 'down' };
const LIVENESS_STYLE: Record<Liveness, { dot: string; text: string }> = {
  live: STATE_STYLE.idle,
  stale: STATE_STYLE.stalled,
  down: STATE_STYLE.offline,
};

function formatLastSeen(iso?: string): string | null {
  if (!iso) return null;
  const ms = Date.now() - new Date(iso).getTime();
  if (!Number.isFinite(ms)) return null;
  if (ms < 60_000) return 'just now';
  if (ms < 3_600_000) return `${Math.floor(ms / 60_000)}m ago`;
  if (ms < 86_400_000) return `${Math.floor(ms / 3_600_000)}h ago`;
  return `${Math.floor(ms / 86_400_000)}d ago`;
}

const FALLBACK_POLL_MS = 3000;

/** Plain-text form of a render node, shared by the copy button and (for
 * plain-text kinds) the rendered line itself. */
function blockText(node: RenderNode): string {
  switch (node.kind) {
    case 'tool': {
      const summary = toolSummary(node.tool, node.input);
      return node.result ? `${node.tool}: ${summary}\n${node.result}` : `${node.tool}: ${summary}`;
    }
    case 'queued_batch':
      return node.entries.map((e) => `${e.agent}: ${e.body}`).join('\n');
    default:
      return node.text;
  }
}

/** Docked (non-floating) live activity feed for one agent — real SSE via
 * transcriptStream.ts, falling back to the same poll path the chat view uses
 * if the stream drops (§12: show the drop, don't render stale as fresh). */
function DetailLiveFeed({ agentId }: { agentId: string }) {
  const [nodes, setNodes] = useState<RenderNode[]>([]);
  const [live, setLive] = useState(true);
  const [following, setFollowing] = useState(true);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let pollTimer: ReturnType<typeof setInterval> | null = null;

    const unsubscribe = subscribeTranscriptStream(agentId, 150, {
      onItems: (items) => setNodes(buildRenderList(items)),
      onFallback: () => {
        setLive(false);
        const poll = async () => {
          try {
            const res = await fetchTranscript(agentId, 150);
            setNodes(buildRenderList(res.items));
          } catch {
            // transient — the WifiOff banner already tells the reader this isn't live
          }
        };
        poll();
        pollTimer = setInterval(poll, FALLBACK_POLL_MS);
      },
    });

    return () => {
      unsubscribe();
      if (pollTimer) clearInterval(pollTimer);
    };
  }, [agentId]);

  useEffect(() => {
    if (!following) return;
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [nodes, following]);

  const handleScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    setFollowing(isNearBottom(el.scrollTop, el.scrollHeight, el.clientHeight));
  };

  const jumpToLatest = () => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
    setFollowing(true);
  };

  const copyBlock = (key: string, text: string) => {
    navigator.clipboard?.writeText(text).catch(() => {});
    setCopiedKey(key);
    setTimeout(() => setCopiedKey((k) => (k === key ? null : k)), 1500);
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-1.5">
        <h3 className="text-[11px] uppercase tracking-wider text-neutral-500">Live feed</h3>
        {!live && (
          <span className="flex items-center gap-1 text-[11px] text-amber-400">
            <WifiOff size={11} />
            Reconnecting — showing last poll
          </span>
        )}
      </div>
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="relative rounded-xl border border-neutral-800 bg-neutral-950 p-2.5 max-h-64 overflow-y-auto space-y-1.5"
      >
        {nodes.length === 0 && <p className="text-xs text-neutral-600">No activity yet</p>}
        {nodes.map((node) => {
          const text = blockText(node);
          return (
            <div key={node.key} className="group flex items-start gap-1.5 text-xs">
              <div className="min-w-0 flex-1">
                {node.kind === 'tool' ? (
                  <div>
                    <span className={clsx(
                      'inline-block px-1.5 py-0.5 rounded text-[10px] font-medium',
                      node.isError ? 'bg-red-900/40 text-red-300' : 'bg-neutral-800 text-neutral-300'
                    )}>
                      {node.tool}
                    </span>
                    <span className="text-neutral-500 ml-1.5 break-words">{toolSummary(node.tool, node.input)}</span>
                  </div>
                ) : (
                  <p className={clsx('break-words whitespace-pre-wrap', node.kind === 'thinking' ? 'text-neutral-600 italic' : 'text-neutral-300')}>
                    {text}
                  </p>
                )}
              </div>
              {text && (
                <button
                  onClick={() => copyBlock(node.key, text)}
                  className="shrink-0 p-1 text-neutral-600 hover:text-neutral-200 opacity-0 group-hover:opacity-100 transition-opacity"
                  aria-label="Copy"
                  title="Copy"
                >
                  {copiedKey === node.key ? <Check size={11} /> : <Copy size={11} />}
                </button>
              )}
            </div>
          );
        })}
        {!following && (
          <button
            onClick={jumpToLatest}
            className="sticky bottom-0 left-1/2 -translate-x-1/2 flex items-center gap-1 px-2 py-1 rounded-full text-[11px] bg-neutral-800 text-neutral-200 border border-neutral-700 hover:bg-neutral-700 shadow"
          >
            <ArrowDown size={11} />
            Jump to latest
          </button>
        )}
      </div>
    </div>
  );
}

const SEND_STATUS_STYLE: Record<string, string> = {
  sending: 'text-neutral-500',
  delivered: 'text-green-400',
  queued: 'text-amber-400',
  held: 'text-amber-400',
  error: 'text-red-400',
};

/** Message box for the panel — sending/delivered/held/queued vocabulary comes
 * from agentSend.ts (already tested) rather than inventing new labels. */
function MessageComposer({ agentId, disabled }: { agentId: string; disabled: boolean }) {
  const [text, setText] = useState('');
  const [status, setStatus] = useState<'idle' | 'sending' | SendState | 'error'>('idle');
  const [detail, setDetail] = useState('');

  const submit = async () => {
    const trimmed = text.trim();
    if (!trimmed || disabled || status === 'sending') return;
    setStatus('sending');
    setDetail('');
    const result = await sendToAgent(agentId, { text: trimmed });
    if (isComposerHold(result) || (!isDelivered(result) && !isQueued(result) && !isHeld(result))) {
      setStatus('error');
      setDetail(describeSendState(result) || 'Send failed');
      return;
    }
    setStatus(result.state as SendState);
    setDetail(describeSendState(result));
    setText('');
  };

  return (
    <div>
      <h3 className="text-[11px] uppercase tracking-wider text-neutral-500 mb-1.5">Message</h3>
      {/* Spec §5 asks for the box "disabled with a note when the agent is down". It used to
          swap the whole control out for the note, which review's DOM probe measured as an
          ABSENT input rather than a disabled one. The note alone reads as a rendering gap —
          the panel just stops where a control should be — whereas a control you can see is
          disabled tells the operator both that messaging exists here and why it is
          unavailable. One input, always rendered, `disabled` when down, note beside it. */}
      <div className="space-y-1.5">
        <div className="flex gap-2">
          <input
            type="text"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') submit(); }}
            disabled={disabled}
            placeholder={disabled ? `${agentId} is down` : `Message ${agentId}...`}
            aria-describedby={disabled ? `msg-disabled-${agentId}` : undefined}
            className="flex-1 min-w-0 px-3 py-1.5 text-sm rounded-lg bg-neutral-900 border border-neutral-800 text-neutral-100 placeholder:text-neutral-600 focus:outline-none focus:border-neutral-600 disabled:opacity-50 disabled:cursor-not-allowed"
          />
          <button
            onClick={submit}
            disabled={disabled || status === 'sending' || !text.trim()}
            className="shrink-0 p-1.5 rounded-lg bg-neutral-800 text-neutral-300 hover:bg-neutral-700 disabled:opacity-40 disabled:cursor-not-allowed"
            aria-label="Send"
          >
            <Send size={14} />
          </button>
        </div>
        {disabled && (
          <p id={`msg-disabled-${agentId}`} className="text-[11px] text-neutral-500">
            Agent is down — messaging disabled.
          </p>
        )}
        {!disabled && status !== 'idle' && (
          <p className={clsx('text-[11px]', SEND_STATUS_STYLE[status] || 'text-neutral-500')}>
            {status === 'sending' ? 'sending…' : (detail || status)}
          </p>
        )}
      </div>
    </div>
  );
}

export function AgentDetailPanel({
  agent, connectionCount, connections, onOpenConversation, onClose, recentErrors,
}: AgentDetailPanelProps) {
  const liveness = computeLiveness(agent);
  const isDown = liveness === 'down';
  const lastSeen = formatLastSeen(agent.last_seen);

  const [promptOpen, setPromptOpen] = useState(false);
  const [promptContent, setPromptContent] = useState<string | null>(null);
  const [promptLoading, setPromptLoading] = useState(false);
  const [promptError, setPromptError] = useState<string | null>(null);

  const togglePrompt = async () => {
    if (promptOpen) { setPromptOpen(false); return; }
    setPromptOpen(true);
    if (promptContent !== null || promptLoading) return;
    setPromptLoading(true);
    setPromptError(null);
    try {
      const res = await fetch(`/api/agents/${encodeURIComponent(agent.id)}/prompt`);
      if (!res.ok) throw new Error(`${res.status}`);
      const data = await res.json();
      setPromptContent(data.content || 'No prompt file found');
    } catch {
      setPromptError('Failed to load prompt file');
    } finally {
      setPromptLoading(false);
    }
  };

  const details: { label: string; value: string | null }[] = [
    { label: 'Role', value: agent.role || null },
    { label: 'Tier', value: agent.tier || null },
    { label: 'Session (tmux)', value: agent.session || null },
    { label: 'Working folder', value: agent.cwd || null },
    { label: 'Machine', value: agent.machine || null },
  ];

  return (
    <div className={clsx(
      'flex flex-col h-full rounded-xl border bg-neutral-900',
      isDown ? 'border-red-900/60 opacity-80' : 'border-neutral-800'
    )}>
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-neutral-800 shrink-0">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-neutral-100 font-semibold break-words">{agent.id}</span>
            {agent.tier && <TierBadge tier={agent.tier} />}
            <span className={clsx('flex items-center gap-1 text-[11px] font-medium', LIVENESS_STYLE[liveness].text)}>
              <span className={clsx('w-1.5 h-1.5 rounded-full', LIVENESS_STYLE[liveness].dot)} />
              {LIVENESS_LABEL[liveness]}
            </span>
          </div>
          {isDown && lastSeen && (
            <p className="text-[11px] text-red-400/80 mt-0.5">last seen {lastSeen}</p>
          )}
        </div>
        <button onClick={onClose} className="p-1.5 text-neutral-500 hover:text-neutral-200 hover:bg-neutral-800 rounded-lg transition-colors shrink-0" aria-label="Close panel">
          <X size={16} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-4 min-h-0">
        {/* Details block */}
        <div className="space-y-1.5">
          {details.map(({ label, value }) => (
            <div key={label} className="flex gap-2 text-sm">
              <span className="text-neutral-500 shrink-0 w-32">{label}</span>
              <span className="text-neutral-300 break-words">{value || '—'}</span>
            </div>
          ))}

          {/* Prompt file — click to view (same source AgentCard uses for this) */}
          <div className="text-sm">
            <button
              onClick={togglePrompt}
              className="flex items-center gap-2 text-left text-neutral-300 hover:text-neutral-100 transition-colors"
            >
              <FileText size={13} className="text-neutral-500 shrink-0" />
              <span className="text-neutral-500 w-[7.5rem] shrink-0">Prompt file</span>
              <span className="break-words underline decoration-neutral-700">{agent.system_prompt || 'view'}</span>
            </button>
            {promptOpen && (
              <div className="mt-2 ml-[1.4rem] rounded-lg border border-neutral-800 bg-neutral-950 p-2.5">
                {promptLoading && <span className="text-xs text-neutral-500">Loading...</span>}
                {promptError && <span className="text-xs text-red-400">{promptError}</span>}
                {!promptLoading && !promptError && promptContent !== null && (
                  <pre className="text-xs text-neutral-300 font-mono whitespace-pre-wrap break-words leading-relaxed max-h-64 overflow-y-auto">{promptContent}</pre>
                )}
              </div>
            )}
          </div>

          {/* Spec §5 "connection count (click to list; each opens its conversation)".
              builder-2 correctly flagged that a bare count cannot drive the list half, so
              the parent now passes the peer ids it already has from /pair-counts.
              `connections` stays optional: with no list this degrades to the count alone
              rather than rendering an empty list that looks like "no connections". */}
          <div className="flex gap-2 text-sm items-start">
            <Link2 size={13} className="text-neutral-500 shrink-0 mt-1" />
            <span className="text-neutral-500 w-[7.375rem] shrink-0">Connections</span>
            <div className="min-w-0">
              <span className="text-neutral-300">{connectionCount}</span>
              {connections && connections.length > 0 && (
                <div className="flex flex-wrap gap-1 mt-1">
                  {connections.map((peer) => (
                    <button
                      key={peer.id}
                      type="button"
                      onClick={() => onOpenConversation(peer.id)}
                      title={peer.onGraph
                        ? `Open ${agent.id} ⇄ ${peer.id}`
                        : `Open ${agent.id} ⇄ ${peer.id} — not a node in the graph`}
                      className={clsx(
                        'px-1.5 py-0.5 rounded text-[11px] focus:outline-none focus:ring-1 focus:ring-sky-500 break-words',
                        peer.onGraph
                          ? 'bg-neutral-800 text-neutral-300 hover:bg-neutral-700 hover:text-neutral-100'
                          : 'bg-neutral-900 text-neutral-500 border border-dashed border-neutral-700 hover:text-neutral-300',
                      )}
                    >
                      {peer.id}{peer.onGraph ? '' : ' ·'}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Live feed + message box — build-order step 8. The key remounts
            (fresh subscription + cleared feed) on agent switch instead of
            resetting state inside the effect. The keys must be DISTINCT: these
            are siblings, and when both carried the bare agent.id React hit a
            duplicate-key collision and kept the stale DetailLiveFeed mounted,
            so transcriptStream's es.close() disposer never ran. Six switches
            leaked six EventSources and exhausted the browser's per-origin
            connection pool — the whole page stopped fetching. */}
        <DetailLiveFeed key={`feed-${agent.id}`} agentId={agent.id} />
        <MessageComposer key={`composer-${agent.id}`} agentId={agent.id} disabled={isDown} />

        {/* Recent problems */}
        <div>
          <h3 className="text-[11px] uppercase tracking-wider text-neutral-500 mb-1.5">Recent problems</h3>
          {!recentErrors || recentErrors.length === 0 ? (
            <p className="text-sm text-neutral-600">No recent errors</p>
          ) : (
            <ul className="space-y-1.5">
              {recentErrors.map((err, i) => (
                <li key={i} className="flex gap-2 text-sm">
                  <AlertTriangle size={13} className="text-red-400 shrink-0 mt-0.5" />
                  <div className="min-w-0">
                    <p className="text-neutral-300 break-words">{err.text}</p>
                    <p className="text-[11px] text-neutral-600">{err.at}</p>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
