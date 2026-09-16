/**
 * TranscriptChatView — transcript-driven chat.
 *
 * Renders the conversation from the session transcript (clean, isolated
 * per-message units) and shows live agent state as a pill sourced from the
 * /agents v2 detector (agent-state-truth's lane) — NOT parsed from the pane.
 *
 * This is the single chat rendering grammar intended to be shared with iOS
 * (ConversationView). Send transport reuses the existing gateway injection.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { buildRenderList, fetchTranscript, type ChatItem } from '../../lib/transcript';
import { subscribeTranscriptStream } from '../../lib/transcriptStream';
import { RenderNodeView } from './TranscriptCards';
import { normalizeAgentState, STATE_STYLE, type LiveState } from '../../lib/agentStatus';
import OptionsMenuCard, { type PendingMenu } from './OptionsMenuCard';

export type { LiveState };

interface Props {
  agentId: string;
  /** Harness/fixture mode: render these items instead of fetching. */
  fixtureItems?: ChatItem[];
  /** Live agent state for the status pill (raw detector OR gateway-mapped vocab). */
  state?: string;
  /** Draft text typed-but-unsent in the agent's composer (detector stranded.text). */
  strandedText?: string;
  /** Live decision menu on the agent's screen (detector pending_menu). Renders
   *  an answerable OptionsCard below the transcript when present. */
  pendingMenu?: PendingMenu | null;
  compact?: boolean;
}

// State vocabulary + colors live in lib/agentStatus (shared with the title dot +
// aligned with the iOS app). The pill NEVER renders the raw status string — only
// a fixed STATE_STYLE label — so any unrecognized value (incl. a self-reported
// multi-KB blob) collapses to the neutral 'unknown' pill.
function StatePill({ state }: { state: LiveState }) {
  const s = STATE_STYLE[state] || STATE_STYLE.unknown;
  return (
    <span className={`inline-flex items-center gap-1.5 text-[11px] ${s.text}`}>
      <span className={`w-2 h-2 rounded-full ${s.dot}`} />
      {s.label}
    </span>
  );
}

export default function TranscriptChatView({ agentId, fixtureItems, state = 'unknown', strandedText, pendingMenu, compact }: Props) {
  const [items, setItems] = useState<ChatItem[]>(fixtureItems || []);
  const [loading, setLoading] = useState(!fixtureItems);
  const [err, setErr] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const atBottom = useRef(true);

  // fixture mode: render given items, no polling
  useEffect(() => {
    if (fixtureItems) { setItems(fixtureItems); setLoading(false); }
  }, [fixtureItems]);

  // live mode: SSE stream (F1) with the interval poll as the fallback lane.
  // One immediate poll fetch always runs first so the chat renders instantly
  // even if the stream endpoint is unavailable (deploy skew, proxy, old API) —
  // the working poll path never regresses.
  useEffect(() => {
    if (fixtureItems) return;
    let cancelled = false;
    let streamLive = false; // once stream items arrive, poll results never overwrite
    let pollIv: ReturnType<typeof setInterval> | undefined;

    const tick = async () => {
      try {
        const r = await fetchTranscript(agentId, 150);
        if (cancelled || streamLive) return;
        setItems(r.items || []);
        setErr(r.error || null);
        setLoading(false);
      } catch (e) {
        if (!cancelled && !streamLive) { setErr(String(e)); setLoading(false); }
      }
    };

    tick(); // instant backfill regardless of stream health
    const disposeStream = subscribeTranscriptStream(agentId, 150, {
      onItems: (its) => {
        if (cancelled) return;
        streamLive = true;
        setItems(its);
        setErr(null);
        setLoading(false);
      },
      onFallback: () => {
        if (cancelled || pollIv) return;
        streamLive = false;
        pollIv = setInterval(tick, 3000); // today's poll cadence
      },
    });

    return () => {
      cancelled = true;
      disposeStream();
      if (pollIv) clearInterval(pollIv);
    };
  }, [agentId, fixtureItems]);

  const nodes = useMemo(() => buildRenderList(items), [items]);
  const st = normalizeAgentState(state);

  // autoscroll if pinned to bottom
  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
  };
  useEffect(() => {
    const el = scrollRef.current;
    if (el && atBottom.current) el.scrollTop = el.scrollHeight;
  }, [nodes.length]);

  return (
    <div className={compact ? 'space-y-2' : 'flex flex-col flex-1 min-h-0'}>
      <div
        ref={scrollRef}
        onScroll={onScroll}
        data-testid="transcript-scroll"
        className={
          compact
            ? 'bg-neutral-950 rounded-lg p-3 max-h-96 overflow-y-auto space-y-2.5'
            : 'flex-1 overflow-y-auto p-4 space-y-2.5 min-h-0 overscroll-y-contain'
        }
      >
        {loading ? (
          <div className="flex items-center gap-2 px-1 py-2">
            <div className="w-2 h-2 bg-neutral-500 rounded-full animate-pulse" />
            <span className="text-xs text-neutral-500">Loading conversation…</span>
          </div>
        ) : nodes.length === 0 ? (
          <span className="text-xs text-neutral-600">{err ? `No transcript (${err})` : 'No messages yet'}</span>
        ) : (
          nodes.map((n) => <RenderNodeView key={n.key} node={n} />)
        )}

        {/* live decision menu (detector pending_menu) — answerable below the last
            message; menus render no transcript line so this IS how chat surfaces
            a decision-waiting agent (previously invisible/unanswerable in chat). */}
        {pendingMenu && pendingMenu.options?.length > 0 && (
          <OptionsMenuCard agentId={agentId} menu={pendingMenu} />
        )}

        {/* in-flight indicator when the agent is mid-turn (transcript not flushed yet) */}
        {st === 'working' && (
          <div className="flex items-center gap-2 px-1 py-1">
            <div className="w-1.5 h-1.5 bg-amber-400 rounded-full animate-pulse" />
            <span className="text-[11px] text-neutral-500 italic">agent is working…</span>
          </div>
        )}
      </div>

      {/* status bar: live state pill + optional unsent-draft chip */}
      <div className="flex items-center gap-3 px-3 py-1.5 border-t border-neutral-800/50">
        <StatePill state={st} />
        {strandedText && (
          <span className="text-[10px] text-yellow-500/90 truncate">
            ✎ unsent: "{strandedText.slice(0, 60)}"
          </span>
        )}
      </div>
    </div>
  );
}
