/**
 * SubagentTranscriptView: one subagent's own transcript (the agent-tree drill-in), read-only.
 *
 * The same rows and skin as the agent's chat (components/kit/ThreadView.tsx; Dev falls back to Bubbles), from
 * GET /api/agents/:id/subagents/:aid/transcript[/stream]. Nothing of the seat's own chat is touched: no decisions,
 * no menu, no call cards, no compaction notices, no reactions, no pending sends, no typing or step state. Those
 * all belong to the seat, and this view must never answer for it or retire its sends.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { useStickToBottom } from '../../lib/useStickToBottom';
import { buildRenderList, fetchTranscript, type ChatItem } from '../../lib/transcript';
import { subscribeTranscriptStream } from '../../lib/transcriptStream';
import { GroupedNodeView } from './TranscriptCards';
import { groupToolRuns } from '../../lib/toolGroups';
import { markResults } from '../../lib/threadKit/results';
import { interleave } from '../../lib/decisions';
import { agentRows } from '../../lib/threadKit/view';
import ThreadView from '../kit/ThreadView';
import { useChatStyle } from '../../lib/agentPrefs';
import { subagentSkin } from '../../lib/subagentView';

interface Props {
  agentId: string;
  subagentId: string;
  banner: { text: string; backLabel: string; onBack: () => void };
}

export default function SubagentTranscriptView({ agentId, subagentId, banner }: Props) {
  const [items, setItems] = useState<ChatItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const columnRef = useRef<HTMLDivElement>(null);
  const stick = useStickToBottom(scrollRef, { rows: columnRef });

  // The seat chat's lanes: one poll at once, then the stream, with the 3 s poll as its fallback.
  useEffect(() => {
    let cancelled = false;
    let streamLive = false;
    let pollIv: ReturnType<typeof setInterval> | undefined;
    const tick = async () => {
      try {
        const r = await fetchTranscript(agentId, 150, { subagentId });
        if (cancelled || streamLive) return;
        setItems(r.items || []);
        setErr(r.error || null);
        setLoading(false);
      } catch (e) {
        if (!cancelled && !streamLive) { setErr(String(e)); setLoading(false); }
      }
    };
    void tick();
    const dispose = subscribeTranscriptStream(agentId, 150, {
      onItems: (its) => { if (cancelled) return; streamLive = true; setItems(its); setErr(null); setLoading(false); },
      onFallback: () => { if (cancelled || pollIv) return; streamLive = false; pollIv = setInterval(tick, 3000); },
    }, { subagentId });
    return () => { cancelled = true; dispose(); if (pollIv) clearInterval(pollIv); };
  }, [agentId, subagentId]);

  const skin = subagentSkin(useChatStyle(agentId));
  const rows = useMemo(() => {
    const nodes = groupToolRuns(markResults(buildRenderList(items), agentId));
    return agentRows(interleave(nodes, []), [], 'idle', { modern: skin === 'modern' });
  }, [items, agentId, skin]);
  const follow = useMemo(() => ({ follow: stick.follow }), [stick.follow]);

  return (
    <div className="flex flex-col flex-1 min-h-0" data-testid="subagent-transcript">
      <div data-testid="subagent-banner" className="shrink-0 border-b border-amber-700/40 bg-amber-500/10">
        <div className="w-full max-w-[860px] mx-auto px-4 py-1.5 flex items-center gap-3 text-px12 text-amber-700 dark:text-amber-200/90">
          <span className="truncate min-w-0">{banner.text}</span>
          <button type="button" onClick={banner.onBack} className="ml-auto shrink-0 underline hover:opacity-80">
            {banner.backLabel}
          </button>
        </div>
      </div>
      <div ref={scrollRef} onScroll={stick.onScroll} data-testid="transcript-scroll"
        className="flex-1 overflow-y-auto min-h-0 overflow-x-hidden overscroll-y-contain flex flex-col">
        <div ref={columnRef}
          className={['mt-auto w-full max-w-[calc(860px+var(--ui-col-extra,0px))] mx-auto px-4 pt-4 pb-4 ui-feed-gap', skin === 'modern' ? 'kit-modern' : ''].join(' ').trim()}>
          {loading ? (
            <div className="flex items-center gap-2 px-1 py-2">
              <div className="w-2 h-2 bg-tone-500 rounded-full animate-pulse" />
              <span className="text-xs text-tone-500">Loading transcript…</span>
            </div>
          ) : rows.length === 0 ? (
            <span className="text-xs text-tone-600">{err ? `No transcript (${err})` : 'No messages yet'}</span>
          ) : (
            <ThreadView rows={rows} skin={skin} times={skin === 'modern'} stick={follow}
              renderNode={(node, interrupted) => <GroupedNodeView node={node} interrupted={interrupted} look="lines" />} />
          )}
        </div>
      </div>
    </div>
  );
}
