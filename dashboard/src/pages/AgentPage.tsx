import { useState, useEffect, useRef, useCallback } from 'react';
import { agentRowsFrom } from '../lib/api';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { TopBar } from '../components/agent/TopBar';
import { Feed } from '../components/agent/Feed';
import { Composer } from '../components/agent/Composer';
import { AgentWorkingRow } from '../components/agent/AgentWorkingRow';
import { useAgentJobs, usePollAgentJobs } from '../stores/agentJobs';
import CallBar from '../components/call/CallBar';
import { stepFrom } from '../lib/liveStep';
import { useAgentSteps } from '../stores/agentSteps';
import { useFeedHealth } from '../hooks/useFeedHealth';
import { useAgentSettings } from '../stores/agentSettings';
import { reviveAgent } from '../lib/api';
import { composerGate, canStopTurn } from '../lib/composerGate';
import { useAgents } from '../hooks/useAgents';
import WebTerminal from '../components/WebTerminal';
import ActionBar from '../components/ActionBar';
import { injectToAgent, sendKeyToAgent } from '../lib/api';
import { logAction } from '../lib/user-actions';
import { OPEN_SIDEBAR_EVENT } from '../lib/headerSlot';
import DeliverNowBanner from '../components/agent/DeliverNowBanner';
import { ArturoPanel } from '../components/arturo/ArturoPanel';
import { useChatStyle, loadPrefs, chooseStyle, styleBeforeDev } from '../lib/agentPrefs';
import { asChip, chipNeedsYou } from '../lib/agentChip';
import { useHeadlessDev } from '../hooks/useHeadlessDev';
import HeadlessDevBanner from '../components/agent/HeadlessDevBanner';
import { wantsAgentPanel } from '../lib/agentsPage';

