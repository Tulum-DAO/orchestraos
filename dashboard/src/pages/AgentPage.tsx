import { useState, useEffect, useRef } from 'react';
import { agentRowsFrom } from '../lib/api';
import { useParams } from 'react-router-dom';
import { TopBar } from '../components/agent/TopBar';
import { Feed } from '../components/agent/Feed';
import { Composer } from '../components/agent/Composer';
import { LiveStatusLine } from '../components/chat/LiveStatusLine';
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
import { useChatStyle, loadPrefs } from '../lib/agentPrefs';

export default function AgentPage() {
  const { id } = useParams<{ id?: string }>();
  const agentId = id || 'gm';
  const [resuming, setResuming] = useState(false);
  // Chat (transcript + composer) or Dev (the seat's live terminal): the chat style you chose for THIS agent in its
  // panel, saved for you on the server (lib/agentPrefs.ts). Switching never loses the draft (stores/composerDrafts).
  const mode: 'chat' | 'dev' = useChatStyle(agentId) === 'dev' ? 'dev' : 'chat';
  useEffect(() => { void loadPrefs(agentId); }, [agentId]);
  const [panelOpen, setPanelOpen] = useState(false);
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
  // The header shows the agent's full name ("Demo · dev"): outside the sidebar there's no project header around it.
  const seat = id ? { id, generation: seatRow?.generation, name: seatRow?.full_name } : undefined;

  // Stop = Esc into the seat's pane, the exact key the Dev-mode ActionBar's Esc sends. Offered
  // only mid-turn with no menu open (canStopTurn): Esc at a prompt is an ANSWER (#334). Reads the
  // seat row's raw `status` and `pending_menu`.
  const seatStatus = (seatRow as { status?: string } | undefined)?.status;
  const seatMenu = (seatRow as { pending_menu?: unknown } | undefined)?.pending_menu;
  const canStop = canStopTurn({ state: seatStatus, pendingMenu: seatMenu });
  const stopTurn = () => {
    logAction('agent.stop', agentId);
    return sendKeyToAgent(agentId, 'escape');
  };

  // THE FLOATING COMPOSER'S HEIGHT, measured, published as --composer-h on the chat column: the
  // transcript pads its bottom by it so the last message is never under the pill. (It used to add
  // the floating Arturo pill and publish a lift for it; Arturo now lives in the top bar.)
  const chatRef = useRef<HTMLDivElement>(null);
  const dockRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const dock = dockRef.current;
    const col = chatRef.current;
    if (!dock || !col) return;
    const apply = () => {
      const h = Math.ceil(dock.getBoundingClientRect().height);
      const scroller = col.querySelector<HTMLElement>('[data-testid="transcript-scroll"]');
      // keep a reader who was at the bottom AT the bottom when the pill grows
      const pinned = !!scroller && scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 120;
      col.style.setProperty('--composer-h', `${h}px`);
      if (scroller && pinned) scroller.scrollTop = scroller.scrollHeight;
    };
    apply();
    const ro = new ResizeObserver(apply);
    ro.observe(dock);
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
        mode={mode}
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
            subagents: (seatRow as { subagents?: number } | undefined)?.subagents,
          }} />
      )}


      {mode === 'dev' ? (
        <>
          {/* Dev: the seat's own tmux pane, the same terminal the Agents page's panel opens. Dev mode keeps the
              browser's own keyboard behaviour (data-keyboard-native: DashboardLayout leaves it alone). */}
          <div data-keyboard-native className="flex-1 min-h-0 bg-black overflow-hidden">
            <WebTerminal
              key={agentId}
              session={seatRow?.tmux_session || agentId.replace('unregistered:', '')}
              machine={seatRow?.machine || 'vps'}
            />
          </div>
          {/* special keys (arrows, Esc, Tab, ...): a phone has no keyboard for them */}
          <div data-arturo-avoid data-keyboard-native className="px-3 py-2 border-t border-border shrink-0 bg-neutral-950">
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
        {/* Main content. The agent rail lives in the Sidebar now — ONE nav column, not two.
            The transcript fills the column and scrolls UNDER the floating composer below. */}
        <div className="flex-1 overflow-hidden flex flex-col min-h-0">
          <Feed agentId={agentId} />
        </div>

        {/* THE DOCK floats over the bottom of the transcript: the live status line, then the
            pill. A fade under it keeps the text that scrolls behind from fighting the controls.
            pointer-events pass through the fade to the transcript; the controls take them back. */}
        <div ref={dockRef} data-testid="composer-dock" className="absolute inset-x-0 bottom-0 z-10 pointer-events-none">
          <div aria-hidden className="absolute inset-x-0 bottom-0 top-6 bg-gradient-to-t from-background via-background/90 to-transparent" />
          <div className="relative pointer-events-auto">
            {/* THE LIVE STATUS LINE, directly above the composer (Shaw's top charge: nothing on
                the page said what the agent was doing right now, so the sidebar and the page could
                contradict each other). Same row, same staleness verdict as the rail dot. */}
            {/* The Resume branch in LiveStatusLine had NO caller anywhere, so a stopped agent was
                told it would not see the message and offered nothing to do about it. */}
            <LiveStatusLine
              onResume={resume}
              state={seatStatus}
              feed={feed}
              stateAgeS={(seatRow as { state_age_s?: number } | undefined)?.state_age_s}
              tool={(seatRow as { tool?: string } | undefined)?.tool}
            />

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
