import { useState } from 'react';
import { clsx } from 'clsx';
import { X, FileText, AlertTriangle, Link2 } from 'lucide-react';
import { normalizeAgentState, STATE_STYLE } from '../lib/agentStatus';
import { TierBadge } from './TierBadge';

export interface AgentDetailPanelProps {
  agent: {
    id: string; tier?: string; role?: string; status?: string; alive?: boolean;
    session?: string; cwd?: string; prompt_file?: string; machine?: string;
    last_seen?: string; current_task?: string;
  };
  connectionCount: number;
  /** Peer agent ids this agent has traffic with, busiest first. Optional: without it the
   *  panel shows the count alone instead of an empty list that reads as "none". */
  connections?: string[];
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
              <span className="break-words underline decoration-neutral-700">{agent.prompt_file || 'view'}</span>
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
                      key={peer}
                      type="button"
                      onClick={() => onOpenConversation(peer)}
                      title={`Open ${agent.id} ⇄ ${peer}`}
                      className="px-1.5 py-0.5 rounded text-[11px] bg-neutral-800 text-neutral-300 hover:bg-neutral-700 hover:text-neutral-100 focus:outline-none focus:ring-1 focus:ring-sky-500 break-words"
                    >
                      {peer}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Live feed + message box mount here at build-order step 8. Intentionally
            not built in this chunk (scope cut per build's handoff). */}
        <div className="rounded-xl border border-dashed border-neutral-800 p-4 text-xs text-neutral-600">
          Live feed and message box mount here (step 8).
        </div>

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
