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
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useStickToBottom } from '../../lib/useStickToBottom';
import { buildRenderList, fetchOlderTranscript, fetchTranscript, type ChatItem } from '../../lib/transcript';
import { useNearTop, useOlderPages } from '../../lib/threadKit/useOlderPages';
import { firstCursor, transcriptKey } from '../../lib/threadKit/paging';
import OlderPagesNote from '../kit/OlderPagesNote';
import { liveTextOf, subscribeTranscriptStream } from '../../lib/transcriptStream';
import type { LiveTextEvent } from '../../lib/transcript.gen';
import { GroupedNodeView } from './TranscriptCards';
import { groupToolRuns } from '../../lib/toolGroups';
import { normalizeAgentState, STATE_STYLE, type LiveState } from '../../lib/agentStatus';
import { styleForAgentState, isDegraded, type FeedVerdict } from '../../lib/feedLiveness';
import OptionsMenuCard, { type PendingMenu } from './OptionsMenuCard';
import { AgentSide } from './DecisionCard';
import { ChatAgentContext } from './chatAgent';
import { useQuery } from '@tanstack/react-query';
import { BatchRecordsContext, fetchBatchRecords } from '../../lib/batchRecords';
import { fetchDecisions, interleave, takeFocusRequest, focusTarget, FOCUS_DECISION_EVENT, type FocusRequest } from '../../lib/decisions';
import { userRows, visiblePending, type PendingSend } from '../../lib/pendingSends';
import { usePendingSends } from '../../stores/pendingSends';
import { foldCardAnswers } from '../../lib/systemMessages';
import { markResults } from '../../lib/threadKit/results';
import ThreadView from '../kit/ThreadView';
import { agentRows, type LiveText } from '../../lib/threadKit/view';
import { useChatStyle } from '../../lib/agentPrefs';

const NO_PENDING: PendingSend[] = [];

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
  /** Feed health from the SHARED rule (lib/feedLiveness). When the feed is not live the pill
   *  must not paint a live colour — that is the P1: a cached 'working' pulsed yellow on this
   *  very pill for an hour while the API was down. Optional so the fixture harness, which has
   *  no feed, renders unchanged. */
  feed?: FeedVerdict;
  /** The agent page carries its own LiveStatusLine above the composer, so it suppresses this
   *  bar. Two renderings of one fact is how the sidebar and the page came to disagree. */
  hideStatusBar?: boolean;
  /** Words the agent is still writing that the transcript does not hold yet (a live-text stream): drawn at the end of
   *  the thread until the finished message arrives (components/kit/ThreadView.tsx). */
  liveText?: LiveText | null;
}

// State vocabulary + colors live in lib/agentStatus (shared with the title dot +
// aligned with the iOS app). The pill NEVER renders the raw status string — only
// a fixed STATE_STYLE label — so any unrecognized value (incl. a self-reported
// multi-KB blob) collapses to the neutral 'unknown' pill.
function StatePill({ state, feed }: { state: LiveState; feed?: FeedVerdict }) {
  // One rule with every other surface: stale -> grey, updating -> dimmed last-known colour.
  const s = styleForAgentState(feed, STATE_STYLE[state] || STATE_STYLE.unknown);
  return (
    <span className={`inline-flex items-center gap-1.5 text-[11px] ${s.text}`}>
      <span className={`w-2 h-2 rounded-full ${s.dot}`} />
      {s.label}
    </span>
  );
}