export default function AgentPage() {
  const { id } = useParams<{ id?: string }>();
  const agentId = id || 'gm';
  const [resuming, setResuming] = useState(false);
  // Chat (transcript + composer) or Dev (the seat's live terminal): the chat style you chose for THIS agent in its
  // panel, saved for you on the server (lib/agentPrefs.ts). Switching never loses the draft (stores/composerDrafts).
  const chosenMode: 'chat' | 'dev' = useChatStyle(agentId) === 'dev' ? 'dev' : 'chat';
  useEffect(() => { void loadPrefs(agentId); }, [agentId]);
  const [panelOpen, setPanelOpen] = useState(false);
  // Settings › Agents links an agent to its own panel: its page, with the panel open (lib/agentsPage.ts). Once per
  // navigation, so closing the panel keeps it closed.
  const location = useLocation();
  const [panelFor, setPanelFor] = useState<string | null>(null);
  if (wantsAgentPanel(location.state) && panelFor !== location.key) { setPanelFor(location.key); setPanelOpen(true); }
  // …then clear that state (same page, replaced), so Back or a reload doesn't open the panel again.
  const navigate = useNavigate();
  useEffect(() => {
    if (wantsAgentPanel(location.state)) navigate({ pathname: location.pathname, search: location.search, hash: location.hash }, { replace: true, state: null });
  }, [location.state, location.pathname, location.search, location.hash, navigate]);
  const gearRef = useRef<HTMLButtonElement>(null);
  // Bring back a seat that fell over: its last session where it left off when that session can still be
  // resumed, else a fresh start as its next generation (with a briefing on what it last did). Answers once it is
  // running. Undefined while in flight, so the button disappears rather than queueing a second start on a
  // double click.
  const resume = resuming ? undefined : () => {
    setResuming(true);
    void reviveAgent(agentId).finally(() => setResuming(false));
  };
  const settings = useAgentSettings();
  // A seat's own page names the seat (Shaw's QA run: the composer said "Ask Arturo" on demo-planner's page).
  const { data } = useAgents();
  // /api/agents answers { agents: [...] }; tolerate a bare array too.
  const rows = agentRowsFrom(data) as Array<{ id: string; generation?: unknown; tmux_session?: string; machine?: string; full_name?: string }> | undefined;
  const seatRow = id && Array.isArray(rows) ? rows.find((a) => a.id === id) : undefined;
  const feed = useFeedHealth();
  // What it is doing now, from its open chat stream (the list's `step` until that has spoken).
  const streamStep = useAgentSteps((s) => (id ? s.byAgent[id] : undefined));
  // Its background jobs, polled while this page is open: the thread's launching tool cards and the slideout's History
  // (Running) read them.
  usePollAgentJobs(agentId);
  const runningBg = useAgentJobs(agentId).filter((j) => j.status === 'running');
  // The header shows the agent's full name ("Demo · dev"): outside the sidebar there's no project header around it.
  const seat = id ? { id, generation: seatRow?.generation, name: seatRow?.full_name } : undefined;

  // Stop = Esc into the seat's pane, the exact key the Dev-mode ActionBar's Esc sends. Offered
  // only mid-turn with no menu open (canStopTurn): Esc at a prompt is an ANSWER (#334). Reads the
  // seat row's raw `status` and `pending_menu`.
  const seatStatus = (seatRow as { status?: string } | undefined)?.status;
  const seatMenu = (seatRow as { pending_menu?: unknown } | undefined)?.pending_menu;
  const canStop = canStopTurn({ state: seatStatus, pendingMenu: seatMenu });
  // A headless seat's Dev switches its terminal on and off: the terminal shows once it is open (a tmux seat's view
  // is the chosen style, unchanged).
  // Back to chat: the button, or the terminal left from inside it while it is shown (the same result either way).
  const backToChat = useCallback(() => {
    void chooseStyle(agentId, styleBeforeDev(agentId));
    logAction('agent.back-to-chat', agentId);
  }, [agentId]);
  const dev = useHeadlessDev(agentId, chosenMode, seatStatus, seat?.name || agentId, backToChat);
  const mode = dev.view;
  const stopTurn = () => {
    logAction('agent.stop', agentId);
    return sendKeyToAgent(agentId, 'escape');
  };

  // THE FLOATING COMPOSER'S HEIGHT, measured, published as --composer-h on the chat column: the
  // transcript pads its bottom by it so the last message is never under the pill. (It used to add
  // the floating Arturo pill and publish a lift for it; Arturo now lives in the top bar.)
  // The working row floats on top of the dock (out of its flow, so the dock never changes size when the row comes or
  // goes); its height is added here, so the thread's bottom space takes the row's place in the same frame.
  const chatRef = useRef<HTMLDivElement>(null);
  const dockRef = useRef<HTMLDivElement>(null);
  const rowSlotRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const dock = dockRef.current;
    const col = chatRef.current;
    if (!dock || !col) return;
    const apply = () => {
      const h = Math.ceil(dock.getBoundingClientRect().height + (rowSlotRef.current?.getBoundingClientRect().height ?? 0));
      const scroller = col.querySelector<HTMLElement>('[data-testid="transcript-scroll"]');
      // keep a reader who was at the bottom AT the bottom when the pill grows
      const pinned = !!scroller && scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 120;
      col.style.setProperty('--composer-h', `${h}px`);
      if (scroller && pinned) scroller.scrollTop = scroller.scrollHeight;
    };
    apply();
    const ro = new ResizeObserver(apply);
    ro.observe(dock);
    if (rowSlotRef.current) ro.observe(rowSlotRef.current);
    return () => ro.disconnect();
  }, [mode]);

  // Load settings from storage on mount
  useEffect(() => {
    settings.loadFromStorage();
  }, []);

  return (
    <div className="flex flex-col h-full min-h-0 bg-background text-foreground">
      {/* Top bar: menu, seat, bell, gear (the agent's panel: name, avatar, voice, model, chat style ...) */}
      <TopBar
        onMenuOpen={() => window.dispatchEvent(new Event(OPEN_SIDEBAR_EVENT))}
        seat={seat}
        mode={chosenMode}
        onBackToChat={backToChat}
        onSettings={() => { setPanelOpen((o) => !o); logAction('agent.panel', agentId); }}
        settingsOpen={panelOpen}
        settingsRef={gearRef}
      />
      {id && (
        <ArturoPanel open={panelOpen} onClose={() => setPanelOpen(false)} returnFocusTo={gearRef}
          agent={{
            id: agentId,
            name: (seatRow as { display_name?: string } | undefined)?.display_name || agentId,
            provider: (seatRow as { runtime?: string; provider?: string } | undefined)?.runtime
              || (seatRow as { provider?: string } | undefined)?.provider,
            // What runs beside its turn: its subagents, or its running background jobs when the jobs route lists more
            // (they include its subagents), so the History row reads "2 running" while two jobs run.
            subagents: Math.max((seatRow as { subagents?: number } | undefined)?.subagents ?? 0, runningBg.length) || undefined,
            contextLeftPct: asChip((seatRow as { chip?: unknown } | undefined)?.chip)?.context_left_pct ?? null,
            waiting: chipNeedsYou(asChip((seatRow as { chip?: unknown } | undefined)?.chip)) || !!seatMenu,
          }} />
      )}


      {mode === 'dev' ? (
        <>
          {/* Dev: the seat's own tmux pane, the same terminal the Agents page's panel opens. Dev mode keeps the
              browser's own keyboard behaviour (data-keyboard-native: DashboardLayout leaves it alone). */}
          {/* A call with this agent while it is in Dev: the bar stays, with Back to chat (never done for you). */}
          <div className="relative shrink-0 h-0 z-20">
            <CallBar agentId={agentId} name={seatRow?.full_name || agentId} onSettings={() => setPanelOpen(true)} onBackToChat={backToChat} />
          </div>
          <div data-keyboard-native className="flex-1 min-h-0 bg-black overflow-hidden">
            <WebTerminal
              key={agentId}
              session={seatRow?.tmux_session || agentId.replace('unregistered:', '')}
              machine={seatRow?.machine || 'vps'}
              closing={chosenMode !== 'dev'}
            />
          </div>
          {/* special keys (arrows, Esc, Tab, ...): a phone has no keyboard for them */}
          <div data-arturo-avoid data-keyboard-native className="px-3 py-2 border-t border-border shrink-0 bg-tone-950">
            <ActionBar
              agentId={agentId}
              outputLines={[]}
              onInject={(text) => injectToAgent(agentId, text)}
              onSendKey={(key) => sendKeyToAgent(agentId, key)}
              injectMode
              devMode
            />
          </div>
        </>
      ) : (
        <div ref={chatRef} className="relative flex-1 min-h-0 flex flex-col overflow-hidden">
        <HeadlessDevBanner dev={dev} />
        {/* A live call with this agent: its bar floats at the top of the chat, and the thread scrolls under it. */}
        <CallBar agentId={agentId} name={seatRow?.full_name || agentId} onSettings={() => setPanelOpen(true)}
                 onBackToChat={chosenMode === 'dev' ? backToChat : undefined} />
        {/* Main content. The agent rail lives in the Sidebar now — ONE nav column, not two.
            The transcript fills the column and scrolls UNDER the floating composer below. */}
        <div className="flex-1 overflow-hidden flex flex-col min-h-0">
          <Feed agentId={agentId} />
        </div>

        {/* THE DOCK floats over the bottom of the transcript: the live status line, then the
            pill, on a solid ground with a short fade above it, so the text that scrolls behind never shows through.
            pointer-events pass through the ground to the transcript; the controls take them back. */}
        <div ref={dockRef} data-testid="composer-dock" className="absolute inset-x-0 bottom-0 z-10 pointer-events-none">
          {/* Solid in the page's own colour behind the working row, the hint line and the composer, so the thread that
              scrolls under them is never readable through; only a short fade above its top edge. */}
          <div aria-hidden data-dock-fade className="absolute inset-x-0 -top-3 h-3 bg-gradient-to-b from-transparent to-background" />
          <div aria-hidden data-dock-solid className="absolute inset-0 bg-background" />
          <div className="relative pointer-events-auto">
            {/* WHO AND WHAT, directly above the composer, in the shape of Arturo's working row: the agent's
                animated avatar, its name, and what it is doing now (lib/agentWorkingRow.ts). Every truth the old
                status line had stays (disconnected, updating, the live step, queued, stopped with Resume ...), and
                it reads the same state as the sidebar dot. Background jobs are not here: they show live on their
                launching tool cards in the thread, and in the slideout's History, under Running. */}
            {/* On top of the dock, out of its flow: no height when there is no row, its own ground when there is. */}
            <div ref={rowSlotRef} className="absolute inset-x-0 bottom-full" data-testid="agent-working-slot">
            <div className="w-full max-w-[860px] mx-auto px-3" data-testid="agent-working">
              <AgentWorkingRow
                agentId={agentId}
                name={(seatRow as { display_name?: string } | undefined)?.display_name || seatRow?.full_name || agentId}
                onResume={resume}
                status={seatStatus}
                chip={asChip((seatRow as { chip?: unknown } | undefined)?.chip)}
                pendingMenu={!!(seatRow as { has_pending_menu?: boolean } | undefined)?.has_pending_menu}
                feed={feed}
                stateAgeS={(seatRow as { state_age_s?: number } | undefined)?.state_age_s}
                tool={(seatRow as { tool?: string } | undefined)?.tool}
                step={streamStep ?? stepFrom((seatRow as { step?: unknown } | undefined)?.step)}
              />
            </div>
            </div>

            <DeliverNowBanner agentId={agentId} />
            <div data-arturo-avoid>
            <Composer
              agentId={agentId}
              seatName={seatRow?.full_name || id}
              gate={composerGate({
                state: seatStatus,
                pendingMenu: seatMenu,
                subagents: (seatRow as { subagents?: number } | undefined)?.subagents,
              })}
              subagents={(seatRow as { subagents?: number } | undefined)?.subagents}
              canStop={canStop}
              onStop={stopTurn}
            />
            </div>
          </div>
        </div>
        </div>
      )}
    </div>
  );
}