export default function TranscriptChatView({ agentId, fixtureItems, state = 'unknown', strandedText, pendingMenu, compact, feed, hideStatusBar, liveText: liveTextProp }: Props) {
  const [items, setItems] = useState<ChatItem[]>(fixtureItems || []);
  // A headless seat's reply still being written, from this view's own stream (a caller's liveText wins).
  // Kept with the agent it came from, so a page that switches agents never draws the last one's words.
  const [liveEv, setLiveEv] = useState<{ agentId: string; ev: LiveTextEvent } | null>(null);
  const [loading, setLoading] = useState(!fixtureItems);
  const [err, setErr] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const columnRef = useRef<HTMLDivElement>(null);
  // FOLLOW THE TAIL ONLY WHILE THE READER IS AT IT, and otherwise keep what they are reading exactly where it is
  // while the thread changes (lib/useStickToBottom.ts; the rows are the column's children).
  const stick = useStickToBottom(scrollRef, { rows: columnRef });

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
      onLiveText: (ev) => { if (!cancelled) setLiveEv(ev.text ? { agentId, ev } : null); },
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
        setLiveEv(null);                          // the poll carries no live words
        pollIv = setInterval(tick, 3000); // today's poll cadence
      },
    });

    return () => {
      cancelled = true;
      disposeStream();
      if (pollIv) clearInterval(pollIv);
    };
  }, [agentId, fixtureItems]);

  // Scrolling up reads the chat's history a page at a time (lib/threadKit/useOlderPages.ts): the live window above,
  // kept current by the stream or the poll, with older pages added above it. A page is read from the route's own
  // cursor (`oldest_id`); the first from the first item of the live window that starts a log line of its own, since
  // the window's top can start partway through one line (the older page brings that line back whole).
  const loadPage = useCallback(async (before: string) => fetchOlderTranscript(agentId, before), [agentId]);
  const paged = useOlderPages<ChatItem>({ live: items, keyOf: transcriptKey, firstCursor, load: loadPage, resetKey: agentId,
    enabled: !fixtureItems && !loading });
  const sentinelRef = useRef<HTMLDivElement>(null);
  useNearTop(scrollRef, sentinelRef, () => { void paged.loadOlder(); }, paged.items.length, !fixtureItems && !loading && paged.hasOlder);
  const flat = useMemo(() => buildRenderList(paged.items), [paged.items]);
  const streamedLive = useMemo(
    // The live words belong after the newest line, so the live window is all they are checked against (older pages
    // hold nothing still being written).
    () => (fixtureItems || liveEv?.agentId !== agentId ? null : liveTextOf(liveEv.ev, items)),
    [liveEv, items, fixtureItems, agentId]);
  const liveText = liveTextProp !== undefined ? liveTextProp : streamedLive;
  // Just-sent bubbles (lib/pendingSends): drawn until their own row arrives. Derived in render as
  // well as retired in the store, so the frame that brings the row never also draws the bubble.
  const rows = useMemo(() => userRows(items), [items]);
  const setRows = usePendingSends((s) => s.setRows);
  const onAgentState = usePendingSends((s) => s.onAgentState);
  const pendingAll = usePendingSends((s) => s.byAgent[agentId] ?? NO_PENDING);
  const pending = useMemo(() => visiblePending(pendingAll, rows), [pendingAll, rows]);
  // A layout effect, so a composer waiting on this row clears its box before the row is painted.
  useLayoutEffect(() => { if (!fixtureItems && !loading) setRows(agentId, rows); }, [agentId, rows, loading, fixtureItems, setRows]);
  // Folding consecutive tool calls (C3) means a new call can join an existing group without
  // changing the number of rendered nodes, so autoscroll keys on the UNGROUPED count below.
  // Finished work (a background job, a subagent's report) becomes a result card first, so a subagent call is not
  // folded into a run of tool calls (lib/threadKit/results.ts).
  const nodes = useMemo(() => groupToolRuns(markResults(flat, agentId)), [flat, agentId]);
  // The agent's approval cards, as bubbles under the messages they belong to (DecisionBubble). Polled, so a
  // card answered anywhere else shows as answered here too, and stays as history.
  const decisionsQ = useQuery({
    queryKey: ['agent-decisions', agentId], queryFn: () => fetchDecisions(agentId),
    enabled: !fixtureItems, refetchInterval: 10_000, staleTime: 5_000,
  });
  // Recorded multi-part answers: an answered AskUserQuestion in the thread is drawn from these where they exist.
  const batchesQ = useQuery({
    queryKey: ['agent-menu-batches', agentId], queryFn: () => fetchBatchRecords(agentId),
    enabled: !fixtureItems, refetchInterval: 15_000, staleTime: 5_000,
  });
  // A machine line that answers a card drawn in this chat ([DECISION ANSWERED <id>], an approval, a
  // closed human task) is shown BY that card, its outcome row; it is not drawn a second time as its
  // own card. One whose card is not here (an earlier generation's) still shows as a card.
  const shownNodes = useMemo(
    () => foldCardAnswers(nodes as { kind: string; text?: string; machine?: string }[], new Set((decisionsQ.data ?? []).map((d) => d.id))) as typeof nodes,
    [nodes, decisionsQ.data]);
  const entries = useMemo(() => interleave(shownNodes, decisionsQ.data ?? [], { cutAbove: paged.hasOlder }),
    [shownNodes, decisionsQ.data, paged.hasOlder]);
  // "Needs you" sends you to this agent's first open decision; the bell, to a named one. A card that
  // is no longer here (answered and gone, or never drawn in this chat) says so instead of doing nothing.
  const [focusNotice, setFocusNotice] = useState<string | null>(null);
  useEffect(() => {
    if (!focusNotice) return;
    const t = window.setTimeout(() => setFocusNotice(null), 5000);
    return () => window.clearTimeout(t);
  }, [focusNotice]);
  useEffect(() => {
    const show = (req: FocusRequest) => {
      const target = focusTarget(decisionsQ.data ?? [], req.decisionId);
      const el = target ? document.getElementById(`decision-${target.id}`) : null;
      if (el) {
        stick.hold();
        el.scrollIntoView({ block: 'center', behavior: 'smooth' });
      } else if (req.decisionId) {
        setFocusNotice('That card is no longer waiting here — it may have been answered elsewhere.');
      }
    };
    const waiting = decisionsQ.data ? takeFocusRequest(agentId) : null;
    if (waiting) window.setTimeout(() => show(waiting), 50);
    const onFocusReq = (e: Event) => {
      const req = (e as CustomEvent<FocusRequest>).detail;
      if (req?.agentId === agentId && decisionsQ.data) { takeFocusRequest(agentId); show(req); }
    };
    window.addEventListener(FOCUS_DECISION_EVENT, onFocusReq);
    return () => window.removeEventListener(FOCUS_DECISION_EVENT, onFocusReq);
  }, [agentId, decisionsQ.data, stick.hold]); // eslint-disable-line react-hooks/exhaustive-deps
  const st = normalizeAgentState(state);
  // The thread as the kit's thread view draws it: the feed's nodes and cards, then the sends still waiting for their rows.
  // How you see this agent (lib/agentPrefs.ts): Bubbles, its own look, or Modern, Arturo's. A switch only re-draws the
  // thread; the session, the draft and pending sends are untouched. The compact card (a dark box) stays Bubbles.
  const chatStyle = useChatStyle(agentId);
  const modern = !compact && chatStyle === 'modern';
  const threadRows = useMemo(() => agentRows(entries, loading ? [] : pending, st, { modern }), [entries, pending, loading, st, modern]);
  // The idle backstop: a bubble no row could match (a long message arrives as a shorter banner)
  // goes once the agent is idle again. Only from a live feed: a stale "idle" proves nothing.
  const feedLive = !feed || !isDegraded(feed);
  // Re-checked every few seconds while a bubble shows, since a short turn can begin and end between
  // two status readings and the state then never changes here.
  const hasPending = pendingAll.length > 0;
  useEffect(() => {
    if (!feedLive || st === 'unknown') return;
    onAgentState(agentId, st === 'idle');
    if (!hasPending) return;
    const iv = window.setInterval(() => onAgentState(agentId, st === 'idle'), 5000);
    return () => window.clearInterval(iv);
  }, [agentId, st, feedLive, hasPending, onAgentState]);
  // No feed prop at all (the fixture harness) means "not fed from a live feed", which must not
  // become "the feed is dead" — it renders as before. Only an ACTUAL non-live verdict suppresses.
  // CONNECTING is not 'not live' — on first paint nothing has been contradicted yet.
  // UPDATING is not live either: the state is minutes old until the refresh lands, so no
  // "agent is working…" claim from it.
  const live = !feed || (!isDegraded(feed) && feed.health !== 'updating');

  // Another seat's transcript (the panel and the page keep this view mounted across seats) opens
  // at ITS newest message, not wherever the previous one was left.
  useLayoutEffect(() => { stick.toBottom(); }, [agentId, stick.toBottom]); // eslint-disable-line react-hooks/exhaustive-deps
  // Tapping into a composer, or the keyboard shrinking the column, brings the conversation to the
  // latest message (Shaw, 2026-10-11). The xterm's hidden textarea is the terminal, not a composer.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onFocus = (e: FocusEvent) => {
      const t = e.target as HTMLElement | null;
      if (!t || !/^(TEXTAREA|INPUT)$/.test(t.tagName) || t.classList.contains('xterm-helper-textarea') || el.contains(t)) return;
      stick.toBottom();
      window.setTimeout(stick.toBottom, 350);   // after the keyboard has finished opening
    };
    document.addEventListener('focusin', onFocus);
    return () => document.removeEventListener('focusin', onFocus);
  }, [stick.toBottom]); // eslint-disable-line react-hooks/exhaustive-deps
  // The thread view follows (or holds the reader) whenever its rows change, in a layout effect.
  const follow = useMemo(() => ({ follow: stick.follow }), [stick.follow]);

  return (
    <BatchRecordsContext.Provider value={batchesQ.data}>
    <div className={compact ? 'space-y-2' : 'flex flex-col flex-1 min-h-0'}>
      <div
        ref={scrollRef}
        onScroll={stick.onScroll}
        data-testid="transcript-scroll"
        className={
          compact
            // A step off the page in either theme (the tone scale: near-black in dark, near-white in light), so
            // the theme-token prose inside it reads in both.
            ? 'bg-tone-950 rounded-lg p-3 max-h-96 overflow-y-auto space-y-2.5'
            // CAPPED AND CENTRED (Shaw: prose ran ~1460px wide and the input ~1660px — unreadable,
            // and the right edges of tool cards and user bubbles were 130px apart because each
            // sized itself to a different container). ONE column, one pair of edges, shared with
            // the composer. The column below is anchored to the BOTTOM like every chat app by its
            // `mt-auto`, NOT by `justify-end` here: in a scrolling flex column `justify-end` makes
            // the overflow spill out of the TOP edge, where no scrollbar, wheel or touch can reach,
            // so a transcript taller than the pane could never be scrolled back (Shaw, 2026-10-11).
            // An auto margin only takes free space, so a short chat still sits on the composer and
            // a long one overflows downward and scrolls.
            : 'flex-1 overflow-y-auto min-h-0 overscroll-y-contain flex flex-col'
        }
      >
        {/* --composer-h: the agent page's FLOATING composer height, so the last message clears the
            pill. Unset everywhere else (the Agents-page panel, the harness): 0, i.e. py-4 as before. */}
        <ChatAgentContext.Provider value={agentId}>
        {/* A screen and a half above the first row, the next older page is read (useNearTop). No height of its own,
            and outside the rows, so neither it nor its "loading" line ever moves what the reader is looking at. */}
        {!compact && <div ref={sentinelRef} data-anchor-skip data-older-sentinel aria-hidden className="h-0 shrink-0" />}
        {!compact && <OlderPagesNote show={paged.loading} />}
        <div ref={columnRef}
          className={compact ? undefined : ["mt-auto w-full max-w-[860px] mx-auto px-4 pt-4 space-y-2.5", modern ? 'kit-modern' : ''].join(' ').trim()}
          style={compact ? undefined : { paddingBottom: 'calc(var(--composer-h, 0px) + 1rem)' }}
        >
        {loading ? (
          <div className="flex items-center gap-2 px-1 py-2">
            <div className="w-2 h-2 bg-tone-500 rounded-full animate-pulse" />
            <span className="text-xs text-tone-500">Loading conversation…</span>
          </div>
        ) : entries.length === 0 ? (
          <span className="text-xs text-tone-600">{err ? `No transcript (${err})` : 'No messages yet'}</span>
        ) : null}
        {/* The thread through the kit's thread view (components/kit/ThreadView.tsx), in the agent chat's own skin. */}
        {!loading && (entries.length > 0 || pending.length > 0 || liveText) && (
          <ThreadView rows={threadRows} skin={modern ? 'modern' : 'bubbles'} times={modern} stick={follow} liveText={liveText}
            renderNode={(node, interrupted) => <GroupedNodeView node={node} interrupted={interrupted} />}
            onDecisionAnswered={() => void decisionsQ.refetch()} />
        )}
        {focusNotice && (
          <div role="status" className="sticky bottom-2 mx-auto w-fit max-w-[92%] text-[11px] text-tone-300 px-3 py-1.5 rounded-full border border-tone-700 bg-tone-900/95 shadow">
            {focusNotice}
          </div>
        )}

        {/* live decision menu (detector pending_menu) — answerable below the last
            message; menus render no transcript line so this IS how chat surfaces
            a decision-waiting agent (previously invisible/unanswerable in chat). */}
        {/* A menu from a DEAD feed is the page's version of the P1's blue chip: it offers an
            answer to a question that may no longer be on screen, and the answer cannot be
            delivered anyway. Staleness outranks a pending menu here exactly as it does in
            chipStateFor. */}
        {live && pendingMenu && pendingMenu.options?.length > 0 && (
          <AgentSide><OptionsMenuCard agentId={agentId} menu={pendingMenu} /></AgentSide>
        )}

        {/* In-flight indicator when the agent is mid-turn (transcript not flushed yet).
            THIS IS THE "FLASHING YELLOW" OF THE P1 — it pulsed amber for an hour off a cached
            'working' while the API was down. It is a claim about RIGHT NOW, so it requires a
            live feed, not just a remembered state. */}
        {live && st === 'working' && !hideStatusBar && (
          <div className="flex items-center gap-2 px-1 py-1">
            <div className="w-1.5 h-1.5 bg-amber-400 rounded-full animate-pulse" />
            <span className="text-[11px] text-tone-500 italic">agent is working…</span>
          </div>
        )}
        </div>
        </ChatAgentContext.Provider>
      </div>

      {/* Status bar. The AGENT PAGE now carries a LiveStatusLine above its composer, so this
          bar is the compact/embedded surface's version of it (AgentCard). Two renderings of one
          fact is how the sidebar and the page came to disagree; they share lib/feedLiveness so
          they cannot say different things, and the page does not show both. */}
      {!hideStatusBar && (
      <div className="flex items-center gap-3 px-3 py-1.5 border-t border-tone-800/50">
        <StatePill state={st} feed={feed} />
        {strandedText && (
          <span className="text-[10px] text-yellow-500/90 truncate">
            ✎ unsent: "{strandedText.slice(0, 60)}"
          </span>
        )}
      </div>
      )}
    </div>
    </BatchRecordsContext.Provider>
  );
}
